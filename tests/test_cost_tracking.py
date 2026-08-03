"""Pre-development tests for the cost tracking module (P0-2).

Interface tests (imports, dataclass shape, signatures, type hints) PASS
immediately on the stub. Behavioral tests (cost math, override precedence,
SQLite WAL persistence, spend_since windowing, streaming aggregation) FAIL
during the RED phase with NotImplementedError and become active once
cost_tracking.py is implemented.

Normative interface: analysis/analysis-brief.md §4 P0-2.
"""

from __future__ import annotations

import inspect
import sqlite3
from dataclasses import fields, is_dataclass

import pytest

from llm_budget_gateway.budget_enforcement import BudgetScope
from llm_budget_gateway.cost_tracking import (
    CostCalculator,
    CostStore,
    CostTracker,
    ModelPrice,
    PriceMap,
    TokenUsage,
    UsageRecord,
    accumulate_usage,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _sample_record(**overrides) -> UsageRecord:
    base: dict[str, object] = dict(
        request_id="req-1",
        api_key="sk_abc",
        user_id=None,
        team=None,
        model="gpt-4o",
        provider="openai",
        prompt_tokens=1000,
        completion_tokens=500,
        total_tokens=1500,
        input_cost=0.005,
        output_cost=0.0075,
        total_cost=0.0125,
        latency_ms=120,
        status="success",
        timestamp=1_700_000_000,
    )
    base.update(overrides)
    return UsageRecord(**base)  # type: ignore[arg-type]


@pytest.fixture
def price_map() -> PriceMap:
    return PriceMap()


@pytest.fixture
def calculator(price_map: PriceMap) -> CostCalculator:
    return CostCalculator(price_map)


@pytest.fixture
def store(tmp_path) -> CostStore:
    return CostStore(str(tmp_path / "ledger.db"))


@pytest.fixture
def tracker(store: CostStore, calculator: CostCalculator) -> CostTracker:
    return CostTracker(store=store, calculator=calculator)


# ---------------------------------------------------------------------------
# Interface tests — pass immediately on the stub
# ---------------------------------------------------------------------------


class TestTokenUsageInterface:
    def test_is_dataclass(self) -> None:
        assert is_dataclass(TokenUsage)

    def test_fields(self) -> None:
        names = {f.name for f in fields(TokenUsage)}
        assert names == {"prompt_tokens", "completion_tokens", "total_tokens"}

    def test_constructible(self) -> None:
        u = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        assert u.total_tokens == 15


class TestModelPriceInterface:
    def test_is_dataclass(self) -> None:
        assert is_dataclass(ModelPrice)

    def test_fields(self) -> None:
        names = {f.name for f in fields(ModelPrice)}
        assert names == {"input_cost_per_million", "output_cost_per_million"}

    def test_constructible(self) -> None:
        p = ModelPrice(input_cost_per_million=3.0, output_cost_per_million=15.0)
        assert p.input_cost_per_million == 3.0


class TestUsageRecordInterface:
    def test_is_dataclass(self) -> None:
        assert is_dataclass(UsageRecord)

    def test_fields(self) -> None:
        names = {f.name for f in fields(UsageRecord)}
        assert names == {
            "request_id",
            "api_key",
            "user_id",
            "team",
            "model",
            "provider",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "input_cost",
            "output_cost",
            "total_cost",
            "latency_ms",
            "status",
            "timestamp",
            "tool_name",
            "project",
        }

    def test_constructible(self) -> None:
        rec = _sample_record()
        assert rec.request_id == "req-1"
        assert rec.status == "success"
        assert rec.user_id is None


class TestAccumulateUsageInterface:
    def test_is_function(self) -> None:
        assert callable(accumulate_usage)

    def test_signature(self) -> None:
        sig = inspect.signature(accumulate_usage)
        assert list(sig.parameters) == ["chunks"]
        assert "list" in str(sig.parameters["chunks"].annotation)
        assert "TokenUsage" in str(sig.return_annotation)


class TestPriceMapInterface:
    def test_init_signature(self) -> None:
        sig = inspect.signature(PriceMap.__init__)
        params = list(sig.parameters)
        assert params == ["self", "overrides"]
        assert sig.parameters["overrides"].default is None

    def test_constructible_without_args(self) -> None:
        pm = PriceMap()
        assert pm is not None

    def test_constructible_with_overrides(self) -> None:
        pm = PriceMap(overrides={"gpt-4o": ModelPrice(3.0, 15.0)})
        assert pm is not None

    def test_get_price_signature(self) -> None:
        sig = inspect.signature(PriceMap.get_price)
        assert list(sig.parameters) == ["self", "model"]
        assert "ModelPrice" in str(sig.return_annotation)

    def test_add_override_signature(self) -> None:
        sig = inspect.signature(PriceMap.add_override)
        assert list(sig.parameters) == ["self", "model", "price"]


class TestCostCalculatorInterface:
    def test_init_signature(self) -> None:
        sig = inspect.signature(CostCalculator.__init__)
        assert list(sig.parameters) == ["self", "price_map"]

    def test_calculate_signature(self) -> None:
        sig = inspect.signature(CostCalculator.calculate)
        params = list(sig.parameters)
        assert params == ["self", "model", "prompt_tokens", "completion_tokens"]
        assert "tuple" in str(sig.return_annotation)


class TestCostStoreInterface:
    def test_init_signature(self) -> None:
        sig = inspect.signature(CostStore.__init__)
        assert list(sig.parameters) == ["self", "db_path"]

    def test_insert_signature(self) -> None:
        sig = inspect.signature(CostStore.insert)
        assert list(sig.parameters) == ["self", "record"]

    def test_spend_since_signature(self) -> None:
        sig = inspect.signature(CostStore.spend_since)
        params = list(sig.parameters)
        assert params == ["self", "scope_key", "since_epoch", "tool_name"]
        assert sig.parameters["tool_name"].default is None
        assert str(sig.return_annotation) == "float"

    def test_close_signature(self) -> None:
        sig = inspect.signature(CostStore.close)
        assert list(sig.parameters) == ["self"]


class TestCostTrackerInterface:
    def test_init_signature(self) -> None:
        sig = inspect.signature(CostTracker.__init__)
        assert list(sig.parameters) == ["self", "store", "calculator"]

    def test_record_is_async(self) -> None:
        assert inspect.iscoroutinefunction(CostTracker.record)

    def test_record_signature(self) -> None:
        sig = inspect.signature(CostTracker.record)
        assert list(sig.parameters) == ["self", "usage"]

    def test_spend_since_is_async(self) -> None:
        assert inspect.iscoroutinefunction(CostTracker.spend_since)

    def test_spend_since_signature(self) -> None:
        sig = inspect.signature(CostTracker.spend_since)
        assert list(sig.parameters) == ["self", "scope_key", "since_epoch", "tool_name"]
        assert sig.parameters["tool_name"].default is None
        assert str(sig.return_annotation) == "float"

    def test_build_record_signature(self) -> None:
        sig = inspect.signature(CostTracker.build_record)
        params = list(sig.parameters)
        assert params == [
            "self",
            "request_id",
            "scope",
            "model",
            "provider",
            "usage",
            "latency_ms",
            "status",
        ]
        for name in (
            "request_id",
            "scope",
            "model",
            "provider",
            "usage",
            "latency_ms",
            "status",
        ):
            assert sig.parameters[name].kind == inspect.Parameter.KEYWORD_ONLY
        assert "UsageRecord" in str(sig.return_annotation)


# ---------------------------------------------------------------------------
# Behavioral tests — FAIL with NotImplementedError during RED phase
# ---------------------------------------------------------------------------


class TestCostMathBehavior:
    @pytest.mark.parametrize(
        ("model", "prompt_tokens", "completion_tokens"),
        [
            ("gpt-4o", 1000, 500),
            ("gpt-4o", 0, 0),
            ("gpt-3.5-turbo", 2000, 1000),
        ],
    )
    def test_calculate_matches_litellm_baseline(
        self,
        calculator: CostCalculator,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        import litellm

        price = litellm.model_cost[model]
        expected_input = prompt_tokens * price["input_cost_per_token"]
        expected_output = completion_tokens * price["output_cost_per_token"]
        input_cost, output_cost, total_cost = calculator.calculate(
            model, prompt_tokens, completion_tokens
        )
        assert input_cost == pytest.approx(expected_input, abs=1e-9)
        assert output_cost == pytest.approx(expected_output, abs=1e-9)
        assert total_cost == pytest.approx(expected_input + expected_output, abs=1e-9)

    def test_calculate_override_wins(self, price_map: PriceMap) -> None:
        price_map.add_override(
            "gpt-4o",
            ModelPrice(input_cost_per_million=100.0, output_cost_per_million=200.0),
        )
        calculator = CostCalculator(price_map)
        input_cost, output_cost, total = calculator.calculate("gpt-4o", 1000, 500)
        assert input_cost == pytest.approx(1000 * 100.0 / 1e6, abs=1e-9)
        assert output_cost == pytest.approx(500 * 200.0 / 1e6, abs=1e-9)
        assert total == pytest.approx(input_cost + output_cost, abs=1e-9)

    def test_unknown_model_returns_zero_price(self, price_map: PriceMap) -> None:
        price = price_map.get_price("no-such-model-xyz")
        assert isinstance(price, ModelPrice)
        assert price.input_cost_per_million == 0.0
        assert price.output_cost_per_million == 0.0

    def test_add_override_then_get_price(self, price_map: PriceMap) -> None:
        price_map.add_override("self-hosted-llama", ModelPrice(0.5, 0.5))
        price = price_map.get_price("self-hosted-llama")
        assert price.input_cost_per_million == 0.5


class TestCostStoreBehavior:
    def test_insert_then_spend_since(self, store: CostStore) -> None:
        store.insert(_sample_record(total_cost=1.0))
        store.insert(_sample_record(request_id="req-2", total_cost=2.0))
        total = store.spend_since("key:sk_abc", since_epoch=1_699_999_999)
        assert total == pytest.approx(3.0, abs=1e-9)

    def test_spend_since_respects_window(self, store: CostStore) -> None:
        store.insert(_sample_record(total_cost=1.0, timestamp=1_700_000_000))
        store.insert(
            _sample_record(request_id="req-2", total_cost=2.0, timestamp=1_700_000_100)
        )
        old = store.spend_since("key:sk_abc", since_epoch=1_700_000_050)
        assert old == pytest.approx(2.0, abs=1e-9)

    def test_spend_since_ignores_other_scopes(self, store: CostStore) -> None:
        store.insert(_sample_record(total_cost=1.0))  # api_key sk_abc
        store.insert(
            _sample_record(request_id="req-2", api_key="sk_other", total_cost=99.0)
        )
        total = store.spend_since("key:sk_abc", since_epoch=0)
        assert total == pytest.approx(1.0, abs=1e-9)

    def test_spend_since_filters_by_tool_name(self, store: CostStore) -> None:
        store.insert(_sample_record(total_cost=1.0, tool_name="srv1:t1"))
        store.insert(
            _sample_record(request_id="req-2", total_cost=2.0, tool_name="srv1:t2")
        )
        store.insert(_sample_record(request_id="req-3", total_cost=4.0))  # no tool
        assert store.spend_since("key:sk_abc", 0) == pytest.approx(7.0, abs=1e-9)
        assert store.spend_since(
            "key:sk_abc", 0, tool_name="srv1:t1"
        ) == pytest.approx(1.0, abs=1e-9)
        assert store.spend_since(
            "key:sk_abc", 0, tool_name="srv1:t2"
        ) == pytest.approx(2.0, abs=1e-9)
        assert store.spend_since(
            "key:sk_abc", 0, tool_name="nope"
        ) == pytest.approx(0.0, abs=1e-9)

    def test_tool_name_and_project_persist_across_reopen(
        self, store: CostStore, tmp_path
    ) -> None:
        store.insert(_sample_record(total_cost=1.0, tool_name="srv1:t1", project="p1"))
        store.close()
        reopened = CostStore(str(tmp_path / "ledger.db"))
        try:
            assert reopened.spend_since(
                "key:sk_abc", 0, tool_name="srv1:t1"
            ) == pytest.approx(1.0, abs=1e-9)
            conn = sqlite3.connect(str(tmp_path / "ledger.db"))
            try:
                row = conn.execute(
                    "SELECT tool_name, project FROM cost_records "
                    "WHERE request_id = ?",
                    ("req-1",),
                ).fetchone()
                assert row == ("srv1:t1", "p1")
            finally:
                conn.close()
        finally:
            reopened.close()

    def test_wal_mode_active(self, store: CostStore, tmp_path) -> None:
        store.close()
        conn = sqlite3.connect(str(tmp_path / "ledger.db"))
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal"
        finally:
            conn.close()

    def test_records_persist_across_reopen(self, store: CostStore, tmp_path) -> None:
        store.insert(_sample_record(total_cost=4.0))
        store.close()
        reopened = CostStore(str(tmp_path / "ledger.db"))
        try:
            total = reopened.spend_since("key:sk_abc", since_epoch=0)
            assert total == pytest.approx(4.0, abs=1e-9)
        finally:
            reopened.close()


class TestCostTrackerBehavior:
    @pytest.mark.asyncio
    async def test_record_persists_via_store(
        self, tracker: CostTracker, store: CostStore
    ) -> None:
        await tracker.record(_sample_record(total_cost=1.5))
        total = await tracker.spend_since("key:sk_abc", since_epoch=0)
        assert total == pytest.approx(1.5, abs=1e-9)

    @pytest.mark.asyncio
    async def test_spend_since_delegates_to_store(
        self, tracker: CostTracker, store: CostStore
    ) -> None:
        store.insert(_sample_record(total_cost=2.5))
        total = await tracker.spend_since("key:sk_abc", since_epoch=0)
        assert total == pytest.approx(2.5, abs=1e-9)

    @pytest.mark.asyncio
    async def test_spend_since_delegates_tool_name_to_store(
        self, tracker: CostTracker, store: CostStore
    ) -> None:
        store.insert(_sample_record(total_cost=1.0, tool_name="srv1:t1"))
        store.insert(
            _sample_record(request_id="req-2", total_cost=2.0, tool_name="srv1:t2")
        )
        total = await tracker.spend_since("key:sk_abc", 0, tool_name="srv1:t2")
        assert total == pytest.approx(2.0, abs=1e-9)

    def test_build_record_computes_cost(
        self, tracker: CostTracker, price_map: PriceMap
    ) -> None:
        price_map.add_override(
            "gpt-4o",
            ModelPrice(input_cost_per_million=100.0, output_cost_per_million=200.0),
        )
        record = tracker.build_record(
            request_id="req-x",
            scope=BudgetScope(kind="key", key="sk_abc"),
            model="gpt-4o",
            provider="openai",
            usage=TokenUsage(
                prompt_tokens=1000, completion_tokens=500, total_tokens=1500
            ),
            latency_ms=80,
            status="success",
        )
        assert record.input_cost == pytest.approx(1000 * 100.0 / 1e6, abs=1e-9)
        assert record.output_cost == pytest.approx(500 * 200.0 / 1e6, abs=1e-9)
        assert record.total_cost == pytest.approx(
            record.input_cost + record.output_cost, abs=1e-9
        )
        assert record.status == "success"
        assert record.latency_ms == 80

    def test_build_record_maps_scope_fields(self, tracker: CostTracker) -> None:
        record = tracker.build_record(
            request_id="req-y",
            scope=BudgetScope(kind="user", key="42"),
            model="gpt-4o",
            provider="openai",
            usage=None,
            latency_ms=0,
            status="error",
        )
        assert record.user_id == "42"
        assert record.status == "error"
        assert record.total_cost == 0.0

    def test_build_record_no_usage_zero_cost(self, tracker: CostTracker) -> None:
        record = tracker.build_record(
            request_id="req-z",
            scope=BudgetScope(kind="team", key="eng"),
            model="gpt-4o",
            provider="openai",
            usage=None,
            latency_ms=12,
            status="error",
        )
        assert record.total_tokens == 0
        assert record.total_cost == 0.0


class TestAccumulateUsageBehavior:
    def test_accumulates_chunks(self) -> None:
        chunks = [
            {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            {"prompt_tokens": 0, "completion_tokens": 3, "total_tokens": 3},
            {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
        ]
        usage = accumulate_usage(chunks)
        assert isinstance(usage, TokenUsage)
        assert usage.prompt_tokens == 15
        assert usage.completion_tokens == 6
        assert usage.total_tokens == 21

    def test_empty_chunks_zero_usage(self) -> None:
        usage = accumulate_usage([])
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0
        assert usage.total_tokens == 0
