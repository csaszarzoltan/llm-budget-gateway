"""Cost tracking: per-request token x price math and SQLite (WAL) ledger.

Placeholder stub for the TDD RED phase — behavioral methods raise
NotImplementedError until implemented (P0-2). Interface is normative per
analysis brief §4 P0-2.
"""

from __future__ import annotations

from dataclasses import dataclass

from .budget_enforcement import BudgetScope


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
    raise NotImplementedError


class PriceMap:
    """LiteLLM model_cost baseline + Settings.pricing_overrides. Overrides win."""

    def __init__(self, overrides: dict[str, ModelPrice] | None = None) -> None:
        self._overrides: dict[str, ModelPrice] = overrides or {}

    def get_price(self, model: str) -> ModelPrice:
        raise NotImplementedError

    def add_override(self, model: str, price: ModelPrice) -> None:
        raise NotImplementedError


class CostCalculator:
    """Pure function: tokens x price / 1e6."""

    def __init__(self, price_map: PriceMap) -> None:
        self._price_map = price_map

    def calculate(
        self, model: str, prompt_tokens: int, completion_tokens: int
    ) -> tuple[float, float, float]:
        # returns (input_cost, output_cost, total_cost)
        raise NotImplementedError


class CostStore:
    """SQLite ledger, WAL mode. Table cost_records with indexes on
    (timestamp), (api_key, timestamp).
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def insert(self, record: UsageRecord) -> None:
        raise NotImplementedError

    def spend_since(self, scope_key: str, since_epoch: int) -> float:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class CostTracker:
    """Async facade over CostStore + CostCalculator."""

    def __init__(self, store: CostStore, calculator: CostCalculator) -> None:
        self._store = store
        self._calculator = calculator

    async def record(self, usage: UsageRecord) -> None:
        raise NotImplementedError

    async def spend_since(self, scope_key: str, since_epoch: int) -> float:
        raise NotImplementedError

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
        raise NotImplementedError
