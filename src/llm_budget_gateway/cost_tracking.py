"""Cost tracking: per-request token x price math and SQLite (WAL) ledger.

Placeholder stub for the TDD RED phase — behavioral methods raise
NotImplementedError until implemented (P0-2). Interface is normative per
analysis brief §4 P0-2.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from dataclasses import dataclass

from .budget_enforcement import BudgetScope

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS cost_records (
    request_id TEXT PRIMARY KEY,
    api_key TEXT NOT NULL,
    user_id TEXT,
    team TEXT,
    model TEXT NOT NULL,
    provider TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    input_cost REAL NOT NULL,
    output_cost REAL NOT NULL,
    total_cost REAL NOT NULL,
    latency_ms INTEGER NOT NULL,
    status TEXT NOT NULL,
    timestamp INTEGER NOT NULL
)
"""


@dataclass
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass
class ModelPrice:
    input_cost_per_million: float
    output_cost_per_million: float


@dataclass
class UsageRecord:
    request_id: str
    api_key: str
    user_id: str | None
    team: str | None
    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    input_cost: float
    output_cost: float
    total_cost: float
    latency_ms: int
    status: str  # "success" | "error" | "fallback"
    timestamp: int  # epoch seconds


def accumulate_usage(chunks: list[dict]) -> TokenUsage:
    """Aggregate partial usage chunk dicts into one TokenUsage record."""
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    for chunk in chunks:
        prompt_tokens += int(chunk.get("prompt_tokens", 0) or 0)
        completion_tokens += int(chunk.get("completion_tokens", 0) or 0)
        total_tokens += int(chunk.get("total_tokens", 0) or 0)
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


class PriceMap:
    """LiteLLM model_cost baseline + Settings.pricing_overrides. Overrides win."""

    def __init__(self, overrides: dict[str, ModelPrice] | None = None) -> None:
        self._overrides: dict[str, ModelPrice] = dict(overrides or {})

    def get_price(self, model: str) -> ModelPrice:
        """Return the effective price for ``model`` (override first, then
        litellm.model_cost; unknown models price at zero without raising).
        """
        if model in self._overrides:
            return self._overrides[model]
        try:
            import litellm

            info = litellm.model_cost.get(model)
        except Exception:
            info = None
        if not info:
            return ModelPrice(0.0, 0.0)
        input_per_token = info.get("input_cost_per_token")
        output_per_token = info.get("output_cost_per_token")
        if input_per_token is None or output_per_token is None:
            return ModelPrice(0.0, 0.0)
        return ModelPrice(
            input_cost_per_million=float(input_per_token) * 1e6,
            output_cost_per_million=float(output_per_token) * 1e6,
        )

    def add_override(self, model: str, price: ModelPrice) -> None:
        """Register a manual price that beats the litellm baseline."""
        self._overrides[model] = price


class CostCalculator:
    """Pure function: tokens x price / 1e6."""

    def __init__(self, price_map: PriceMap) -> None:
        self._price_map = price_map

    def calculate(
        self, model: str, prompt_tokens: int, completion_tokens: int
    ) -> tuple[float, float, float]:
        """Return ``(input_cost, output_cost, total_cost)`` for the usage."""
        price = self._price_map.get_price(model)
        input_cost = prompt_tokens * price.input_cost_per_million / 1e6
        output_cost = completion_tokens * price.output_cost_per_million / 1e6
        return input_cost, output_cost, input_cost + output_cost


class CostStore:
    """SQLite ledger, WAL mode. Table cost_records with indexes on
    (timestamp), (api_key, timestamp).

    The connection is created with ``check_same_thread=False`` and every
    operation is serialized under ``self._lock`` so the store can safely be
    driven from worker threads (see ``CostTracker`` which offloads sync I/O
    off the event loop).
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(_CREATE_TABLE)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cost_records_timestamp "
                "ON cost_records (timestamp)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cost_records_api_key_timestamp "
                "ON cost_records (api_key, timestamp)"
            )
            self._conn.commit()

    def insert(self, record: UsageRecord) -> None:
        """Persist one usage record (upsert on request_id)."""
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO cost_records (
                    request_id, api_key, user_id, team, model, provider,
                    prompt_tokens, completion_tokens, total_tokens,
                    input_cost, output_cost, total_cost,
                    latency_ms, status, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.request_id,
                    record.api_key,
                    record.user_id,
                    record.team,
                    record.model,
                    record.provider,
                    record.prompt_tokens,
                    record.completion_tokens,
                    record.total_tokens,
                    record.input_cost,
                    record.output_cost,
                    record.total_cost,
                    record.latency_ms,
                    record.status,
                    record.timestamp,
                ),
            )
            self._conn.commit()

    def spend_since(self, scope_key: str, since_epoch: int) -> float:
        """Sum total_cost for records matching ``scope_key`` with
        timestamp >= since_epoch.
        """
        kind, _, key = scope_key.partition(":")
        with self._lock:
            if kind == "global":
                row = self._conn.execute(
                    "SELECT COALESCE(SUM(total_cost), 0) FROM cost_records "
                    "WHERE timestamp >= ?",
                    (since_epoch,),
                ).fetchone()
                return float(row[0])
            column = {"key": "api_key", "user": "user_id", "team": "team"}.get(
                kind
            )
            if column is None:
                raise ValueError(f"unknown scope kind: {kind!r}")
            row = self._conn.execute(
                f"SELECT COALESCE(SUM(total_cost), 0) FROM cost_records "
                f"WHERE {column} = ? AND timestamp >= ?",
                (key, since_epoch),
            ).fetchone()
            return float(row[0])

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            self._conn.close()


class CostTracker:
    """Async facade over CostStore + CostCalculator."""

    def __init__(self, store: CostStore, calculator: CostCalculator) -> None:
        self._store = store
        self._calculator = calculator

    async def record(self, usage: UsageRecord) -> None:
        """Persist a usage record (off the event loop)."""
        await asyncio.to_thread(self._store.insert, usage)

    async def spend_since(self, scope_key: str, since_epoch: int) -> float:
        """Return total spend for ``scope_key`` in the window (off the event
        loop)."""
        return await asyncio.to_thread(
            self._store.spend_since, scope_key, since_epoch
        )

    def build_record(
        self,
        *,
        request_id: str,
        scope: BudgetScope,
        model: str,
        provider: str,
        usage: TokenUsage | None,
        latency_ms: int,
        status: str,
    ) -> UsageRecord:
        """Assemble a UsageRecord, computing costs from usage (zero when None)."""
        if usage is not None:
            input_cost, output_cost, total_cost = self._calculator.calculate(
                model, usage.prompt_tokens, usage.completion_tokens
            )
            prompt_tokens = usage.prompt_tokens
            completion_tokens = usage.completion_tokens
            total_tokens = usage.total_tokens
        else:
            input_cost = output_cost = total_cost = 0.0
            prompt_tokens = completion_tokens = total_tokens = 0
        return UsageRecord(
            request_id=request_id,
            api_key=scope.key if scope.kind == "key" else "",
            user_id=scope.key if scope.kind == "user" else None,
            team=scope.key if scope.kind == "team" else None,
            model=model,
            provider=provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            input_cost=input_cost,
            output_cost=output_cost,
            total_cost=total_cost,
            latency_ms=latency_ms,
            status=status,
            timestamp=int(time.time()),
        )
