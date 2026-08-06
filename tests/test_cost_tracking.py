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
import time
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
            "route",
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
        assert list(sig.parameters) == ["self", "db_path", "connection"]

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

    def test_set_model_cooldown_signature(self) -> None:
        sig = inspect.signature(CostStore.set_model_cooldown)
        params = list(sig.parameters)
        assert params == ["self", "route", "model", "seconds", "reason"]
        assert sig.parameters["seconds"].default == 3600

    def test_model_in_cooldown_signature(self) -> None:
        sig = inspect.signature(CostStore.model_in_cooldown)
        assert list(sig.parameters) == ["self", "route", "model"]
        assert str(sig.return_annotation) == "int"


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
            "route",
        ]
        for name in (
            "request_id",
            "scope",
            "model",
            "provider",
            "usage",
            "latency_ms",
            "status",
            "route",
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

    def test_daily_usage_groups_by_day_and_model(self, store: CostStore) -> None:
        import time

        now = int(time.time())
        today = now - (now % 86400)
        yesterday = today - 86400
        store.insert(
            _sample_record(
                request_id="r1",
                model="gpt-4o",
                total_tokens=1500,
                total_cost=0.0125,
                timestamp=today,
                route="hermes-default",
            )
        )
        store.insert(
            _sample_record(
                request_id="r2",
                model="gpt-4o",
                total_tokens=500,
                total_cost=0.004,
                timestamp=yesterday,
                route="hermes-default",
            )
        )
        store.insert(
            _sample_record(
                request_id="r3",
                model="mimo-v2.5",
                total_tokens=300,
                total_cost=0.001,
                timestamp=yesterday,
                route="hermes-planner",
            )
        )
        result = store.daily_usage(days=3)
        days = {d["date"]: d["models"] for d in result["days"]}
        today_key = __import__("datetime").datetime.fromtimestamp(
            today, __import__("datetime").timezone.utc
        ).strftime("%Y-%m-%d")
        yesterday_key = __import__("datetime").datetime.fromtimestamp(
            yesterday, __import__("datetime").timezone.utc
        ).strftime("%Y-%m-%d")
        assert today_key in days
        assert yesterday_key in days
        today_models = {m["model"]: m for m in days[today_key]}
        assert today_models["gpt-4o"]["total_tokens"] == 1500
        y_models = {m["model"]: m for m in days[yesterday_key]}
        assert y_models["gpt-4o"]["total_tokens"] == 500
        assert y_models["mimo-v2.5"]["total_tokens"] == 300
        # raw call list, newest first
        assert result["calls"][0]["request_id"] == "r1"
        assert {c["request_id"] for c in result["calls"]} == {"r1", "r2", "r3"}
        assert all(c["route"] in {"hermes-default", "hermes-planner"} for c in result["calls"])

    def test_daily_usage_filters_calls_by_route(self, store: CostStore) -> None:
        import time

        now = int(time.time())
        store.insert(
            _sample_record(
                request_id="a1",
                total_tokens=100,
                timestamp=now,
                route="hermes-default",
            )
        )
        store.insert(
            _sample_record(
                request_id="a2",
                total_tokens=200,
                timestamp=now,
                route="hermes-planner",
            )
        )
        result = store.daily_usage(days=1, route="hermes-planner")
        assert [c["request_id"] for c in result["calls"]] == ["a2"]
        # day buckets are not route-filtered: both models still appear
        assert len(result["days"]) >= 1

    def test_spend_since_project_scope(self, store: CostStore) -> None:
        """M7: project is a valid scope kind and filters on the project column."""
        store.insert(_sample_record(total_cost=1.0, project="projA"))
        store.insert(
            _sample_record(request_id="req-2", total_cost=99.0, project="projB")
        )
        store.insert(_sample_record(request_id="req-3", total_cost=1000.0))  # no project
        assert store.spend_since("project:projA", 0) == pytest.approx(1.0, abs=1e-9)
        assert store.spend_since("project:projB", 0) == pytest.approx(99.0, abs=1e-9)

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

    def test_cooldown_blocks_until_expiry(self, store: CostStore) -> None:
        store.set_model_cooldown("hermes-default", "@openai/gpt-4o", 60, reason="http_429")
        assert store.model_in_cooldown("hermes-default", "@openai/gpt-4o") > 0
        # route-scoped: other routes are unaffected
        assert store.model_in_cooldown("hermes-planner", "@openai/gpt-4o") == 0
        # other models on the same route are unaffected
        assert store.model_in_cooldown("hermes-default", "@openai/gpt-4o-mini") == 0

    def test_cooldown_expires_and_is_cleaned(self, store: CostStore) -> None:
        store.set_model_cooldown("hermes-default", "@openai/gpt-4o", 1, reason="http_429")
        assert store.model_in_cooldown("hermes-default", "@openai/gpt-4o") >= 0
        # force expiry by writing an until_ts in the past
        store._conn.execute(
            "UPDATE model_cooldowns SET until_ts = ? "
            "WHERE route='hermes-default' AND model='@openai/gpt-4o'",
            (int(time.time()) - 5,),
        )
        store._conn.commit()
        assert store.model_in_cooldown("hermes-default", "@openai/gpt-4o") == 0
        assert store.active_cooldowns() == []

    def test_active_cooldowns_lists_only_live_entries(
        self, store: CostStore
    ) -> None:
        store.set_model_cooldown("r1", "m1", 3600)
        store.set_model_cooldown("r1", "m2", 3600)
        entries = store.active_cooldowns()
        assert {e["model"] for e in entries} == {"m1", "m2"}
        assert all(e["remaining_seconds"] > 0 for e in entries)
        assert all(e["route"] == "r1" for e in entries)


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


def test_usage_by_period_buckets(store: CostStore) -> None:
    """usage_by_period buckets by hour / day / month with token sums."""
    import time as _t

    now = int(_t.time())
    base = {
        "request_id": "r1", "api_key": "k", "user_id": None, "team": None,
        "model": "nemotron", "provider": "p", "prompt_tokens": 100,
        "completion_tokens": 50, "total_tokens": 150, "input_cost": 0.0,
        "output_cost": 0.0, "total_cost": 0.001, "latency_ms": 10,
        "status": "success", "timestamp": now, "route": "hermes-default",
    }
    for i in range(3):
        store.insert(UsageRecord(**{**base, "request_id": f"r{i}", "timestamp": now - i * 3600}))
    day = store.usage_by_period(period="day", days=1)
    assert day["days"], "expected at least one day bucket"
    total = sum(
        m["total_tokens"] for b in day["days"] for m in b["models"]
    )
    assert total == 450
    hourly = store.usage_by_period(period="hour", days=24)
    assert len(hourly["days"]) >= 1
    monthly = store.usage_by_period(period="month", days=30)
    assert monthly["days"]
    # route filter narrows the call list
    filtered = store.usage_by_period(period="day", days=1, route="other-route")
    assert filtered["calls"] == []


def test_route_status_and_cooldown_reset(store: CostStore) -> None:
    """route_status reports last call + cooldown; clear_cooldown resets."""
    import time as _t

    now = int(_t.time())
    store.insert(
        UsageRecord(
            request_id="r1", api_key="k", user_id=None, team=None,
            model="@openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
            provider="p", prompt_tokens=1, completion_tokens=1,
            total_tokens=2, input_cost=0.0, output_cost=0.0,
            total_cost=0.0, latency_ms=5, status="success",
            timestamp=now, route="hermes-default",
        )
    )
    st = store.route_status("hermes-default", ["@openrouter/nvidia/nemotron-3-ultra-550b-a55b:free", "other-model"])
    info = st["models"]["@openrouter/nvidia/nemotron-3-ultra-550b-a55b:free"]
    assert info["last_status"] == "success"
    assert info["last_called_at"] == now
    assert info["cooldown_remaining"] == 0
    assert st["last_served"]["model"] == "@openrouter/nvidia/nemotron-3-ultra-550b-a55b:free"
    # never-called model has no status
    assert st["models"]["other-model"]["last_called_at"] is None

    store.set_model_cooldown("hermes-default", "other-model", seconds=120, reason="quota")
    st2 = store.route_status("hermes-default", ["other-model"])
    assert st2["models"]["other-model"]["cooldown_remaining"] > 0
    assert st2["models"]["other-model"]["cooldown_reason"] == "quota"
    store.clear_cooldown("hermes-default", "other-model")
    st3 = store.route_status("hermes-default", ["other-model"])
    assert st3["models"]["other-model"]["cooldown_remaining"] == 0
    assert st3["models"]["other-model"]["cooldown_reason"] is None
