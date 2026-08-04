"""Automatic model fallback: typed chains, cooldowns, context pre-checks (P0-4).

Pre-development stub: the public interface is complete and constructible so
interface tests pass immediately; every behavioral method raises
implemented production behavior with TDD regression coverage.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .budget_enforcement import RateLimitExceededError

if TYPE_CHECKING:  # pragma: no cover
    from .budget_enforcement import CounterStore
    from .gateway_proxy import GatewayProxy, ProviderResponse

#: Trigger classes that make sense to retry on by default.
_DEFAULT_ON = ["rate_limit", "server_error", "timeout"]

#: Fallback context budget when litellm has no max_input_tokens for a model.
_DEFAULT_CONTEXT_BUDGET = 128_000


@dataclass
class FallbackConfig:
    """Ordered fallback chain for one logical model."""

    model: str  # logical model name (matches request body)
    chain: list[str]  # ordered fallback models
    on: list[str] = field(default_factory=lambda: list(_DEFAULT_ON))
    # allowed triggers: "rate_limit" | "server_error" | "timeout"
    #                   | "content_policy" | "context_window"
    cooldown_seconds: int = 60
    disable: bool = False


class FallbackManager:
    """Traverses fallback chains, classifies errors, enforces cooldowns."""

    def __init__(
        self,
        configs: list[FallbackConfig],
        counter_store: CounterStore | None = None,
    ) -> None:
        self.configs = configs
        self.counter_store = counter_store
        self._failed_at: dict[str, float] = {}

    def config_for(self, model: str) -> FallbackConfig | None:
        """Return the fallback config for ``model`` (None when unconfigured)."""
        for cfg in self.configs:
            if cfg.model == model:
                return cfg
        return None

    def chain_for(self, model: str, disable: bool = False) -> list[str]:
        """Return [model] + chain (cooldown-filtered, disable-aware)."""
        config = self.config_for(model)
        if config is None or disable or config.disable:
            return [model]
        chain = [m for m in config.chain if not self.in_cooldown(m)]
        return [model, *chain]

    def classify_error(self, exc: Exception, status_code: int | None = None) -> str:
        """Map an exception to one of: rate_limit/timeout/server_error/
        content_policy/context_window/unknown.
        """
        if isinstance(exc, RateLimitExceededError) or status_code == 429:
            return "rate_limit"
        message = str(exc).lower()
        if (
            isinstance(exc, TimeoutError)
            or "timeout" in message
            or "timed out" in message
        ):
            return "timeout"
        if status_code is not None and 500 <= status_code <= 599:
            return "server_error"
        if (
            "content management policy" in message
            or "content filter" in message
            or "filtered due to" in message
        ):
            return "content_policy"
        if "context length" in message or "maximum context" in message:
            return "context_window"
        return "unknown"

    def should_fallback(self, config: FallbackConfig, error_class: str) -> bool:
        """True only when ``error_class`` is in the config's trigger list."""
        return error_class in config.on

    def mark_failed(self, model: str) -> None:
        """Start a cooldown for the given model (stampede protection)."""
        self._failed_at[model] = time.time()

    def in_cooldown(self, model: str) -> bool:
        """True when ``model`` is inside its cooldown window."""
        failed_at = self._failed_at.get(model)
        if failed_at is None:
            return False
        config = self.config_for(model)
        cooldown = config.cooldown_seconds if config is not None else 60
        if cooldown <= 0:
            return False
        return (time.time() - failed_at) < cooldown

    def estimate_tokens(self, body: dict) -> int:
        """Cheap heuristic estimate: chars/4 plus per-message overhead."""
        total = 0
        messages = body.get("messages")
        if isinstance(messages, list):
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content", "")
                if isinstance(content, str):
                    total += max(1, len(content) // 4)
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            total += max(1, len(part["text"]) // 4)
                total += 4  # role + separators overhead
        prompt = body.get("prompt")
        if isinstance(prompt, str):
            total += max(1, len(prompt) // 4)
        inputs = body.get("input")
        if isinstance(inputs, str):
            total += max(1, len(inputs) // 4)
        elif isinstance(inputs, list):
            for item in inputs:
                if isinstance(item, str):
                    total += max(1, len(item) // 4)
        return max(total, 1)

    def context_safe(self, model: str, body: dict) -> bool:
        """True if estimate_tokens(body) fits the model context budget
        (litellm.model_cost max_input_tokens, fallback 128k).
        """
        return self.estimate_tokens(body) <= self._context_budget(model)

    async def dispatch(
        self,
        proxy: GatewayProxy,
        model: str,
        body: dict,
        api_key: str,
        headers: dict,
        disable_fallbacks: bool = False,
    ) -> ProviderResponse:
        """Try the chain in order; on classified error mark_failed(current) and
        move next if should_fallback; return first success; after exhaustion
        re-raise the last exception. Skips models whose context budget cannot
        fit the request pre-call.
        """
        chain = self.chain_for(model, disable=disable_fallbacks)
        last_exc: Exception | None = None
        for candidate in chain:
            if not self.context_safe(candidate, body):
                continue
            try:
                return await proxy.forward(candidate, body)
            except Exception as exc:
                last_exc = exc
                if disable_fallbacks:
                    raise
                config = self.config_for(candidate) or self.config_for(model)
                if config is None:
                    config = FallbackConfig(model=candidate, chain=[])
                if not self.should_fallback(config, self.classify_error(exc)):
                    raise
                self.mark_failed(candidate)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"no fallback candidate available for model {model!r}")

    def _context_budget(self, model: str) -> int:
        try:
            import litellm

            info = litellm.model_cost.get(model)
            if info and info.get("max_input_tokens"):
                return int(info["max_input_tokens"])
        except Exception:
            pass
        return _DEFAULT_CONTEXT_BUDGET
