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


def create_app(settings: Settings | None = None) -> FastAPI:
    """Wire Settings -> PriceMap -> CostTracker -> BudgetEnforcer ->
    FallbackManager -> GatewayProxy and return the app with the gateway routes
    mounted (POST /v1/chat/completions, /v1/completions, /v1/embeddings,
    GET /v1/models, GET /health).
    """
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
            # Provider priority for duplicate models: first provider wins.
            # Mirrors the Hermes provider mapping — opencode-go serves the
            # deepseek-* family, opencode-zen the mimo-* family. deepinfra,
            # xiaomi, google and openrouter follow as generic catalogs.
            priority = ["opencode-go", "opencode-zen", "deepinfra", "xiaomi", "google", "openrouter"]
            connections = sorted(store.list(), key=lambda c: priority.index(c["slug"]) if c["slug"] in priority else 99)
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
                    "models": models,
                }
            if registry:
                direct = DirectProviderClient(registry, timeout=settings.provider_timeout)
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
        # Aligned with the M1 decision (_model_known): the gateway accepts
        # gateway-configured models (overrides + fallback chains) AND any
        # litellm-known model (forwarded via litellm anyway), so the listing
        # mirrors exactly what the request path will serve.
        models: set[str] = set(settings.pricing_overrides)
        for cfg in fallback_configs:
            models.add(cfg.model)
            models.update(cfg.chain)
        try:
            import litellm

            models.update(litellm.model_cost)
        except Exception:  # pragma: no cover - litellm is a hard dep
            pass
        return JSONResponse(
            {
                "object": "list",
                "data": [{"id": m, "object": "model"} for m in sorted(models)],
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
