"""Budget enforcement: sync TPM/RPM ceilings + async dollar budgets (P0-3).

Pre-development stub: the public interface is complete and constructible so
interface tests pass immediately; every behavioral method raises
``NotImplementedError`` until the developer implements it (TDD RED phase).

Import direction (acyclic): budget_enforcement -> cost_tracking (type-only).
``BudgetScope`` is DEFINED here; cost_tracking.py imports it.
"""

from __future__ import annotations

import asyncio
import calendar
import math
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Protocol

import yaml

if TYPE_CHECKING:  # pragma: no cover
    from .cost_tracking import CostTracker, UsageRecord

_SCOPE_KINDS = ("global", "team", "user", "key")

#: Bounded-cache cap for window-bucket counters. Oldest buckets are evicted
#: past this size so long uptime does not grow memory without bound (review
#: minor: InMemoryCounterStore keys were never pruned).
_MAX_COUNTER_BUCKETS = 10_000

#: Seconds an in-flight hold lives before it is treated as abandoned. A request
#: that never reaches its release (process crash, lost task) must not block the
#: budget forever, so holds expire on their own.
DEFAULT_HOLD_TTL_SECONDS = 900

#: Output-token allowance held for a request that declares no max_tokens.
DEFAULT_HOLD_OUTPUT_TOKENS = 4096


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


@dataclass
class BudgetHold:
    """An in-flight claim on one scope's hard limit, released after settlement."""

    hold_id: int
    scope_key: str
    amount: float
    expires_at: int


