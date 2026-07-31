"""Budget enforcement: sync TPM/RPM ceilings + async dollar budgets (P0-3).

Pre-development stub: the public interface is complete and constructible so
interface tests pass immediately; every behavioral method raises
``NotImplementedError`` until the developer implements it (TDD RED phase).

Import direction (acyclic): budget_enforcement -> cost_tracking (type-only).
``BudgetScope`` is DEFINED here; cost_tracking.py imports it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # pragma: no cover
    from .cost_tracking import CostTracker, UsageRecord


@dataclass(frozen=True)
class BudgetScope:
    """Hierarchical budget scope: global > team > user > key."""

    kind: str  # "global" | "team" | "user" | "key"
    key: str  # e.g. "key:sk_live_abc", "user:42", "team:eng", "global:default"

    def scope_key(self) -> str:
        """Return the canonical ``f"{kind}:{key}"`` scope identifier."""
        raise NotImplementedError


@dataclass
class BudgetConfig:
    """Per-scope budget configuration."""

    scope: BudgetScope
    soft_limit: float | None = None  # USD; alert only, never blocks
    hard_limit: float | None = None  # USD; reject with 412 when exceeded
    window: str = "30d"  # "30s" | "30m" | "30h" | "30d" | "daily" | "monthly"
    tpm_limit: int | None = None  # tokens per minute (sync ceiling, 429)
    rpm_limit: int | None = None  # requests per minute (sync ceiling, 429)


class BudgetExceededError(Exception):
    """Hard dollar budget exceeded -> HTTP 412 (Portkey convention)."""

    def __init__(self, scope: BudgetScope, spend: float, limit: float) -> None:
        self.scope = scope
        self.spend = spend
        self.limit = limit
        super().__init__(
            f"budget exceeded for {scope.kind}:{scope.key}: {spend} >= {limit}"
        )


class RateLimitExceededError(Exception):
    """Sync TPM/RPM ceiling exceeded -> HTTP 429."""

    def __init__(self, scope: BudgetScope, limit_type: str, limit: int) -> None:
        self.scope = scope
        self.limit_type = limit_type  # "tpm" | "rpm"
        self.limit = limit
        super().__init__(
            f"rate limit exceeded ({limit_type}) for {scope.kind}:{scope.key}: {limit}"
        )


class CounterStore(Protocol):
    """Atomic windowed counter. A Redis impl swaps in for multi-instance (P1)."""

    def increment(self, key: str, amount: int = 1) -> int: ...

    def get(self, key: str) -> int: ...

    def reset(self, key: str) -> None: ...


class InMemoryCounterStore:
    """Thread-safe dict-based CounterStore for v0.1 single-node operation.

    Window buckets are keyed ``f"{scope_key}:{window}:{bucket_epoch}"``.
    """

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        self._lock = Lock()

    def increment(self, key: str, amount: int = 1) -> int:
        raise NotImplementedError

    def get(self, key: str) -> int:
        raise NotImplementedError

    def reset(self, key: str) -> None:
        raise NotImplementedError


class BudgetEnforcer:
    """Sync pre-dispatch TPM/RPM ceilings + async post-response dollar budgets."""

    def __init__(
        self,
        configs: list[BudgetConfig],
        cost_tracker: CostTracker,
        counter_store: CounterStore | None = None,
        now_fn: Callable[[], int] | None = None,
    ) -> None:
        self.configs = configs
        self.cost_tracker = cost_tracker
        self.counter_store = counter_store
        self._now_fn = now_fn

    def config_for(self, scope: BudgetScope) -> BudgetConfig | None:
        raise NotImplementedError

    def window_seconds(self, window: str) -> int:
        raise NotImplementedError

    def check_sync(
        self, scopes: list[BudgetScope], model: str, est_input_tokens: int
    ) -> None:
        """Increment TPM/RPM counters; raise RateLimitExceededError on ceiling hit."""
        raise NotImplementedError

    async def check_hard(self, scopes: list[BudgetScope]) -> None:
        """Raise BudgetExceededError for any scope over its hard limit."""
        raise NotImplementedError

    def soft_exceeded(self, scopes: list[BudgetScope]) -> list[BudgetScope]:
        """Return scopes past their soft limit; never raises."""
        raise NotImplementedError

    async def reconcile(self, usage: UsageRecord) -> None:
        """Async dollar accounting after a response."""
        raise NotImplementedError


def load_budget_configs(path: str | Path) -> list[BudgetConfig]:
    """Load budget configs from YAML (shape per examples/budgets.example.yaml).

    Contract: malformed YAML or an unknown scope kind raises ValueError.
    """
    raise NotImplementedError
