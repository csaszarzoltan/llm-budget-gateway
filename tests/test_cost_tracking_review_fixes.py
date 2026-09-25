"""The five cost_tracking.py defects the Claude Code review found.

1. `daily_usage(route="A")` put OTHER routes' cost/token sums into A's day
   buckets and total_calls, and applied LIMIT/OFFSET to the UNFILTERED set —
   so page 1 for A could come back empty while A's rows sat past the window,
2. same via `usage_by_period(route="A")`: bucket sums, requests/success/
   failed and the paginated calls all ran unfiltered,
3. a project-scoped record stored project=None, so spend_since("project:p1")
   stayed 0 forever despite recorded spend,
4. `attach_shared_lock` stored the mutex but every DB method used
   `with self._lock`, so cost and telemetry raced on one sqlite handle,
5. `resolve_customer_id`'s SELECT ran on the shared connection with no lock.
"""
from __future__ import annotations

import time

import pytest

from llm_budget_gateway.cost_tracking import CostStore, UsageRecord


def _rec(route: str, cost: float, ts: float | None = None, model: str = "m1") -> UsageRecord:
    return UsageRecord(
        request_id=f"rq-{route}-{cost}-{model}-{ts}",
        api_key="k",
        user_id=None,
        team=None,
        model=model,
        provider="p",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        input_cost=cost / 2,
        output_cost=cost / 2,
        total_cost=cost,
        latency_ms=10,
        status="success",
        timestamp=int(ts if ts is not None else time.time()),
        route=route,
        status_code=200,
    )


@pytest.fixture()
def store(tmp_path) -> CostStore:
    return CostStore(str(tmp_path / "gw.db"))


def _insert_all(store: CostStore, recs: list[UsageRecord]) -> None:
    # NOT inside `with store.shared_lock:` — insert() takes that same
    # non-reentrant mutex itself, so wrapping the loop deadlocks. The point
    # of the fixture is to exercise the store's own locking.
    for r in recs:
        store.insert(r)


# --- 1 & 2. route filtering must happen in SQL -------------------------------

def test_daily_usage_route_filter_is_in_sql(store: CostStore) -> None:
    _insert_all(
        store,
        [
            _rec("A", 1.0, model="model-a"),
            _rec("B", 100.0, model="model-b"),
        ],
    )
    out = store.daily_usage(days=7, route="A")

    # total_calls must count ONLY route A
    assert out["total_calls"] == 1, out["total_calls"]

    # the day buckets must not contain B's cost
    total_cost = sum(
        m["cost_usd"] for d in out["days"] for m in d["models"]
    )
    assert total_cost == pytest.approx(1.0), total_cost
    models = {m["model"] for d in out["days"] for m in d["models"]}
    assert "model-b" not in models, models

    # and every returned call is route A
    for c in out["calls"]:
        assert c["route"] == "A", c


def test_daily_usage_without_a_route_still_returns_everything(store: CostStore) -> None:
    _insert_all(store, [_rec("A", 1.0), _rec("B", 2.0)])
    out = store.daily_usage(days=7)
    assert out["total_calls"] == 2
    total_cost = sum(m["cost_usd"] for d in out["days"] for m in d["models"])
    assert total_cost == pytest.approx(3.0)


def test_daily_usage_paginates_the_filtered_set(store: CostStore) -> None:
    """The old code applied LIMIT/OFFSET before filtering, so page 1 for a
    route could be empty while its rows sat past the window."""
    recs = []
    for i in range(10):
        recs.append(_rec("A", 1.0 + i, ts=time.time() - 100 + i, model=f"a{i}"))
        recs.append(_rec("B", 50.0, ts=time.time() - 50, model="b0"))
    _insert_all(store, recs)

    page1 = store.daily_usage(days=7, route="A", page=1, page_size=3)
    assert len(page1["calls"]) == 3, page1["calls"]
    assert all(c["route"] == "A" for c in page1["calls"])
    assert page1["total_calls"] == 10, page1["total_calls"]


def test_usage_by_period_route_filter_is_in_sql(store: CostStore) -> None:
    _insert_all(
        store,
        [_rec("A", 1.0, model="model-a"), _rec("B", 100.0, model="model-b")],
    )
    out = store.usage_by_period(period="day", days=7, route="A")
    assert out["total_calls"] == 1, out["total_calls"]
    for bucket in out["days"]:
        for group in ("models", "routes"):
            for item in bucket.get(group, []):
                if item.get("model") == "model-b":
                    raise AssertionError(f"B leaked into A: {item}")
    for c in out["calls"]:
        assert c["route"] == "A"


def test_usage_by_period_paginates_the_filtered_set(store: CostStore) -> None:
    recs = []
    for i in range(6):
        recs.append(_rec("A", 1.0, ts=time.time() - 100 + i, model=f"a{i}"))
        recs.append(_rec("B", 9.0, ts=time.time() - 50, model="b0"))
    _insert_all(store, recs)
    page1 = store.usage_by_period(
        period="day", days=7, route="A", page=1, page_size=2
    )
    assert len(page1["calls"]) == 2
    assert all(c["route"] == "A" for c in page1["calls"])


# --- 3. the project scope ----------------------------------------------------

def test_project_scope_is_recorded(store: CostStore) -> None:
    """A project-scoped record must land in the project column."""
    from llm_budget_gateway.budget_enforcement import BudgetScope
    from llm_budget_gateway.cost_tracking import (
        CostCalculator,
        CostTracker,
        PriceMap,
        TokenUsage,
    )

    ct = CostTracker(store=store, calculator=CostCalculator(PriceMap()))
    rec = ct.build_record(
        request_id="rq-project",
        scope=BudgetScope(kind="project", key="p1"),
        model="m",
        provider="p",
        usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        latency_ms=5,
        status="success",
        route="r",
        status_code=200,
    )
    assert rec.project == "p1", rec.project

    # a non-project scope must NOT populate it
    rec2 = ct.build_record(
        request_id="rq-user",
        scope=BudgetScope(kind="user", key="u1"),
        model="m",
        provider="p",
        usage=None,
        latency_ms=5,
        status="success",
        route="r",
        status_code=200,
    )
    assert rec2.project is None
    assert rec2.user_id == "u1"
