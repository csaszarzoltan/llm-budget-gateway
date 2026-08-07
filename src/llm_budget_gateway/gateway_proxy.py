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
import random
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import litellm

from .budget_enforcement import (
    BudgetEnforcer,
    BudgetExceededError,
    BudgetScope,
    RateLimitExceededError,
)
from .config import Settings
from .cost_tracking import CostTracker, TokenUsage, UsageRecord, accumulate_usage
from .model_fallback import FallbackManager

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

#: How long a sticky-session model binding stays valid before it expires
#: and the conversation may re-resolve through the whole route chain.
_STICKY_TTL_SECONDS = 3600

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


def _is_context_error(body: dict | str | list) -> bool:
    """True when a 400 error body signals context-window overflow.

    OpenAI-compatible providers usually answer "maximum context length"
    / "too many tokens" as a 400 with an ``error.message`` body (instead
    of 413/422), so the route loop treats those as fallback-eligible.
    """
    text = ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            text = str(err.get("message", ""))
        elif isinstance(err, str):
            text = err
        if not text:
            text = str(body)
    elif isinstance(body, str):
        text = body
    text = text.lower()
    return any(
        key in text
        for key in (
            "context length",
            "context_length",
            "maximum context",
            "max context",
            "too many tokens",
            "token limit",
            "exceeds the maximum",
            "input is too long",
            "requested context",
            "context window",
        )
    )


