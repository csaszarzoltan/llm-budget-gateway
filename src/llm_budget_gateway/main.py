"""FastAPI app factory for the LLM budget gateway.

Placeholder stub for the TDD RED phase — create_app raises
NotImplementedError until implemented (P0-1). Interface is normative per
analysis brief §4 P0-1.
"""

from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .budget_enforcement import (
    BudgetEnforcer,
    InMemoryCounterStore,
    load_budget_configs,
)
from .config import Settings
from .cost_tracking import CostCalculator, CostStore, CostTracker, ModelPrice, PriceMap
from .gateway_proxy import GatewayProxy, ProviderResponse
from .model_fallback import FallbackConfig, FallbackManager


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

    proxy = GatewayProxy(
        settings=settings,
        cost_tracker=tracker,
        budget_enforcer=enforcer,
        fallback_manager=manager,
    )

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

    return app


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
        return GatewayProxy._error_response(
            400, "request body is not valid JSON", ""
        )
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
