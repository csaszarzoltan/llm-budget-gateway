"""Core OpenAI-compatible proxy router.

Owns the request lifecycle: auth -> scopes -> sync enforce -> forward -> cost
record. Placeholder stub for the TDD RED phase — behavioral methods raise
NotImplementedError until implemented (P0-1). Interface is normative per
analysis brief §4 P0-1.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
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
from .cost_tracking import CostTracker, TokenUsage, accumulate_usage
from .model_fallback import FallbackManager

logger = logging.getLogger(__name__)

#: Client body fields allowed through to litellm. Everything else is dropped —
#: provider credentials (api_key/api_base/base_url/headers) and endpoint
#: overrides must come from gateway settings/env only, never from the client
#: body (SSRF + cost-bypass prevention).
_FORWARD_ALLOWLIST = frozenset(
    {
        "model",
        "messages",
        "prompt",
        "input",
        "stream",
        "stream_options",
        "temperature",
        "top_p",
        "n",
        "max_tokens",
        "max_completion_tokens",
        "stop",
        "presence_penalty",
        "frequency_penalty",
        "logit_bias",
        "user",
        "seed",
        "tools",
        "tool_choice",
        "response_format",
        "functions",
        "function_call",
        "suffix",
        "echo",
        "best_of",
        "logprobs",
        "top_logprobs",
        "encoding_format",
        "dimensions",
        "quality",
        "modalities",
        "audio",
        "parallel_tool_calls",
    }
)


class ApiKeyError(Exception):
    """Raised when the virtual API key is missing or unknown (HTTP 401)."""


class ProviderTimeoutError(TimeoutError):
    """Raised when the upstream provider does not respond (or a stream chunk
    does not arrive) within ``Settings.provider_timeout`` seconds.

    Subclasses ``TimeoutError`` so the fallback manager classifies it as
    ``timeout`` and retries down the chain when configured.
    """


@dataclass
class ProviderResponse:
    status_code: int
    body: dict | str | list
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
        except ApiKeyError:
            logger.warning(
                "auth failed request=%s key=%r",
                request_id,
                self._redact_key(api_key),
            )
            return self._error_response(401, "invalid or missing api key", model)

        if not self._model_known(model):
            return self._error_response(404, f"unknown model: {model}", model)

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
            response = await self._forward_with_fallback(
                model, body, api_key, headers
            )
        except ProviderTimeoutError:
            logger.warning("provider timeout request=%s model=%s", request_id, model)
            await self._record(
                request_id=request_id,
                scope=scopes[0],
                model=model,
                usage=None,
                latency_ms=0,
                status="timeout",
            )
            return self._error_response(502, "upstream provider timed out", model)
        except Exception as exc:
            logger.warning(
                "provider error request=%s model=%s: %s",
                request_id,
                model,
                exc,
            )
            await self._record(
                request_id=request_id,
                scope=scopes[0],
                model=model,
                usage=None,
                latency_ms=0,
                status="error",
            )
            return self._error_response(502, "upstream provider error", model)

        await self._record(
            request_id=request_id,
            scope=scopes[0],
            model=response.model,
            usage=response.usage,
            latency_ms=response.latency_ms,
            status="success",
        )
        return response

    async def _forward_with_fallback(
        self, model: str, body: dict, api_key: str, headers: dict
    ) -> ProviderResponse:
        """Route through FallbackManager.dispatch when a real manager is wired;
        fall back to plain forward for Mock-doubled managers (isawaitable
        guard pattern).
        """
        dispatch = getattr(self._fallback_manager, "dispatch", None)
        if dispatch is not None and inspect.iscoroutinefunction(dispatch):
            return await dispatch(self, model, body, api_key, headers)
        return await self.forward(model, body)

    async def forward(
        self, model: str, body: dict, stream: bool = False
    ) -> ProviderResponse:
        """Forward to the provider via litellm (stream-aware, timeout-bounded).

        Routes embeddings bodies (``input`` without ``messages``/``prompt``)
        to ``litellm.aembedding``; chat/completions bodies go to
        ``litellm.acompletion``. Stream=true bodies are drained, their usage
        aggregated and the chunks serialized into SSE lines (``data: <json>``
        framing + terminal ``data: [DONE]``) so the HTTP layer can stream
        them instead of crashing on raw chunk objects.

        Returns a ProviderResponse carrying usage, latency and the
        provider-shaped body (dict for non-stream, SSE line list for
        stream=true).
        """
        start = time.perf_counter()
        # Whitelist only — never forward api_key/base_url/headers from the
        # client body (provider auth/endpoint come from gateway settings/env).
        kwargs = {k: v for k, v in body.items() if k in _FORWARD_ALLOWLIST}
        kwargs["model"] = model  # gateway decides the model (fallback-aware)
        # Embeddings use litellm.aembedding — acompletion has no ``input``
        # param and would error/misroute. Detect by body shape: ``input``
        # without chat (messages) or legacy-completion (prompt) markers.
        is_embedding = (
            "input" in body and "messages" not in body and "prompt" not in body
        )
        is_stream = (not is_embedding) and (stream or bool(body.get("stream")))
        if is_stream:
            kwargs["stream"] = True
        try:
            if is_embedding:
                response = await asyncio.wait_for(
                    litellm.aembedding(**kwargs),
                    timeout=self._settings.provider_timeout,
                )
            else:
                response = await asyncio.wait_for(
                    litellm.acompletion(**kwargs),
                    timeout=self._settings.provider_timeout,
                )
        except TimeoutError as exc:
            raise ProviderTimeoutError(
                f"upstream provider timed out after "
                f"{self._settings.provider_timeout}s"
            ) from exc

        usage: TokenUsage | None = None
        body_out: dict | str | list
        served_model = getattr(response, "model", None) or model

        if is_stream and hasattr(response, "__aiter__"):
            # Streaming: drain and aggregate chunk usage so the request is
            # recorded at real cost, not $0 (budget bypass). Chunks are then
            # serialized into SSE lines — StreamingResponse requires
            # bytes/str, raw litellm chunk objects crash it.
            chunks, usage = await self._drain_stream(response)
            body_out = self._sse_lines(chunks)
            if chunks:
                served_model = self._chunk_model(chunks[0]) or served_model
        else:
            resp_usage = (
                response.get("usage")
                if isinstance(response, dict)
                else getattr(response, "usage", None)
            )
            if resp_usage is not None:
                prompt_tokens = getattr(resp_usage, "prompt_tokens", 0) or 0
                completion_tokens = (
                    getattr(resp_usage, "completion_tokens", 0) or 0
                )
                total_tokens = getattr(resp_usage, "total_tokens", 0) or (
                    prompt_tokens + completion_tokens
                )
                usage = TokenUsage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                )
            body_out = self._serializable_body(response, stream)

        latency_ms = int((time.perf_counter() - start) * 1000)
        headers_out = getattr(response, "headers", None) or {}
        status_code = getattr(response, "status_code", None) or 200
        return ProviderResponse(
            status_code=status_code,
            body=body_out,
            headers=headers_out,
            model=served_model,
            usage=usage,
            latency_ms=latency_ms,
        )

    async def _drain_stream(
        self, response: object
    ) -> tuple[list, TokenUsage | None]:
        """Consume an async streaming iterator, aggregating chunk usage.

        Each chunk must arrive within ``Settings.provider_timeout`` seconds;
        a stalled stream raises ProviderTimeoutError (availability guard,
        review checklist item 2). Returns ``(chunks, usage)``; usage is None
        when no chunk carried a usage object (provider did not emit one).
        """
        chunks: list = []
        usage_parts: list[dict] = []
        async for chunk in self._iter_with_timeout(response):
            chunks.append(chunk)
            chunk_usage = (
                chunk.get("usage")
                if isinstance(chunk, dict)
                else getattr(chunk, "usage", None)
            )
            if chunk_usage is not None:
                usage_parts.append(
                    {
                        "prompt_tokens": (
                            getattr(chunk_usage, "prompt_tokens", 0) or 0
                        ),
                        "completion_tokens": (
                            getattr(chunk_usage, "completion_tokens", 0) or 0
                        ),
                        "total_tokens": (
                            getattr(chunk_usage, "total_tokens", 0) or 0
                        ),
                    }
                )
        usage = accumulate_usage(usage_parts) if usage_parts else None
        return chunks, usage

    async def _iter_with_timeout(self, response: object):
        """Yield each chunk from ``response``, raising ProviderTimeoutError
        when a chunk does not arrive within ``Settings.provider_timeout``
        seconds. A healthy stream may run arbitrarily long chunk-to-chunk;
        only silence past the deadline fails."""
        aiter = response.__aiter__()  # type: ignore[union-attr]
        while True:
            try:
                chunk = await asyncio.wait_for(
                    aiter.__anext__(),
                    timeout=self._settings.provider_timeout,
                )
            except StopAsyncIteration:
                return
            except TimeoutError as exc:
                raise ProviderTimeoutError(
                    f"upstream stream stalled for "
                    f"{self._settings.provider_timeout}s"
                ) from exc
            yield chunk

    @staticmethod
    def _sse_lines(chunks: list) -> list[str]:
        """Serialize drained chunks into SSE frames: one ``data: <json>``
        line per chunk, terminated by ``data: [DONE]`` (OpenAI stream
        convention)."""
        lines = [
            "data: "
            + json.dumps(
                GatewayProxy._chunk_to_dict(c),
                ensure_ascii=False,
                default=str,
            )
            + "\n\n"
            for c in chunks
        ]
        lines.append("data: [DONE]\n\n")
        return lines

    @staticmethod
    def _chunk_to_dict(chunk: object) -> dict:
        """Best-effort plain-dict projection of a stream chunk (litellm
        ModelResponse, pydantic model, dict or namespace)."""
        if isinstance(chunk, dict):
            return chunk
        for attr in ("model_dump", "dict"):
            fn = getattr(chunk, attr, None)
            if callable(fn):
                try:
                    result = fn()
                except Exception:
                    continue
                if isinstance(result, dict):
                    return result
        if hasattr(chunk, "__dict__"):
            return dict(vars(chunk))
        return {"content": str(chunk)}

    @staticmethod
    def _chunk_model(chunk: object) -> str | None:
        """The ``model`` field of a chunk (dict- or object-shaped)."""
        if isinstance(chunk, dict):
            return chunk.get("model")
        return getattr(chunk, "model", None)

    @staticmethod
    def _redact_key(api_key: str) -> str:
        """Redact a submitted virtual key for logs: first 4 chars + length.

        Server logs are an exfiltration target — the full key must never be
        written (review finding D).
        """
        if not api_key:
            return "<empty>"
        if len(api_key) <= 4:
            return "****"
        return f"{api_key[:4]}…{len(api_key)}ch"

    def resolve_scopes(self, api_key: str, headers: dict) -> list[BudgetScope]:
        """Combine key scope + header-mapped user/team scopes + global scope.

        Raises ApiKeyError (401) if api_key is not in Settings.virtual_keys.
        """
        key_id = self._settings.virtual_keys.get(api_key)
        if key_id is None:
            raise ApiKeyError("invalid or missing api key")
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
        """Best-effort cost record; tolerates non-awaitable tracker doubles.

        A recording failure is logged and swallowed — it must never surface as
        a provider error (internal DB failure != provider failure).
        """
        try:
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
        except Exception:
            logger.exception(
                "cost record failed request=%s model=%s status=%s",
                request_id,
                model,
                status,
            )

    def _estimate_input_tokens(self, body: dict) -> int:
        """Estimate prompt tokens via the fallback manager (0 when unknown)."""
        estimator = getattr(self._fallback_manager, "estimate_tokens", None)
        if estimator is None:
            return 0
        result = estimator(body)
        return result if isinstance(result, int) else 0

    def _model_known(self, model: str) -> bool:
        """True when ``model`` is gateway-configured or litellm-known.

        Unknown models map to 404 (P0-1) instead of a 502 provider error.
        """
        if not model:
            return False
        if model in self._settings.pricing_overrides:
            return True
        for cfg in getattr(self._settings, "fallback_configs", []):
            if isinstance(cfg, dict) and (
                model == cfg.get("model") or model in cfg.get("chain", [])
            ):
                return True
        try:
            return model in litellm.model_cost
        except Exception:
            return False

    @staticmethod
    def _serializable_body(response: object, stream: bool) -> dict | str:
        """Convert a non-stream provider response to a plain dict.

        Streaming responses pass through as the litellm iterator object.
        Falls back to a ``vars()`` projection for namespace-shaped objects
        (test doubles, unusual providers) so the HTTP layer never receives a
        non-serializable body.
        """
        if stream:
            return response  # type: ignore[return-value]
        if isinstance(response, dict):
            return response
        for attr in ("model_dump", "dict"):
            fn = getattr(response, attr, None)
            if callable(fn):
                try:
                    result = fn()
                except Exception:
                    continue
                if isinstance(result, dict):
                    return result
        if hasattr(response, "__dict__"):
            return dict(vars(response))
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
