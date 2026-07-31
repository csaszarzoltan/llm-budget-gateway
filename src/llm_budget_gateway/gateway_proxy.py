"""Core OpenAI-compatible proxy router.

Owns the request lifecycle: auth -> scopes -> sync enforce -> forward -> cost
record. Placeholder stub for the TDD RED phase — behavioral methods raise
NotImplementedError until implemented (P0-1). Interface is normative per
analysis brief §4 P0-1.
"""

from __future__ import annotations

import inspect
import time
from dataclasses import dataclass
from uuid import uuid4

import litellm

from .budget_enforcement import (
    BudgetEnforcer,
    BudgetExceededError,
    BudgetScope,
    RateLimitExceededError,
)
from .config import Settings
from .cost_tracking import CostTracker, TokenUsage
from .model_fallback import FallbackManager


class ApiKeyError(Exception):
    """Raised when the virtual API key is missing or unknown (HTTP 401)."""


@dataclass
class ProviderResponse:
    status_code: int
    body: dict | str
    headers: dict[str, str]
    model: str  # actual model that served the request
    usage: TokenUsage | None
    latency_ms: int


class GatewayProxy:
    """Owns the request lifecycle: auth -> scopes -> sync enforce -> forward
    -> cost record.
    """

    def __init__(
        self,
        settings: Settings,
        cost_tracker: CostTracker,
        budget_enforcer: BudgetEnforcer,
        fallback_manager: FallbackManager,
    ) -> None:
        self._settings = settings
        self._cost_tracker = cost_tracker
        self._budget_enforcer = budget_enforcer
        self._fallback_manager = fallback_manager

    async def handle_chat_completion(
        self, body: dict, api_key: str, headers: dict
    ) -> ProviderResponse:
        """Handle POST /v1/chat/completions through the full lifecycle."""
        return await self._handle(body, api_key, headers)

    async def handle_completion(
        self, body: dict, api_key: str, headers: dict
    ) -> ProviderResponse:
        """Handle POST /v1/completions through the full lifecycle."""
        return await self._handle(body, api_key, headers)

    async def handle_embeddings(
        self, body: dict, api_key: str, headers: dict
    ) -> ProviderResponse:
        """Handle POST /v1/embeddings through the full lifecycle."""
        return await self._handle(body, api_key, headers)

    async def _handle(
        self, body: dict, api_key: str, headers: dict
    ) -> ProviderResponse:
        """Shared lifecycle: auth -> scopes -> sync enforce -> hard check ->
        forward -> cost record. Errors map to HTTP responses.
        """
        request_id = uuid4().hex
        model = body.get("model", "") if isinstance(body, dict) else ""
        try:
            scopes = self.resolve_scopes(api_key, headers)
        except ApiKeyError as exc:
            return self._error_response(401, str(exc), model)

        est_input_tokens = self._estimate_input_tokens(body)
        try:
            self._budget_enforcer.check_sync(scopes, model, est_input_tokens)
        except RateLimitExceededError as exc:
            return self._error_response(429, str(exc), model)

        try:
            check = self._budget_enforcer.check_hard(scopes)
            if inspect.isawaitable(check):
                await check
        except BudgetExceededError as exc:
            return self._error_response(412, str(exc), model)

        try:
            response = await self.forward(model, body)
            await self._record(
                request_id=request_id,
                scope=scopes[0],
                model=response.model,
                usage=response.usage,
                latency_ms=response.latency_ms,
                status="success",
            )
            return response
        except Exception as exc:
            await self._record(
                request_id=request_id,
                scope=scopes[0],
                model=model,
                usage=None,
                latency_ms=0,
                status="error",
            )
            return self._error_response(502, f"provider error: {exc}", model)

    async def forward(
        self, model: str, body: dict, stream: bool = False
    ) -> ProviderResponse:
        """Forward to the provider via litellm.acompletion (stream-aware).

        Returns a ProviderResponse carrying usage, latency and the
        provider-shaped body (dict for non-stream, passthrough object for
        stream=true).
        """
        start = time.perf_counter()
        kwargs = dict(body)
        kwargs.setdefault("model", model)
        if stream:
            kwargs["stream"] = True
        response = await litellm.acompletion(**kwargs)
        latency_ms = int((time.perf_counter() - start) * 1000)

        usage: TokenUsage | None = None
        resp_usage = getattr(response, "usage", None)
        if resp_usage is not None:
            prompt_tokens = getattr(resp_usage, "prompt_tokens", 0) or 0
            completion_tokens = getattr(resp_usage, "completion_tokens", 0) or 0
            total_tokens = getattr(resp_usage, "total_tokens", 0) or (
                prompt_tokens + completion_tokens
            )
            usage = TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            )

        served_model = getattr(response, "model", None) or model
        headers = getattr(response, "headers", None) or {}
        status_code = getattr(response, "status_code", None) or 200
        body_out: dict | str = self._serializable_body(response, stream)
        return ProviderResponse(
            status_code=status_code,
            body=body_out,
            headers=headers,
            model=served_model,
            usage=usage,
            latency_ms=latency_ms,
        )

    def resolve_scopes(self, api_key: str, headers: dict) -> list[BudgetScope]:
        """Combine key scope + header-mapped user/team scopes + global scope.

        Raises ApiKeyError (401) if api_key is not in Settings.virtual_keys.
        """
        key_id = self._settings.virtual_keys.get(api_key)
        if key_id is None:
            raise ApiKeyError(f"unknown api key: {api_key!r}")
        scopes = [BudgetScope(kind="key", key=key_id)]
        lowered = {str(name).lower(): value for name, value in headers.items()}
        for header, kind in self._settings.user_header_mappings.items():
            value = lowered.get(str(header).lower())
            if value:
                scopes.append(BudgetScope(kind=kind, key=str(value)))
        scopes.append(BudgetScope(kind="global", key="default"))
        return scopes

    async def _record(
        self,
        *,
        request_id: str,
        scope: BudgetScope,
        model: str,
        usage: TokenUsage | None,
        latency_ms: int,
        status: str,
    ) -> None:
        """Best-effort cost record; tolerates non-awaitable tracker doubles."""
        record = getattr(self._cost_tracker, "build_record", None)
        if record is None:
            return
        usage_record = record(
            request_id=request_id,
            scope=scope,
            model=model,
            provider="litellm",
            usage=usage,
            latency_ms=latency_ms,
            status=status,
        )
        result = self._cost_tracker.record(usage_record)
        if inspect.isawaitable(result):
            await result

    def _estimate_input_tokens(self, body: dict) -> int:
        """Estimate prompt tokens via the fallback manager (0 when unknown)."""
        estimator = getattr(self._fallback_manager, "estimate_tokens", None)
        if estimator is None:
            return 0
        result = estimator(body)
        return result if isinstance(result, int) else 0

    @staticmethod
    def _serializable_body(response: object, stream: bool) -> dict | str:
        """Convert a non-stream provider response to a plain dict.

        Streaming responses pass through as the litellm iterator object.
        """
        if stream:
            return response  # type: ignore[return-value]
        if isinstance(response, dict):
            return response
        if hasattr(response, "model_dump"):
            return response.model_dump()
        if hasattr(response, "dict"):
            return response.dict()
        return response  # type: ignore[return-value]

    @staticmethod
    def _error_response(status_code: int, message: str, model: str) -> ProviderResponse:
        return ProviderResponse(
            status_code=status_code,
            body={
                "error": {
                    "message": message,
                    "type": "invalid_request_error",
                    "code": status_code,
                }
            },
            headers={"content-type": "application/json"},
            model=model,
            usage=None,
            latency_ms=0,
        )
