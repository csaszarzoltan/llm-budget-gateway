"""Automatic model fallback: typed chains, cooldowns, context pre-checks (P0-4).

Pre-development stub: the public interface is complete and constructible so
interface tests pass immediately; every behavioral method raises
``NotImplementedError`` until the developer implements it (TDD RED phase).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .budget_enforcement import CounterStore
    from .gateway_proxy import GatewayProxy, ProviderResponse


@dataclass
class FallbackConfig:
    """Ordered fallback chain for one logical model."""

    model: str  # logical model name (matches request body)
    chain: list[str]  # ordered fallback models
    on: list[str] = field(
        default_factory=lambda: ["rate_limit", "server_error", "timeout"]
    )
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

    def config_for(self, model: str) -> FallbackConfig | None:
        raise NotImplementedError

    def chain_for(self, model: str, disable: bool = False) -> list[str]:
        """Return [model] + chain (cooldown-filtered, disable-aware)."""
        raise NotImplementedError

    def classify_error(self, exc: Exception, status_code: int | None = None) -> str:
        """Map an exception to one of: rate_limit/timeout/server_error/
        content_policy/context_window/unknown."""
        raise NotImplementedError

    def should_fallback(self, config: FallbackConfig, error_class: str) -> bool:
        raise NotImplementedError

    def mark_failed(self, model: str) -> None:
        """Start a cooldown for the given model (stampede protection)."""
        raise NotImplementedError

    def in_cooldown(self, model: str) -> bool:
        raise NotImplementedError

    def estimate_tokens(self, body: dict) -> int:
        """Cheap heuristic estimate; litellm.token_counter when available."""
        raise NotImplementedError

    def context_safe(self, model: str, body: dict) -> bool:
        """True if estimate_tokens(body) fits the model context budget
        (litellm.model_cost max_input_tokens, fallback 128k)."""
        raise NotImplementedError

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
        fit the request pre-call."""
        raise NotImplementedError