@dataclass
class BudgetReservation:
    """The holds taken for one request. ``release`` is idempotent."""

    holds: list[BudgetHold] = field(default_factory=list)
    released: bool = False


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

    Window buckets are keyed ``f"{scope_key}:{window}:{bucket_epoch}"``. The
    backing map is an LRU-bounded OrderedDict: past ``_MAX_COUNTER_BUCKETS``
    the oldest bucket is evicted on write, bounding memory on long uptime.
    """

    def __init__(self) -> None:
        self._counters: OrderedDict[str, int] = OrderedDict()
        self._lock = Lock()

    def increment(self, key: str, amount: int = 1) -> int:
        """Atomically add ``amount`` to ``key`` and return the new value."""
        with self._lock:
            if key in self._counters:
                value = self._counters[key] + amount
                self._counters[key] = value
                self._counters.move_to_end(key)
            else:
                value = amount
                self._counters[key] = value
            while len(self._counters) > _MAX_COUNTER_BUCKETS:
                self._counters.popitem(last=False)
            return value

    def get(self, key: str) -> int:
        """Return the current value for ``key`` (0 when absent)."""
        with self._lock:
            return self._counters.get(key, 0)

    def reset(self, key: str) -> None:
        """Remove ``key`` so it reads back as zero."""
        with self._lock:
            self._counters.pop(key, None)


def budget_window_seconds(
    window: str, now_fn: Callable[[], int] | None = None
) -> int:
    """Map a window string to seconds (\"monthly\" = current calendar month).

    Extracted from BudgetEnforcer.window_seconds with identical behavior
    (docs/architecture/mcp-governance.md §9.2): ``daily`` -> 86400,
    ``monthly`` -> the current calendar month, otherwise ``<n><s|m|h|d>``.
    Unknown window -> ValueError.
    """
    if window == "daily":
        return 86_400
    if window == "monthly":
        now = int(now_fn() if now_fn is not None else time.time())
        year, month = time.gmtime(now)[:2]
        return calendar.monthrange(year, month)[1] * 86_400
    if len(window) < 2 or window[-1] not in "smhd":
        raise ValueError(f"unknown budget window: {window!r}")
    amount = int(window[:-1])
    if amount < 1:
        # S12: 0/negative windows would silently disable the ceiling (a 0s
        # spend window never trips the hard limit). Mirror schemas._window_seconds.
        raise ValueError(f"budget window amount must be >= 1: {window!r}")
    seconds = {"s": 1, "m": 60, "h": 3600, "d": 86_400}[window[-1]]
    return amount * seconds


class BudgetEnforcer:
    """Sync pre-dispatch TPM/RPM ceilings + async post-response dollar budgets."""

    def __init__(
        self,
        configs: list[BudgetConfig],
        cost_tracker: CostTracker,
        counter_store: CounterStore | None = None,
        now_fn: Callable[[], int] | None = None,
        hold_ttl_seconds: int = DEFAULT_HOLD_TTL_SECONDS,
    ) -> None:
        self.configs = configs
        self.cost_tracker = cost_tracker
        self.counter_store = counter_store
        self._now_fn = now_fn if now_fn is not None else (lambda: int(time.time()))
        self._last_rate_limit_state: dict[str, dict[str, object]] = {}
        self._hold_ttl_seconds = hold_ttl_seconds
        self._holds: dict[int, BudgetHold] = {}
        self._next_hold_id = 0
        # Serializes check-then-hold so concurrent requests see each other's
        # holds. Created here; asyncio binds it to the running loop on first use.
        self._reserve_lock = asyncio.Lock()

    def config_for(self, scope: BudgetScope) -> BudgetConfig | None:
        """Return the config whose scope matches ``scope`` (by scope_key)."""
        target = scope.scope_key()
        for cfg in self.configs:
            if cfg.scope.scope_key() == target:
                return cfg
        return None

    def window_seconds(self, window: str) -> int:
        """Map a window string to seconds ("monthly" = current calendar month)."""
        return budget_window_seconds(window, self._now_fn)

    def check_sync(
        self, scopes: list[BudgetScope], model: str, est_input_tokens: int
    ) -> None:
        """Increment TPM/RPM counters; raise RateLimitExceededError on ceiling hit."""
        if self.counter_store is None:
            return
        now = int(self._now_fn())
        state: dict[str, dict[str, object]] = {}
        for scope in scopes:
            cfg = self.config_for(scope)
            if cfg is None:
                continue
            window_sec = self.window_seconds(cfg.window)
            bucket = (now // window_sec) * window_sec
            base = f"{scope.scope_key()}:{cfg.window}:{bucket}"
            remaining: dict[str, object] = {}
            if cfg.tpm_limit is not None:
                tpm = self.counter_store.increment(f"{base}:tpm", est_input_tokens)
                if tpm > cfg.tpm_limit:
                    raise RateLimitExceededError(scope, "tpm", cfg.tpm_limit)
                remaining["tpm_remaining"] = max(0, cfg.tpm_limit - tpm)
            if cfg.rpm_limit is not None:
                rpm = self.counter_store.increment(f"{base}:rpm", 1)
                if rpm > cfg.rpm_limit:
                    raise RateLimitExceededError(scope, "rpm", cfg.rpm_limit)
                remaining["rpm_remaining"] = max(0, cfg.rpm_limit - rpm)
            if remaining:
                remaining["reset_at"] = bucket + window_sec
                state[scope.scope_key()] = remaining
        # Expose the latest limits so the request path can attach standard
        # X-RateLimit-* headers (client-visible quota).
        self._last_rate_limit_state = state

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

    async def reserve(
        self, scopes: list[BudgetScope], amount: float
    ) -> BudgetReservation:
        """Hold ``amount`` against every hard-limited scope, or refuse.

        Committed spend plus the in-flight holds for each scope must stay within
        its hard limit after the new hold. The check and the hold happen under one
        lock, so a concurrent caller cannot pass on headroom this call is about to
        take. All scopes succeed or none is held. ``amount`` may be ``inf`` for a
        request whose cost cannot be estimated; that fails closed on any capped scope.
        """
        if amount < 0 or math.isnan(amount):
            raise ValueError("reservation amount must be a non-negative number")
        reservation = BudgetReservation()
        if self.cost_tracker is None:
            return reservation
        async with self._reserve_lock:
            now = int(self._now_fn())
            self._expire_holds(now)
            capped: list[BudgetScope] = []
            for scope in scopes:
                cfg = self.config_for(scope)
                if cfg is None or cfg.hard_limit is None:
                    continue
                since = now - self.window_seconds(cfg.window)
                committed = await self.cost_tracker.spend_since(scope.scope_key(), since)
                in_flight = self._held_for(scope.scope_key(), now)
                # ``>=`` on committed + in-flight matches check_hard: a scope
                # already at its cap refuses even a zero-cost request.
                used = committed + in_flight
                if used >= cfg.hard_limit or used + amount > cfg.hard_limit:
                    raise BudgetExceededError(
                        scope, committed + in_flight, cfg.hard_limit
                    )
                capped.append(scope)
            for scope in capped:
                self._next_hold_id += 1
                hold = BudgetHold(
                    hold_id=self._next_hold_id,
                    scope_key=scope.scope_key(),
                    amount=amount,
                    expires_at=now + self._hold_ttl_seconds,
                )
                self._holds[hold.hold_id] = hold
                reservation.holds.append(hold)
        return reservation

    def release(self, reservation: BudgetReservation) -> None:
        """Drop a reservation's holds. Safe to call more than once."""
        if reservation.released:
            return
        for hold in reservation.holds:
            self._holds.pop(hold.hold_id, None)
        reservation.released = True

    def held_amount(self, scope_key: str) -> float:
        """Sum of unexpired in-flight holds on ``scope_key``."""
        return self._held_for(scope_key, int(self._now_fn()))

    def _held_for(self, scope_key: str, now: int) -> float:
        return sum(
            hold.amount
            for hold in self._holds.values()
            if hold.scope_key == scope_key and hold.expires_at > now
        )

    def _expire_holds(self, now: int) -> None:
        for hold_id in [h.hold_id for h in self._holds.values() if h.expires_at <= now]:
            del self._holds[hold_id]

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
        otherwise drives the async tracker on a fresh event loop. When a loop
        is already running (real request path), the coroutine is executed on a
        worker thread's own loop so ``asyncio.run`` never raises RuntimeError.
        """
        spend_dict = getattr(self.cost_tracker, "spend", None)
        if isinstance(spend_dict, dict):
            return float(spend_dict.get(scope.scope_key(), 0.0))
        import asyncio
        import threading

        coro = self.cost_tracker.spend_since(scope.scope_key(), 0)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        # soft_exceeded is a sync API, but we're inside a running loop — run
        # the coroutine on a fresh thread with its own loop.
        result: dict[str, float] = {}

        def _runner() -> None:
            result["spend"] = asyncio.run(coro)

        thread = threading.Thread(target=_runner)
        thread.start()
        thread.join()
        return result["spend"]


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
