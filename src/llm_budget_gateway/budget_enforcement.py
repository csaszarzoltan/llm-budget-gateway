"""Budget enforcement: sync TPM/RPM ceilings + async dollar budgets (P0-3).

Pre-development stub: the public interface is complete and constructible so
interface tests pass immediately; every behavioral method raises
``NotImplementedError`` until the developer implements it (TDD RED phase).

Import direction (acyclic): budget_enforcement -> cost_tracking (type-only).
``BudgetScope`` is DEFINED here; cost_tracking.py imports it.
"""

from __future__ import annotations

import calendar
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Protocol

import yaml

if TYPE_CHECKING:  # pragma: no cover
    from .cost_tracking import CostTracker, UsageRecord

_SCOPE_KINDS = ("global", "team", "user", "key")


@dataclass(frozen=True)
class BudgetScope:
    """Hierarchical budget scope: global > team > user > key."""

    kind: str  # "global" | "team" | "user" | "key"
    key: str  # e.g. "key:sk_live_abc", "user:42", "team:eng", "global:default"

    def scope_key(self) -> str:
        """Return the canonical ``f"{kind}:{key}"`` scope identifier."""
        return f"{self.kind}:{self.key}"


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
        """Atomically add ``amount`` to ``key`` and return the new value."""
        with self._lock:
            value = self._counters.get(key, 0) + amount
            self._counters[key] = value
            return value

    def get(self, key: str) -> int:
        """Return the current value for ``key`` (0 when absent)."""
        with self._lock:
            return self._counters.get(key, 0)

    def reset(self, key: str) -> None:
        """Remove ``key`` so it reads back as zero."""
        with self._lock:
            self._counters.pop(key, None)


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
        self._now_fn = now_fn if now_fn is not None else (lambda: int(time.time()))

    def config_for(self, scope: BudgetScope) -> BudgetConfig | None:
        """Return the config whose scope matches ``scope`` (by scope_key)."""
        target = scope.scope_key()
        for cfg in self.configs:
            if cfg.scope.scope_key() == target:
                return cfg
        return None

    def window_seconds(self, window: str) -> int:
        """Map a window string to seconds ("monthly" = current calendar month)."""
        if window == "daily":
            return 86_400
        if window == "monthly":
            now = int(self._now_fn())
            year, month = time.gmtime(now)[:2]
            return calendar.monthrange(year, month)[1] * 86_400
        if len(window) < 2 or window[-1] not in "smhd":
            raise ValueError(f"unknown budget window: {window!r}")
        amount = int(window[:-1])
        seconds = {"s": 1, "m": 60, "h": 3600, "d": 86_400}[window[-1]]
        return amount * seconds

    def check_sync(
        self, scopes: list[BudgetScope], model: str, est_input_tokens: int
    ) -> None:
        """Increment TPM/RPM counters; raise RateLimitExceededError on ceiling hit."""
        if self.counter_store is None:
            return
        now = int(self._now_fn())
        for scope in scopes:
            cfg = self.config_for(scope)
            if cfg is None:
                continue
            window_sec = self.window_seconds(cfg.window)
            bucket = (now // window_sec) * window_sec
            base = f"{scope.scope_key()}:{cfg.window}:{bucket}"
            if cfg.tpm_limit is not None:
                tpm = self.counter_store.increment(f"{base}:tpm", est_input_tokens)
                if tpm > cfg.tpm_limit:
                    raise RateLimitExceededError(scope, "tpm", cfg.tpm_limit)
            if cfg.rpm_limit is not None:
                rpm = self.counter_store.increment(f"{base}:rpm", 1)
                if rpm > cfg.rpm_limit:
                    raise RateLimitExceededError(scope, "rpm", cfg.rpm_limit)

    async def check_hard(self, scopes: list[BudgetScope]) -> None:
        """Raise BudgetExceededError for any scope over its hard limit."""
        if self.cost_tracker is None:
            return
        now = int(self._now_fn())
        for scope in scopes:
            cfg = self.config_for(scope)
            if cfg is None or cfg.hard_limit is None:
                continue
            since = now - self.window_seconds(cfg.window)
            spend = await self.cost_tracker.spend_since(scope.scope_key(), since)
            if spend >= cfg.hard_limit:
                raise BudgetExceededError(scope, spend, cfg.hard_limit)

    def soft_exceeded(self, scopes: list[BudgetScope]) -> list[BudgetScope]:
        """Return scopes past their soft limit; never raises."""
        exceeded: list[BudgetScope] = []
        if self.cost_tracker is None:
            return exceeded
        for scope in scopes:
            cfg = self.config_for(scope)
            if cfg is None or cfg.soft_limit is None:
                continue
            if self._sync_spend(scope) >= cfg.soft_limit:
                exceeded.append(scope)
        return exceeded

    async def reconcile(self, usage: UsageRecord) -> None:
        """Async dollar accounting after a response (delegates to tracker)."""
        if self.cost_tracker is None:
            return
        record = getattr(self.cost_tracker, "record", None)
        if record is None:
            return
        result = record(usage)
        if hasattr(result, "__await__"):
            await result

    def _sync_spend(self, scope: BudgetScope) -> float:
        """Best-effort synchronous spend lookup.

        Prefers an in-memory ``spend`` dict on the tracker (test doubles);
        otherwise drives the async tracker on a fresh event loop.
        """
        spend_dict = getattr(self.cost_tracker, "spend", None)
        if isinstance(spend_dict, dict):
            return float(spend_dict.get(scope.scope_key(), 0.0))
        # asyncio.run requires no running loop; soft_exceeded is a sync API.
        import asyncio

        return asyncio.run(self.cost_tracker.spend_since(scope.scope_key(), 0))


def load_budget_configs(path: str | Path) -> list[BudgetConfig]:
    """Load budget configs from YAML (shape per examples/budgets.example.yaml).

    Contract: malformed YAML or an unknown scope kind raises ValueError;
    a missing file raises FileNotFoundError.
    """
    with open(path) as f:
        try:
            data = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            raise ValueError(f"malformed budget config {path}: {exc}") from exc
    if not isinstance(data, dict) or "scopes" not in data:
        raise ValueError(f"budget config {path} must contain a 'scopes' list")
    configs: list[BudgetConfig] = []
    for entry in data["scopes"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("scope"), dict):
            raise ValueError(
                f"budget config {path}: each scope entry needs a 'scope' map"
            )
        scope_data = entry["scope"]
        kind = scope_data.get("kind")
        key = scope_data.get("key")
        if kind not in _SCOPE_KINDS:
            raise ValueError(f"unknown scope kind: {kind!r}")
        configs.append(
            BudgetConfig(
                scope=BudgetScope(kind=kind, key=key),
                soft_limit=entry.get("soft_limit"),
                hard_limit=entry.get("hard_limit"),
                window=entry.get("window", "30d"),
                tpm_limit=entry.get("tpm_limit"),
                rpm_limit=entry.get("rpm_limit"),
            )
        )
    return configs