@dataclass
class ProviderResponse:
    status_code: int
    body: dict | str | list | AsyncIterator[str]
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
        self._routing_control_plane = None
        self._product_console = None
        self._direct_client = None
        self._routing_now: Callable[[], datetime] = lambda: datetime.now(UTC)
        # session_id -> (serving model, monotonic ts) for sticky sessions:
        # an agentic conversation keeps its model while it stays healthy.
        self._sticky_sessions: dict[str, tuple[str, float]] = {}
        # Integrated intelligence (formerly the separate satellite service):
        # exact-response cache, PII redaction and cost-aware routing are now
        # wired straight into the proxy path. Attached lazily by main.py so
        # tests can run without the SQLite files.
        self._intel_cache = None
        self._intel_redactor = None
        self._intel_cost_router = None

    def attach_intelligence(self, cache=None, redactor=None, cost_router=None) -> None:
        """Attach the integrated intelligence helpers (cache, PII, cost)."""
        self._intel_cache = cache
        self._intel_redactor = redactor
        self._intel_cost_router = cost_router

    def attach_product_console(self, store: object) -> None:
        """Attach the UI-managed route store (pc_routes/targets model).

        Routes created and published on the cockpit Routes tab are resolved
        by this store; the user edits them in the UI and no gateway code
        change or restart is needed afterwards.
        """
        self._product_console = store

    def attach_routing_control_plane(self, plane: object, *, now: Callable[[], datetime] | None = None) -> None:
        """Attach logical-route resolution for application gateway keys."""
        self._routing_control_plane = plane
        if now is not None:
            self._routing_now = now

    def attach_direct_client(self, client: object) -> None:
        """Attach the direct provider transport (replaces litellm forwarding).

        When attached, models resolved by the direct client are forwarded as
        plain HTTP calls to the configured provider endpoint; models the
        client does not know fall back to the legacy litellm path.
        """
        self._direct_client = client

    # -- sticky session helpers -------------------------------------------

    @staticmethod
    def _extract_session_id(body: dict) -> str | None:
        """Pull a conversation identifier the client can echo per request."""
        sid = body.get("session_id") or body.get("conversation_id")
        if not sid:
            metadata = body.get("metadata")
            if isinstance(metadata, dict):
                sid = metadata.get("session_id") or metadata.get("conversation_id")
        return str(sid) if sid else None

    def _set_sticky(self, session_id: str, model: str) -> None:
        self._sticky_sessions[session_id] = (model, time.monotonic())

    def _get_sticky(self, session_id: str) -> str | None:
        """Return the bound model, expiring stale bindings (TTL 1h)."""
        entry = self._sticky_sessions.get(session_id)
        if entry is None:
            return None
        model, ts = entry
        if time.monotonic() - ts > _STICKY_TTL_SECONDS:
            self._sticky_sessions.pop(session_id, None)
            return None
        return model

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
        if self._routing_control_plane is not None:
            try:
                self._routing_control_plane.authenticate_application(api_key)
            except PermissionError:
                pass
            else:
                try:
                    return await self._handle_logical_route(body, api_key, headers, request_id)
                except ProviderTimeoutError:
                    return self._error_response(
                        502, "upstream provider timed out", model
                    )
                except Exception:
                    logger.exception(
                        "logical route failed request=%s model=%s", request_id, model
                    )
                    return self._error_response(
                        502, "upstream provider error", model
                    )
        if self._product_console is not None:
            try:
                self._product_console.authenticate_application(api_key)
            except PermissionError:
                pass
            else:
                try:
                    return await self._handle_logical_route(body, api_key, headers, request_id)
                except ProviderTimeoutError:
                    return self._error_response(
                        502, "upstream provider timed out", model
                    )
                except Exception:
                    logger.exception(
                        "logical route failed request=%s model=%s", request_id, model
                    )
                    return self._error_response(
                        502, "upstream provider error", model
                    )
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
            response = await self._forward_with_fallback(model, body, api_key, headers)
        except ProviderTimeoutError:
            logger.warning("provider timeout request=%s model=%s", request_id, model)
            await self._record(
                request_id=request_id,
                scope=scopes[0],
                model=model,
                usage=None,
                latency_ms=0,
                status="timeout",
                status_code=502,
                customer_id=self._resolve_request_customer(body, headers),
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
                status_code=502,
                customer_id=self._resolve_request_customer(body, headers),
            )
            return self._error_response(502, "upstream provider error", model)

        await self._record(
            request_id=request_id,
            scope=scopes[0],
            model=response.model,
            usage=response.usage,
            latency_ms=response.latency_ms,
            status="success",
            customer_id=self._resolve_request_customer(body, headers),
        )
        # Rate-limit visibility: attach standard X-RateLimit-* headers from
        # the last check_sync so clients (Hermes) can show remaining quota.
        try:
            rl = getattr(self._budget_enforcer, "_last_rate_limit_state", {})
            if rl:
                first = next(iter(rl.values()))
                response.headers = dict(response.headers or {})
                if "tpm_remaining" in first:
                    response.headers["X-RateLimit-Remaining"] = str(
                        first["tpm_remaining"]
                    )
                if "rpm_remaining" in first:
                    response.headers["X-RateLimit-RPM-Remaining"] = str(
                        first["rpm_remaining"]
                    )
                if "reset_at" in first:
                    response.headers["X-RateLimit-Reset"] = str(first["reset_at"])
        except Exception:
            logger.exception("rate limit header attach failed request=%s", request_id)
        return response

    async def _handle_logical_route(
        self, body: dict, api_key: str, headers: dict, request_id: str
    ) -> ProviderResponse:
        """Resolve and execute a published route for an application key.

        Routes published on the cockpit Routes tab (pc_routes/targets model)
        take precedence; the logical routing plane (admin API) is the fallback
        for routes created outside the UI.
        """
        plane = self._routing_control_plane
        alias = str(body.get("model", ""))
        if not alias:
            return self._error_response(404, f"unknown route: {alias}", alias)
        metadata = body.get("metadata", {})
        # Profile-aware routing: a client (Hermes) can request a specific
        # route per profile via X-Hermes-Profile or X-Gateway-Route header
        # (or metadata.profile). The header wins over the body model name,
        # so one client key can drive multiple routes without editing
        # configs — e.g. hermes-coding → coding route, hermes-research →
        # research route.
        profile_route = ""
        for hdr in ("x-hermes-profile", "x-gateway-route"):
            val = headers.get(hdr) if isinstance(headers, dict) else None
            if val:
                profile_route = str(val).strip()
                break
        if not profile_route:
            profile_route = str(metadata.get("profile", "") or "").strip()
        if profile_route and profile_route != alias:
            # Only override when the named route actually exists — otherwise
            # fall through to the body model (which may be a valid alias).
            store = self._product_console
            exists = False
            if store is not None:
                try:
                    exists = store.published_route_by_name(profile_route) is not None
                except Exception:
                    exists = False
            if exists:
                alias = profile_route
        # Conversation tracking: an optional X-Gateway-Conversation header
        # (or metadata.conversation_id) tags every request of one client
        # conversation so usage can be aggregated per conversation.
        metadata = metadata if isinstance(metadata, dict) else {}
        conversation_id = ""
        for hdr in ("x-gateway-conversation", "x-conversation-id"):
            val = headers.get(hdr) if isinstance(headers, dict) else None
            if val:
                conversation_id = str(val).strip()
                break
        if not conversation_id:
            conversation_id = str(
                metadata.get("conversation_id", "") or ""
            ).strip()
        conversation_id = conversation_id or None
        # Client identity: any client may send these to tag who originated
        # the request (e.g. hermes profile names, custom app names).
        client_id = str(metadata.get("client_id", "")) or None
        client_profile = str(metadata.get("client_profile", "")) or None
        capabilities = []
        if body.get("tools"):
            capabilities.append("tools")
        if body.get("response_format"):
            capabilities.append("structured_output")
        for value in metadata.get("capabilities", []):
            if isinstance(value, str) and value not in capabilities:
                capabilities.append(value)

        # 1) UI-managed route (Routes tab, targets model) — user-editable.
        store = self._product_console
        # When omitted, the gateway falls back to the registered application
        # name so every request is attributed — no client modification needed.
        if not client_id:
            # Try pc_apps first (UI-created applications).
            if store is not None:
                try:
                    app_info = store.authenticate_application(str(api_key))
                    client_id = str(app_info.get("name", ""))
                except Exception:
                    pass
            # Fallback: control-plane / gateway keys → use key prefix + short ID
            if not client_id:
                client_id = f"gw-{str(api_key)[-8:]}" if api_key else "anonymous"
        route = None
        from_plane = False
        if store is not None:
            try:
                route = store.published_route_by_name(alias)
            except Exception:
                route = None
        if route is not None:
            decision = self._resolve_targets(route, capabilities, body=body)
            if decision is None:
                return self._error_response(422, "no eligible route target", alias)
            candidates = decision["candidates"]
            fallback_statuses = decision["fallback_statuses"]
            fallback = decision.get("fallback_reason") or "none"
            target_cooldowns = decision.get("target_cooldowns", {})
            route_name = route["name"]
        else:
            # 2) Logical routing plane (admin-created routes).
            from_plane = True
            try:
                decision = plane.resolve_alias(
                    alias,
                    now=self._routing_now(),
                    quality_tier=str(metadata.get("quality_tier", "balanced")),
                    estimated_cost=float(metadata.get("max_cost_usd", 0)),
                    region=str(metadata.get("region", "eu")),
                    capabilities=capabilities,
                )
            except (KeyError, RuntimeError, TypeError, ValueError) as exc:
                return self._error_response(404, f"unknown route: {alias}", alias)
            candidates = list(decision.get("candidate_models", []))
            selected = str(decision["selected_model"])
            if selected in candidates:
                candidates.remove(selected)
            candidates.insert(0, selected)
            fallback = decision.get("fallback_reason") or "none"
            fallback_statuses = decision.get("fallback_statuses", [])
            target_cooldowns = {}
            route_name = alias
        response = None
        served: str | None = None
        outbound = {k: v for k, v in body.items() if k != "metadata"}
        # Thinking / reasoning support: when the client sends metadata.thinking
        # or metadata.reasoning_effort, forward them to the provider in the
        # request body. Different providers use different field names — the
        # gateway passes them through so every thinking-capable model works.
        if metadata.get("thinking"):
            outbound["thinking"] = metadata["thinking"]
        if metadata.get("reasoning_effort"):
            outbound["reasoning_effort"] = metadata["reasoning_effort"]
        # Integrated intelligence — exact-response cache: an identical
        # request (same route + payload) served recently is answered from
        # the cache instead of burning tokens. Opt-in per request via
        # X-Gateway-Cache: 1 header, or per route via metadata.
        want_cache = (
            str(headers.get("x-gateway-cache", "")).lower() == "1"
            or str(metadata.get("cache", "")).lower() == "1"
        )
        if want_cache and self._intel_cache is not None:
            try:
                cached = self._intel_cache.get("default", outbound)
                if cached is not None:
                    resp = ProviderResponse(
                        status_code=200,
                        body=cached,
                        headers={"X-Gateway-Cache-Hit": "1", "X-Gateway-Route": route_name},
                        latency_ms=0,
                        model=str(served or cached.get("model", "")),
                        usage=None,
                    )
                    # Record the cache hit so the Usage page shows it
                    scope = BudgetScope(kind="key", key=str(api_key))
                    cache_record = UsageRecord(
                        request_id=request_id,
                        api_key=str(api_key),
                        user_id=None, team=None,
                        model=str(served or cached.get("model", "")),
                        provider="direct",
                        prompt_tokens=0, completion_tokens=0, total_tokens=0,
                        reasoning_tokens=0,
                        input_cost=0.0, output_cost=0.0, reasoning_cost=0.0,
                        total_cost=0.0,
                        latency_ms=0,
                        status="success",
                        timestamp=int(time.time()),
                        route=route_name,
                        client_id=client_id,
                        client_profile=client_profile,
                        cache_hit=True,
                    )
                    cache_record.customer_id = self._cost_tracker.resolve_customer_id(
                        metadata=metadata, headers=headers, client_id=client_id
                    )
                    try:
                        result = self._cost_tracker.record(cache_record)
                        if inspect.isawaitable(result):
                            await result
                    except Exception:
                        pass
                    logger.info(
                        "request=%s route=%s cache-hit", request_id, route_name
                    )
                    return resp
            except Exception:
                logger.exception("cache lookup failed request=%s", request_id)
        # Integrated intelligence — PII redaction: when the client asks
        # (X-Gateway-Redact-Pii: 1), user messages are redacted before the
        # request reaches the provider, so emails/cards/phones never leave
        # the gateway in plaintext.
        want_redact = (
            str(headers.get("x-gateway-redact-pii", "")).lower() == "1"
            or str(metadata.get("redact_pii", "")).lower() == "1"
        )
        if want_redact and self._intel_redactor is not None and isinstance(outbound.get("messages"), list):
            try:
                for msg in outbound["messages"]:
                    if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                        result = self._intel_redactor.redact(msg["content"])
                        if result.count:
                            msg["content"] = result.text
            except Exception:
                logger.exception("pii redaction failed request=%s", request_id)
        # Integrated intelligence — cost-aware routing: when the request
        # asks for it (metadata cost_aware: true), reorder the eligible
        # candidates by lowest cost among healthy models instead of fixed
        # priority. Falls back to the original order on any error.
        if (
            str(metadata.get("cost_aware", "")).lower() in ("1", "true")
            and self._intel_cost_router is not None
            and len(candidates) > 1
        ):
            try:
                priced: list[dict] = []
                for cand in candidates:
                    _, _, total_per_m = self._cost_tracker.estimate_cost(cand, 1000, 1000)
                    priced.append({
                        "model": cand,
                        "cost": float(total_per_m),
                        "quality": 1.0,
                        "latency_ms": 0,
                        "healthy": True,
                    })
                chosen = self._intel_cost_router.choose(
                    priced, min_quality=0.0, max_latency_ms=None
                )
                best = str(chosen["model"])
                if best in candidates:
                    rest = [c for c in candidates if c != best]
                    candidates = [best] + rest
                    fallback = "cost_aware"
            except Exception:
                logger.exception("cost-aware routing failed request=%s", request_id)
        # Sticky session: while a conversation's model stays healthy (not in
        # cooldown), keep serving it instead of re-walking the whole chain.
        session_id = self._extract_session_id(body)
        sticky_model = None
        if session_id:
            sticky_model = self._get_sticky(session_id)
            if sticky_model is not None and (
                sticky_model not in candidates
                or self._cost_tracker.model_in_cooldown(route_name, sticky_model)
            ):
                sticky_model = None
        if sticky_model is not None:
            candidates = [sticky_model]
            fallback = "sticky_session"
        logger.info(
            "request=%s route=%s candidates=%s sticky=%s",
            request_id,
            route_name,
            ",".join(candidates),
            sticky_model or "-",
        )
        # Total fallback-chain deadline: the sum of per-target timeouts can
        # exceed the client's own timeout (Hermes ~60-90s), so a chain of
        # cooldown skips + slow timeouts ends with the client giving up
        # ("provider failed after retries") even though a later fallback
        # would answer. Once the budget is spent, skip the remaining
        # candidates and try the last one with the leftover time.
        chain_started = time.perf_counter()
        chain_budget = float(getattr(self._settings, "route_timeout_budget", 90.0))
        for index, candidate in enumerate(candidates):
            is_last = index + 1 >= len(candidates)
            response_retried = False
            remaining = 0
            # Enforce the chain budget: after it is spent, only the last
            # candidate may still be attempted (with whatever time is left).
            elapsed = time.perf_counter() - chain_started
            if elapsed >= chain_budget and not is_last:
                fallback = f"chain_budget_{int(chain_budget)}s"
                logger.info(
                    "route=%s chain budget %ss spent (%.1fs) skipping %s request=%s",
                    route_name,
                    chain_budget,
                    elapsed,
                    candidate,
                    request_id,
                )
                continue
            if not from_plane:
                try:
                    remaining = self._cost_tracker.model_in_cooldown(
                        route_name, candidate
                    )
                except Exception:
                    remaining = 0
            if remaining and not is_last:
                # Model is cooling down after a recent failure (e.g. daily
                # quota exhausted) — skip it instead of walking the whole
                # chain again on every request.
                fallback = f"model_cooldown_{remaining}s"
                logger.info(
                    "route=%s model=%s skipped (cooldown %ss) request=%s",
                    route_name,
                    candidate,
                    remaining,
                    request_id,
                )
                continue
            # Per-target timeout: a target's timeout_seconds is enforced here
            # (capped by the global provider timeout). Only applies to
            # UI-managed routes; plane routes keep the global timeout.
            target_timeout: float | None = None
            if not from_plane:
                try:
                    to = int(
                        next(
                            (
                                t.get("timeout_seconds")
                                for t in route.get("targets", [])
                                if str(t.get("model", "")) == candidate
                            ),
                            0,
                        )
                        or 0
                    )
                    if to > 0:
                        target_timeout = min(
                            to, int(self._settings.provider_timeout)
                        )
                except (TypeError, ValueError):
                    target_timeout = None
            # Cap the per-target timeout by the remaining chain budget so a
            # single target cannot burn the whole budget (a 120-180s target
            # would still blow past the client timeout).
            if target_timeout is not None:
                remaining_budget = chain_budget - (
                    time.perf_counter() - chain_started
                )
                if remaining_budget > 0:
                    target_timeout = min(target_timeout, remaining_budget)
                else:
                    target_timeout = 0.01  # last candidate: tiny grace
            try:
                response = await self.forward(
                    candidate, outbound, timeout=target_timeout
                )
            except ProviderTimeoutError:
                if is_last:
                    raise
                cooldown_seconds = (
                    int(target_cooldowns.get(candidate, 3600))
                    if target_cooldowns
                    else 3600
                )
                try:
                    self._cost_tracker.set_model_cooldown(
                        route_name,
                        candidate,
                        cooldown_seconds,
                        reason=json.dumps(
                            {
                                "type": "timeout",
                                "seconds": int(target_timeout or 0),
                            }
                        ),
                    )
                except Exception:
                    logger.exception(
                        "cooldown record failed route=%s model=%s",
                        route_name,
                        candidate,
                    )
                fallback = f"provider_timeout_{int(target_timeout or 0)}s"
                logger.info(
                    "route=%s model=%s timed out after %ss request=%s",
                    route_name,
                    candidate,
                    target_timeout or self._settings.provider_timeout,
                    request_id,
                )
                continue
            except Exception as exc:
                if is_last:
                    raise
                cooldown_seconds = (
                    int(target_cooldowns.get(candidate, 3600))
                    if target_cooldowns
                    else 3600
                )
                try:
                    self._cost_tracker.set_model_cooldown(
                        route_name,
                        candidate,
                        cooldown_seconds,
                        reason=json.dumps(
                            {
                                "type": "error",
                                "error": str(exc)[:500],
                            }
                        ),
                    )
                except Exception:
                    logger.exception(
                        "cooldown record failed route=%s model=%s",
                        route_name,
                        candidate,
                    )
                fallback = "provider_error"
                logger.warning(
                    "route=%s model=%s failed request=%s error=%s",
                    route_name,
                    candidate,
                    request_id,
                    exc,
                )
                continue
            if response.status_code not in set(fallback_statuses):
                if not (
                    response.status_code == 400
                    and _is_context_error(response.body)
                ):
                    if response.status_code >= 400:
                        # Non-fallback provider error returned to the client —
                        # must be visible in the log or these failures stay
                        # silent (e.g. 405 Method Not Allowed).
                        logger.warning(
                            "route=%s model=%s non-fallback error status=%s "
                            "request=%s latency=%sms body=%s",
                            route_name,
                            candidate,
                            response.status_code,
                            request_id,
                            response.latency_ms,
                            str(response.body)[:400],
                        )
                    served = candidate
                    break
                # Some providers report context-window overflow as 400 with a
                # context-length error body instead of 413/422. Walk to the
                # next target WITHOUT cooldown (the request is simply too
                # large for this model — not a provider outage).
                logger.warning(
                    "route=%s model=%s context overflow (400) request=%s body=%s",
                    route_name,
                    candidate,
                    request_id,
                    str(response.body)[:300],
                )
                if index + 1 < len(candidates):
                    fallback = "context_window_400"
                continue
            # Transient 5xx (502/503/504) is usually momentary provider
            # overload — retry the SAME model once before falling back, so a
            # rare blip does not degrade the response or park the model in a
            # long cooldown. Only one retry, then fall through to the normal
            # fallback + short-cooldown path below.
            if (
                int(response.status_code or 0) in (502, 503, 504)
                and not response_retried
            ):
                response_retried = True
                logger.info(
                    "route=%s model=%s transient %s retrying once request=%s",
                    route_name,
                    candidate,
                    response.status_code,
                    request_id,
                )
                try:
                    response = await self.forward(
                        candidate, outbound, timeout=target_timeout
                    )
                except ProviderTimeoutError:
                    if is_last:
                        raise
                    continue
                except Exception as exc:
                    if is_last:
                        raise
                    continue
                if int(response.status_code or 0) < 400:
                    served = candidate
                    break
            cooldown_seconds = 3600
            if target_cooldowns:
                cooldown_seconds = int(target_cooldowns.get(candidate, 3600))
            # Transient 5xx (502/503/504) usually means the provider is
            # momentarily overloaded, not that the model is unusable — a
            # short cooldown (or none) lets the model come back quickly
            # instead of being parked for the target's full cooldown
            # (e.g. 600s), which is what the UI "cooldown" would do. Rate
            # limits (429) and hard client errors keep the full cooldown.
            transient = int(response.status_code or 0) in (502, 503, 504)
            if transient:
                cooldown_seconds = min(cooldown_seconds, 60)
            try:
                body_text = ""
                if isinstance(response.body, dict):
                    err = response.body.get("error") or {}
                    body_text = (
                        str(err.get("provider_body", ""))
                        if isinstance(err, dict)
                        else str(err)
                    )
                if not body_text:
                    body_text = (
                        response.body
                        if isinstance(response.body, str)
                        else json.dumps(response.body)[:500]
                    )
                self._cost_tracker.set_model_cooldown(
                    route_name,
                    candidate,
                    cooldown_seconds,
                    reason=json.dumps(
                        {
                            "type": "http",
                            "status_code": response.status_code,
                            "body": body_text[:800],
                        }
                    ),
                )
            except Exception:
                logger.exception(
                    "cooldown record failed route=%s model=%s", route_name, candidate
                )
            if index + 1 < len(candidates):
                fallback = f"provider_status_{response.status_code}"
        assert response is not None
        response.headers = dict(response.headers)
        response.headers["X-Gateway-Route"] = route_name
        response.headers["X-Gateway-Serving-Model"] = response.model
        response.headers["X-Gateway-Fallback"] = str(fallback)
        if sticky_model is not None:
            response.headers["X-Gateway-Sticky-Session"] = "1"
        if session_id and response.status_code < 400:
            # Bind to the gateway candidate name — providers may echo a
            # shortened model name, which would not match the route chain.
            self._set_sticky(session_id, served or response.model)

        scope = BudgetScope(kind="key", key=str(api_key))
        cost = 0.0

        # Live-stream responses carry a body generator; the usage is only
        # known once the stream finishes, so the cost record is deferred to
        # a wrapper generator that runs after the last chunk.
        if isinstance(response.body, AsyncIterator):
            original_body = response.body

            async def _wrapped_stream() -> AsyncIterator[str]:
                nonlocal cost
                stream_usage: TokenUsage | None = None
                try:
                    async for ev in original_body:
                        yield ev
                finally:
                    # Aggregate usage from the drained chunks (the direct
                    # client stores them on the response).
                    gen_chunks = getattr(response, "_stream_chunks", None) or []
                    stream_usage = GatewayProxy._collect_stream_usage(gen_chunks)
                    try:
                        record = self._cost_tracker.build_record(
                            request_id=request_id,
                            scope=scope,
                            model=served or response.model,
                            provider="direct",
                            usage=stream_usage,
                            latency_ms=int(
                                (time.perf_counter() - chain_started) * 1000
                            ),
                            status="success"
                            if response.status_code < 400
                            else "error",
                            route=route_name,
                            status_code=response.status_code,
                            conversation_id=conversation_id,
                        )
                        record.client_id = client_id
                        record.client_profile = client_profile
                        record.cache_hit = False
                        record.customer_id = (
                            self._cost_tracker.resolve_customer_id(
                                metadata=metadata,
                                headers=headers,
                                client_id=client_id,
                            )
                        )
                        cost = float(getattr(record, "total_cost", 0.0))
                        result = self._cost_tracker.record(record)
                        if inspect.isawaitable(result):
                            await result
                    except Exception:
                        logger.exception(
                            "product route stream cost record failed request=%s",
                            request_id,
                        )

            response.body = _wrapped_stream()
            return response

        try:
            record = self._cost_tracker.build_record(
                request_id=request_id,
                scope=scope,
                # Use the gateway target name (served) — providers may echo a
                # shortened/prefix-less model name, which would fragment the
                # usage stats and break per-target status lookups.
                model=served or response.model,
                provider="direct",
                usage=response.usage,
                latency_ms=response.latency_ms,
                status="success" if response.status_code < 400 else "error",
                route=route_name,
                status_code=response.status_code,
                conversation_id=conversation_id,
            )
            # Tag the record with client identity and cache status.
            record.client_id = client_id
            record.client_profile = client_profile
            record.cache_hit = False
            record.customer_id = self._cost_tracker.resolve_customer_id(
                metadata=metadata, headers=headers, client_id=client_id
            )
            cost = float(getattr(record, "total_cost", 0.0))
            result = self._cost_tracker.record(record)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception("product route cost record failed request=%s", request_id)
        logger.info(
            "request=%s route=%s served=%s status=%s fallback=%s latency=%sms",
            request_id,
            route_name,
            served or response.model,
            response.status_code,
            fallback or "-",
            response.latency_ms,
        )
        if response.status_code < 400 and self._product_console is not None:
            try:
                self._product_console.record_request(
                    app_id=str(api_key),
                    route=route_name,
                    model=response.model,
                    cost=cost,
                    latency=response.latency_ms,
                    success=response.status_code < 400,
                    reason=str(fallback),
                )
            except Exception:
                logger.exception("product route activity record failed request=%s", request_id)
        if response.status_code < 400 and from_plane:
            try:
                plane.record_model_spend(
                    alias, response.model, cost, at=self._routing_now()
                )
            except Exception:
                logger.exception("logical route spend record failed request=%s", request_id)
        # Cache the successful response when the client asked for caching,
        # so an identical later request is answered without a provider call.
        if (
            want_cache
            and self._intel_cache is not None
            and response.status_code < 400
            and isinstance(response.body, dict)
        ):
            try:
                self._intel_cache.put(
                    "default", outbound, response.body, ttl=300
                )
            except Exception:
                logger.exception("cache put failed request=%s", request_id)
        return response

    def _resolve_targets(
        self, route: dict, capabilities: list[str], body: dict | None = None
    ) -> dict | None:
        """Pick the first eligible target of a UI route at the current instant.

        Mirrors ProductConsoleStore.test_route: a target is eligible when the
        current time in its timezone falls inside its start/end window, its
        required capabilities are satisfied, its metadata condition (if any)
        matches the request, and the estimated request cost stays under its
        ``max_cost_usd`` ceiling. Targets with ``mode: weighted`` (or an
        explicit ``weight``) are chosen proportionally to their weight, then
        ordered by weight for the fallback walk. Returns ordered candidates
        plus the union of the on_status_codes fallback statuses.
        """
        now = self._routing_now()
        eligible: list[dict] = []
        excluded: list[str] = []
        for target in route.get("targets", []):
            local = now.astimezone(ZoneInfo(str(target.get("timezone", "UTC"))))
            clock = local.strftime("%H:%M")
            start = str(target.get("start", "00:00"))
            end = str(target.get("end", "23:59"))
            inside = (
                start <= clock < end if start < end else clock >= start or clock < end
            )
            reason = None if inside else "outside_schedule"
            if reason is None and not set(
                target.get("required_capabilities", [])
            ).issubset(capabilities):
                reason = "missing_capabilities"
            if reason is None:
                reason = self._condition_reason(target, body)
            if reason is None:
                reason = self._budget_gate_reason(target, body)
            if reason:
                excluded.append(f"{target.get('model')} ({reason})")
            else:
                eligible.append(target)
        if not eligible:
            return None
        has_weighted = any(
            t.get("mode") == "weighted" or t.get("weight") is not None
            for t in eligible
        )
        if has_weighted:
            weights = [
                max(0.0, float(t.get("weight", 10) or 10)) for t in eligible
            ]
            chosen = random.choices(eligible, weights=weights, k=1)[0]
            rest = [t for t in eligible if t is not chosen]
            rest.sort(key=lambda t: -max(0.0, float(t.get("weight", 10) or 10)))
            ordered = [chosen] + rest
        else:
            ordered = sorted(eligible, key=lambda t: int(t.get("priority", 100)))
        statuses: set[int] = set()
        for target in ordered:
            for code in target.get("on_status_codes", [408, 409, 425, 429, 500, 502, 503, 504]):
                try:
                    statuses.add(int(code))
                except (TypeError, ValueError):
                    continue
        # Context-window overflow (413/422) is always fallback-eligible:
        # a request too large for one target's window may fit the next
        # target's — independent of the UI's on_status_codes list.
        statuses.update({413, 422})
        return {
            "candidates": [str(t["model"]) for t in ordered],
            "fallback_statuses": sorted(statuses),
            "fallback_reason": None if not excluded else "outside_schedule",
            "target_cooldowns": {
                str(t["model"]): int(t.get("cooldown_seconds", 3600))
                for t in ordered
            },
        }

    @staticmethod
    def _condition_reason(target: dict, body: dict | None) -> str | None:
        """Eligibility by request metadata condition.

        The UI stores ``condition: {field, operator, value}`` on a target
        (field like ``metadata.plan``). A target without a condition is always
        eligible; with one, it only serves requests whose metadata matches.
        """
        cond = target.get("condition")
        if not cond or not isinstance(cond, dict):
            return None
        metadata: dict = {}
        if body and isinstance(body.get("metadata"), dict):
            metadata = body["metadata"]
        field = str(cond.get("field", ""))
        key = field[len("metadata."):] if field.startswith("metadata.") else field
        got = metadata.get(key)
        op = str(cond.get("operator", "equals"))
        want = cond.get("value")
        if op == "equals":
            ok = got == want
        elif op == "not_equals":
            ok = got != want
        elif op == "contains":
            ok = want is not None and str(want) in str(got or "")
        else:
            ok = True
        return None if ok else "condition_mismatch"

    def _budget_gate_reason(self, target: dict, body: dict | None) -> str | None:
        """Exclude a target whose estimated request cost exceeds its
        ``max_cost_usd`` per-request ceiling (budget gate)."""
        ceiling = target.get("max_cost_usd")
        if ceiling is None or not body:
            return None
        try:
            max_out = body.get("max_completion_tokens", body.get("max_tokens", 0))
            if isinstance(max_out, bool) or not isinstance(max_out, int) or max_out < 0:
                max_out = 0
            input_tokens = self._fallback_manager.estimate_tokens(body)
            _, _, total = self._cost_tracker.estimate_cost(
                str(target.get("model", "")), input_tokens, int(max_out)
            )
            if total > float(ceiling):
                return "budget_gate"
        except Exception:
            pass
        return None

    async def _forward_direct(
        self, model: str, body: dict, stream: bool = False, timeout: float | None = None,
        request_id: str | None = None,
    ) -> ProviderResponse:
        """Forward via the direct provider transport (no litellm).

        Stream=true requests now yield SSE chunks LIVE to the client
        instead of draining them first: the first chunk arrives within
        ``timeout`` (first-byte budget), then each subsequent chunk is
        yielded immediately. The response body is an async SSE generator.
        Usage is NOT available in the ProviderResponse.usage (None); the
        caller wraps the generator and writes the cost record on
        completion.
        """
        start = time.perf_counter()
        effective_timeout = (
            timeout if timeout is not None else self._settings.provider_timeout
        )
        is_embedding = (
            "input" in body and "messages" not in body and "prompt" not in body
        )
        is_stream = (not is_embedding) and (stream or bool(body.get("stream")))
        kind = "embedding" if is_embedding else "chat"
        served = model
        if is_stream:
            # Live streaming: await the FIRST chunk HERE (inside the route
            # loop, so a 4xx/5xx/timeout on the first byte falls back to the
            # next candidate exactly like a drained call), then yield the
            # remaining chunks to the client as they arrive.
            chunks: list[dict] = []
            agen = self._direct_client.stream_chunks(  # type: ignore[attr-defined]
                model, body, kind=kind
            )
            try:
                first_chunk = await asyncio.wait_for(
                    agen.__anext__(), timeout=effective_timeout
                )
            except StopAsyncIteration:
                await agen.aclose()  # type: ignore[union-attr]
                return ProviderResponse(
                    status_code=200,
                    body=[],
                    headers={},
                    model=served or model,
                    usage=None,
                    latency_ms=int((time.perf_counter() - start) * 1000),
                )
            except TimeoutError as exc:
                try:
                    await agen.aclose()  # type: ignore[union-attr]
                except Exception:
                    pass
                raise ProviderTimeoutError(
                    f"upstream provider timed out after {effective_timeout}s"
                ) from exc
            chunks.append(first_chunk)
            if isinstance(first_chunk, dict) and first_chunk.get("model"):
                served = first_chunk["model"]

            async def _rest_stream() -> AsyncIterator[str]:
                # First chunk is already consumed — emit it, then stream
                # the remainder live.
                yield "data: " + json.dumps(
                    GatewayProxy._chunk_to_dict(first_chunk),
                    ensure_ascii=False,
                    default=str,
                ) + "\n\n"
                async for chunk in agen:
                    chunks.append(chunk)
                    yield "data: " + json.dumps(
                        GatewayProxy._chunk_to_dict(chunk),
                        ensure_ascii=False,
                        default=str,
                    ) + "\n\n"
                yield "data: [DONE]\n\n"
                # Usage is aggregated here (last usage chunk), so the cost
                # record written by the caller with usage=None is stale —
                # the caller updates it when the stream completes.
                _rest_stream.chunks = chunks  # type: ignore[attr-defined]

            resp = ProviderResponse(
                status_code=200,
                body=_rest_stream(),
                headers={"X-Gateway-Streaming": "1"},
                model=served or model,
                usage=None,  # filled by the wrapper at stream end
                latency_ms=0,  # will be set by caller after stream ends
            )
            # Async generators have no __dict__, so the caller cannot read
            # the drained chunks off the body object — attach them to the
            # response instead (the wrapper generator consumes them).
            resp._stream_chunks = chunks  # type: ignore[attr-defined]
            return resp

        status = 502
        data: dict = {}
        try:
            status, data, served = await asyncio.wait_for(
                self._direct_client.forward(model, body, kind=kind),  # type: ignore[attr-defined]
                timeout=effective_timeout,
            )
        except TimeoutError as exc:
            raise ProviderTimeoutError(
                f"upstream provider timed out after {effective_timeout}s"
            ) from exc
        except Exception as exc:
            status_code = int(getattr(exc, "status_code", 502))
            message = str(exc) or "upstream provider error"
            body_detail = getattr(exc, "body", "") or ""
            return ProviderResponse(
                status_code=status_code,
                body=(
                    {"error": {"message": message, "type": "provider_error"}}
                    if not body_detail
                    else {"error": {"message": message, "type": "provider_error", "provider_body": body_detail}}
                ),
                headers={},
                model=model,
                usage=None,
                latency_ms=int((time.perf_counter() - start) * 1000),
            )

        usage: TokenUsage | None = None
        resp_usage = data.get("usage")
        if isinstance(resp_usage, dict):
            usage = TokenUsage(
                prompt_tokens=int(resp_usage.get("prompt_tokens", 0) or 0),
                completion_tokens=int(resp_usage.get("completion_tokens", 0) or 0),
                total_tokens=int(
                    resp_usage.get("total_tokens", 0)
                    or 0
                    or int(resp_usage.get("prompt_tokens", 0) or 0)
                    + int(resp_usage.get("completion_tokens", 0) or 0)
                ),
            )
        return ProviderResponse(
            status_code=status,
            body=data,
            headers={},
            model=served or model,
            usage=usage,
            latency_ms=int((time.perf_counter() - start) * 1000),
        )

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
        self, model: str, body: dict, stream: bool = False, timeout: float | None = None
    ) -> ProviderResponse:
        """Forward to the provider via litellm (stream-aware, timeout-bounded).

        Routes embeddings bodies (``input`` without ``messages``/``prompt``)
        to ``litellm.aembedding``; chat/completions bodies go to
        ``litellm.acompletion``. Stream=true bodies are drained, their usage
        aggregated and the chunks serialized into SSE lines (``data: <json>``
        framing + terminal ``data: [DONE]``) so the HTTP layer can stream
        them instead of crashing on raw chunk objects.

        ``timeout`` overrides the global provider timeout per target (used
        by the UI-managed route loop so a target's ``timeout_seconds`` is
        actually enforced).

        Returns a ProviderResponse carrying usage, latency and the
        provider-shaped body (dict for non-stream, SSE line list for
        stream=true).
        """
        start = time.perf_counter()
        effective_timeout = (
            timeout if timeout is not None else self._settings.provider_timeout
        )
        # Direct provider transport first: when a direct client is attached
        # and knows this model, forward as plain HTTP (no litellm). This is
        # the path for gateway-configured providers (flat model names like
        # ``mimo-v2.5-free`` that litellm cannot resolve).
        if self._direct_client is not None:
            resolved = getattr(self._direct_client, "resolve", None)
            if resolved is not None:
                try:
                    resolved(model)
                except Exception:
                    resolved = None
                if resolved is not None:
                    return await self._forward_direct(
                        model, body, stream, timeout=effective_timeout,
                        request_id=getattr(self, "_current_request_id", None),
                    )

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
        if is_embedding:
            # aembedding has no stream support; a client-sent
            # stream/stream_options (both in _FORWARD_ALLOWLIST) would be
            # forwarded as-is and 502 upstream. Strip them for embeddings.
            kwargs.pop("stream", None)
            kwargs.pop("stream_options", None)
        try:
            if is_embedding:
                response = await asyncio.wait_for(
                    litellm.aembedding(**kwargs),
                    timeout=effective_timeout,
                )
            else:
                response = await asyncio.wait_for(
                    litellm.acompletion(**kwargs),
                    timeout=effective_timeout,
                )
        except TimeoutError as exc:
            raise ProviderTimeoutError(
                f"upstream provider timed out after {effective_timeout}s"
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
                completion_tokens = getattr(resp_usage, "completion_tokens", 0) or 0
                total_tokens = getattr(resp_usage, "total_tokens", 0) or (
                    prompt_tokens + completion_tokens
                )
                usage = TokenUsage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    reasoning_tokens=getattr(resp_usage, "reasoning_tokens", 0) or 0,
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

    async def _drain_stream(self, response: object) -> tuple[list, TokenUsage | None]:
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
                        "total_tokens": (getattr(chunk_usage, "total_tokens", 0) or 0),
                        "reasoning_tokens": (getattr(chunk_usage, "reasoning_tokens", 0) or 0),
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
                    f"upstream stream stalled for {self._settings.provider_timeout}s"
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
    def _collect_stream_usage(chunks: list) -> TokenUsage | None:
        """Aggregate usage from the final usage chunk of a stream.

        The live-streaming path cannot build the UsageRecord until the
        stream ends, so the wrapper generator calls this on [DONE] and the
        record is written after the response is fully streamed.
        """
        for c in reversed(chunks):
            if isinstance(c, dict) and c.get("usage"):
                last = c["usage"]
                return TokenUsage(
                    prompt_tokens=int(last.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(last.get("completion_tokens", 0) or 0),
                    total_tokens=int(last.get("total_tokens", 0) or 0),
                )
        return None

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
        status_code: int | None = None,
        customer_id: str | None = None,
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
                status_code=status_code,
            )
            usage_record.customer_id = customer_id
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

    def _resolve_request_customer(self, body: dict, headers: dict) -> str | None:
        """Resolve the customer id for a request — best effort, never raises."""
        try:
            metadata = body.get("metadata", {})
            if not isinstance(metadata, dict):
                metadata = {}
            client_id = str(metadata.get("client_id", "")) or None
            return self._cost_tracker.resolve_customer_id(
                metadata=metadata, headers=headers, client_id=client_id
            )
        except Exception:
            logger.exception("customer id resolution failed request=%s", body)
            return None

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
