"""Unpriced-traffic visibility: a model with no known price must not look free."""
from __future__ import annotations

import time

import pytest

from llm_budget_gateway.budget_enforcement import BudgetScope
from llm_budget_gateway.cost_tracking import (
    CostCalculator,
    CostStore,
    CostTracker,
    ModelPrice,
    PriceMap,
    TokenUsage,
)

UNKNOWN = "@direct/no-such-model-xyz"


def _tracker(tmp_path, overrides=None) -> tuple[CostTracker, CostStore]:
    store = CostStore(str(tmp_path / "ledger.db"))
    tracker = CostTracker(store=store, calculator=CostCalculator(PriceMap(overrides)))
    return tracker, store


def _record(tracker: CostTracker, model: str, request_id: str, tokens: int = 100):
    return tracker.build_record(
        request_id=request_id,
        scope=BudgetScope(kind="key", key="k1"),
        model=model,
        provider="direct",
        usage=TokenUsage(prompt_tokens=tokens, completion_tokens=0, total_tokens=tokens),
        latency_ms=5,
        status="success",
    )


def test_unknown_model_with_usage_is_flagged_unpriced(tmp_path):
    tracker, _ = _tracker(tmp_path)

    assert _record(tracker, UNKNOWN, "r1").unpriced is True


def test_explicit_zero_override_is_priced_not_flagged(tmp_path):
    tracker, _ = _tracker(tmp_path, {UNKNOWN: ModelPrice(0.0, 0.0)})

    assert _record(tracker, UNKNOWN, "r1").unpriced is False


def test_record_without_usage_is_not_flagged(tmp_path):
    tracker, _ = _tracker(tmp_path)
    rec = tracker.build_record(
        request_id="r1", scope=BudgetScope(kind="key", key="k1"), model=UNKNOWN,
        provider="direct", usage=None, latency_ms=5, status="error",
    )

    assert rec.unpriced is False


def test_unpriced_summary_counts_requests_tokens_and_models(tmp_path):
    tracker, store = _tracker(tmp_path, {"priced-model": ModelPrice(1.0, 1.0)})
    store.insert(_record(tracker, UNKNOWN, "r1", 100))
    store.insert(_record(tracker, UNKNOWN, "r2", 50))
    store.insert(_record(tracker, "priced-model", "r3", 70))

    summary = store.unpriced_summary(days=1)

    assert summary["requests"] == 2
    assert summary["tokens"] == 150
    assert summary["models"] == [{"model": UNKNOWN, "requests": 2, "tokens": 150}]


def test_unpriced_summary_ignores_rows_outside_the_window(tmp_path):
    tracker, store = _tracker(tmp_path)
    old = _record(tracker, UNKNOWN, "old")
    old.timestamp = int(time.time()) - 5 * 86400
    store.insert(old)

    assert store.unpriced_summary(days=1)["requests"] == 0


def test_old_database_without_the_column_is_migrated(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE cost_records (request_id TEXT PRIMARY KEY, api_key TEXT NOT NULL,"
        " user_id TEXT, team TEXT, model TEXT NOT NULL, provider TEXT NOT NULL,"
        " prompt_tokens INTEGER NOT NULL, completion_tokens INTEGER NOT NULL,"
        " total_tokens INTEGER NOT NULL, input_cost REAL NOT NULL, output_cost REAL NOT NULL,"
        " total_cost REAL NOT NULL, latency_ms INTEGER NOT NULL, status TEXT NOT NULL,"
        " timestamp INTEGER NOT NULL)"
    )
    conn.commit()
    conn.close()

    store = CostStore(str(path))

    assert store.unpriced_summary(days=1)["requests"] == 0
