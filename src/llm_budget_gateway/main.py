"""FastAPI app factory for the LLM budget gateway.

Placeholder stub for the TDD RED phase — create_app raises
NotImplementedError until implemented (P0-1). Interface is normative per
analysis brief §4 P0-1.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

logger = logging.getLogger(__name__)

from .budget_enforcement import (
    BudgetEnforcer,
    InMemoryCounterStore,
    load_budget_configs,
)
from .config import Settings
from .cost_estimation import CostEstimator
from .cost_tracking import CostCalculator, CostStore, CostTracker, ModelPrice, PriceMap
from .gateway_home import install_gateway_home
from .gateway_proxy import GatewayProxy, ProviderResponse
from .model_fallback import FallbackConfig, FallbackManager
from .routing_control_plane import RoutingControlPlane


def _setup_gateway_logging() -> None:
    """Make gateway INFO logs visible in the service-managed log file.

    uvicorn's LOGGING_CONFIG only configures its own loggers: the root logger
    keeps Python's default WARNING level AND no handler, so every gateway INFO
    log (request routing, served decisions, fallbacks) would be silently
    dropped. Attach a stderr handler to the ``llm_budget_gateway`` logger —
    the service manager redirects that stderr to
    ``.gateway-console/logs/gateway.log`` — and stop propagation to avoid
    duplicate lines via the root's WARNING+ last-resort handler.
    """
    gw = logging.getLogger("llm_budget_gateway")
    if gw.handlers:
        return
    gw.setLevel(logging.INFO)
    gw.propagate = False
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    gw.addHandler(handler)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Wire Settings -> PriceMap -> CostTracker -> BudgetEnforcer ->
    FallbackManager -> GatewayProxy and return the app with the gateway routes
    mounted (POST /v1/chat/completions, /v1/completions, /v1/embeddings,
    GET /v1/models, GET /health).
    """
    _setup_gateway_logging()
    settings = settings or Settings()

    price_map = PriceMap(
        overrides={
            model: ModelPrice(**raw)
            for model, raw in settings.pricing_overrides.items()
        }
    )
    calculator = CostCalculator(price_map)
    store = CostStore(_sqlite_path(settings.database_url))
    tracker = CostTracker(store=store, calculator=calculator)

    try:
        budget_configs = load_budget_configs(settings.budget_config_path)
    except FileNotFoundError:
        budget_configs = []
    enforcer = BudgetEnforcer(
        configs=budget_configs,
        cost_tracker=tracker,
        counter_store=InMemoryCounterStore(),
    )

    fallback_configs = [FallbackConfig(**cfg) for cfg in settings.fallback_configs]
    manager = FallbackManager(
        configs=fallback_configs,
        counter_store=InMemoryCounterStore(),
    )

    estimator = CostEstimator(calculator, manager)
    proxy = GatewayProxy(
        settings=settings,
        cost_tracker=tracker,
        budget_enforcer=enforcer,
        fallback_manager=manager,
    )

    # Attach the logical routing plane when a persisted routing DB exists
    # (created by the cockpit-first launcher under .gateway-console/). This
    # lets application keys (gw_...) resolve published logical LLM routes;
    # without it the proxy only serves Settings.virtual_keys.
    data_dir = Path(__file__).resolve().parents[2] / ".gateway-console"
    routing_db = data_dir / "routing.db"
    if routing_db.exists():
        try:
            plane = RoutingControlPlane(
                sqlite3.connect(str(routing_db), check_same_thread=False)
            )
            proxy.attach_routing_control_plane(plane)
        except sqlite3.Error:
            logger.exception("failed to attach routing control plane")

    # Attach the UI-managed route store (cockpit Routes tab, pc_routes/targets
    # model). Routes created and published there are served by this proxy and
    # stay editable by the user in the UI — no code or restart needed after
    # the user edits a route.
    product_db = data_dir / "product.db"
    if product_db.exists():
        try:
            from .product_console import ProductConsoleStore

            product = ProductConsoleStore(
                sqlite3.connect(str(product_db), check_same_thread=False)
            )
            proxy.attach_product_console(product)
            logger.info("attached UI-managed product routes")
        except Exception:
            logger.exception("failed to attach product route store")

    # Attach the integrated intelligence helpers (formerly the separate
    # Intelligence satellite service): exact-response cache, PII redaction
    # and cost-aware routing. All are wired into the proxy request path and
    # opt-in per request (X-Gateway-Cache: 1, X-Gateway-Redact-Pii: 1,
    # metadata.cost_aware: true).
    try:
        from .market_features import ExactResponseCache, PIIRedactor, UsageAnomalyDetector
        from .market_features import CostAwareRouter as MarketCostAwareRouter

        proxy.attach_intelligence(
            cache=ExactResponseCache(str(data_dir / "intelligence.db")),
            redactor=PIIRedactor(),
            cost_router=MarketCostAwareRouter(),
        )
        logger.info("attached integrated intelligence helpers")
    except Exception:
        logger.exception("failed to attach intelligence helpers")

    # Attach the direct provider transport built from the persisted provider
    # connections (providers.db + vault key). This replaces litellm for all
    # gateway-configured providers (flat model names litellm cannot resolve).
    providers_db = data_dir / "providers.db"
    master_key = data_dir / "provider-master.key"
    if providers_db.exists() and master_key.exists():
        try:
            from .provider_connections import CredentialVault, ProviderConnectionStore
            from .provider_direct import DirectProviderClient

            store = ProviderConnectionStore(
                sqlite3.connect(str(providers_db), check_same_thread=False),
                CredentialVault(master_key),
            )
            # Provider registry order is the persisted connection list
            # (as shown on the Providers tab); duplicate flat model names
            # resolve first-wins inside DirectProviderClient, and routes pin
            # providers explicitly with @slug/model aliases — no hardcoded
            # priority list here.
            connections = list(store.list())
            registry: dict[str, dict] = {}
            for connection in connections:
                slug = str(connection["slug"])
                secret = store.connection_secret(str(connection["id"]))
                models = [str(m["id"]) for m in store.models(str(connection["id"]))]
                base_url = str(secret.get("base_url", "")).rstrip("/")
                if not base_url or not models:
                    continue
                registry[slug] = {
                    "base_url": base_url,
                    "api_key_env": f"__vault_{slug}__",  # unused: key passed directly
                    "api_key": str(secret.get("api_key", "")),
                    "user_agent": str(secret.get("user_agent", "")).strip() or None,
                    "models": models,
                }
                extra_body_raw = str(secret.get("extra_body_json", "") or "").strip()
                if extra_body_raw:
                    try:
                        extra_body = json.loads(extra_body_raw)
                        if isinstance(extra_body, dict) and extra_body:
                            registry[slug]["extra_body"] = extra_body
                    except json.JSONDecodeError:
                        logger.warning(
                            "provider=%s invalid extra_body_json, ignored", slug
                        )
            if registry:
                direct = DirectProviderClient(
                    registry,
                    timeout=settings.provider_timeout,
                    signature_db_path=_sqlite_path(settings.database_url),
                )
                proxy.attach_direct_client(direct)
                logger.info("attached direct provider transport: %s", sorted(registry))
        except Exception:
            logger.exception("failed to attach direct provider transport")

    app = FastAPI(title="LLM Budget Gateway", version="0.1.0")

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        body = await _read_json_body(request)
        if isinstance(body, ProviderResponse):
            return _provider_response(body)
        response = await proxy.handle_chat_completion(
            body, _bearer_token(request), dict(request.headers)
        )
        return _provider_response(response)

    @app.post("/v1/completions")
    async def completions(request: Request) -> Response:
        body = await _read_json_body(request)
        if isinstance(body, ProviderResponse):
            return _provider_response(body)
        response = await proxy.handle_completion(
            body, _bearer_token(request), dict(request.headers)
        )
        return _provider_response(response)

    @app.post("/v1/embeddings")
    async def embeddings(request: Request) -> Response:
        body = await _read_json_body(request)
        if isinstance(body, ProviderResponse):
            return _provider_response(body)
        response = await proxy.handle_embeddings(
            body, _bearer_token(request), dict(request.headers)
        )
        return _provider_response(response)

    @app.post("/v1/cost-estimates")
    async def cost_estimates(request: Request) -> Response:
        body = await _read_json_body(request)
        if isinstance(body, ProviderResponse):
            return _provider_response(body)
        try:
            proxy.resolve_scopes(_bearer_token(request), dict(request.headers))
        except Exception:
            return _provider_response(
                GatewayProxy._error_response(
                    401, "invalid or missing api key", str(body.get("model", ""))
                )
            )
        if not proxy._model_known(str(body.get("model", ""))):
            return _provider_response(
                GatewayProxy._error_response(
                    404,
                    f"unknown model: {body.get('model', '')}",
                    str(body.get("model", "")),
                )
            )
        try:
            return JSONResponse(estimator.estimate(body).as_dict())
        except ValueError as exc:
            return _provider_response(
                GatewayProxy._error_response(400, str(exc), str(body.get("model", "")))
            )

    @app.get("/v1/models")
    async def list_models() -> JSONResponse:
        # Only UI-managed routes that the proxy actually serves are exposed
        # as first-class model names ("hermes-default" / "hermes-planner") —
        # one route per model. The proxy resolves routes by their latest
        # draft even before publish (Save draft makes the change live), so
        # draft routes are listed too — otherwise clients like Hermes lose
        # the route's context_length the moment an editor saves a draft and
        # fall back to stale defaults. Only archived routes are excluded.
        # Target provider models are intentionally NOT listed: they are
        # internal fallback details; the route decides which target serves a
        # request. The full litellm catalog and the legacy fallback configs
        # are excluded for the same reason, so the interactive model picker
        # in clients like Hermes shows exactly the handful of route names a
        # user can actually select. The request path is unchanged
        # (_model_known still accepts litellm-known models — only the
        # listing is narrowed).
        # Include context_length: the minimum across all targets of the route
        # (since any target may serve the request via fallback).
        models: list[dict] = []
        product = getattr(proxy, "_product_console", None)
        if product is not None:
            try:
                for route in product.routes():
                    if route.get("status") != "archived":
                        route_name = str(route.get("name", ""))
                        # compute min context_length across targets
                        ctx_lengths = [
                            t.get("context_length")
                            for t in route.get("targets", [])
                            if t.get("context_length") is not None
                        ]
                        min_ctx = min(ctx_lengths) if ctx_lengths else None
                        model_entry = {"id": route_name, "object": "model"}
                        if min_ctx is not None:
                            model_entry["context_length"] = min_ctx
                        models.append(model_entry)
            except Exception:
                logger.exception("failed to list UI-managed routes as models")
        return JSONResponse(
            {
                "object": "list",
                "data": sorted(models, key=lambda x: x["id"]),
            }
        )

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return install_gateway_home(app)
async def _read_json_body(request: Request) -> dict | ProviderResponse:
    """Parse the request body as a JSON object; 400 on malformed input.

    A malformed JSON body must surface as a 400 invalid_request_error —
    not an unhandled ``json.JSONDecodeError`` (500) — and a non-object
    body (``[1,2]``, ``"str"``) must be rejected here instead of falling
    through to ``body.get("model", "")`` (a confusing 404 ``unknown model:
    ``). Reuses ``GatewayProxy._error_response`` so the error shape stays
    identical to every other gateway error.
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return GatewayProxy._error_response(400, "request body is not valid JSON", "")
    if not isinstance(body, dict):
        return GatewayProxy._error_response(
            400, "request body must be a JSON object", ""
        )
    return body


def _bearer_token(request: Request) -> str:
    """Extract the bare API key from an ``Authorization`` header."""
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return authorization.strip()


def _sqlite_path(database_url: str) -> str:
    """Strip the ``sqlite:///`` prefix to a filesystem path."""
    prefix = "sqlite:///"
    if database_url.startswith(prefix):
        return database_url[len(prefix) :]
    return database_url


def _provider_response(response: ProviderResponse) -> Response:
    """Convert a ProviderResponse into a FastAPI Response.

    Dict bodies become JSON; stream=true bodies are already serialized SSE
    lines (``data: <json>`` + ``data: [DONE]``) and are streamed as
    ``text/event-stream``.
    """
    headers = dict(response.headers or {})
    if isinstance(response.body, dict):
        return JSONResponse(
            content=response.body, status_code=response.status_code, headers=headers
        )
    if isinstance(response.body, list):
        return StreamingResponse(
            content=response.body,
            status_code=response.status_code,
            headers=headers,
            media_type="text/event-stream",
        )
    if isinstance(response.body, str):
        return Response(
            content=response.body,
            status_code=response.status_code,
            headers=headers,
            media_type="text/event-stream",
        )
    return StreamingResponse(
        content=response.body,
        status_code=response.status_code,
        headers=headers,
        media_type="text/event-stream",
    )
