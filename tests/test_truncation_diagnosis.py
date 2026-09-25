"""Truncation diagnosis: finish_reason + empty_response in cost_records.

RED phase. Without these columns a `success` row cannot distinguish a natural
stop from a provider-side `length` cut, which is exactly why past
"cut off" reports stayed undecidable. Three contracts:
1. idempotent migration on a PRE-EXISTING table (CREATE TABLE IF NOT EXISTS
   never alters one),
2. the value is actually recorded on a success row,
3. usage_by_period's success_rate stops counting 0-token successes.
"""

from __future__ import annotations

import sqlite3
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
    UsageRecord,
)


@pytest.fixture
def store(tmp_path) -> CostStore:
    return CostStore(str(tmp_path / "gateway.db"))


@pytest.fixture
def tracker() -> CostTracker:
    return CostTracker(
        store=CostStore(":memory:"),
        calculator=CostCalculator(PriceMap(overrides={"m": ModelPrice(1.0, 2.0)})),
    )


def test_migration_adds_columns_to_existing_table(tmp_path) -> None:
    """A DB created by an older build already has cost_records WITHOUT the
    new columns; CostStore must add them without touching existing rows."""
    path = str(tmp_path / "legacy.db")
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE cost_records (
        request_id TEXT PRIMARY KEY, api_key TEXT NOT NULL, user_id TEXT,
        team TEXT, model TEXT NOT NULL, provider TEXT NOT NULL,
        prompt_tokens INTEGER NOT NULL, completion_tokens INTEGER NOT NULL,
        total_tokens INTEGER NOT NULL, input_cost REAL NOT NULL,
        output_cost REAL NOT NULL, total_cost REAL NOT NULL,
        latency_ms INTEGER NOT NULL, status TEXT NOT NULL, status_code INTEGER,
        timestamp INTEGER NOT NULL)"""
    )
    con.execute(
        "INSERT INTO cost_records VALUES ('old-1','k',NULL,NULL,'m','direct',1,2,3,0,0,0,5,'success',200,?)",
        (int(time.time()),),
    )
    con.commit()
    con.close()

    s = CostStore(path)  # must not raise
    cols = {r[1] for r in s.connection.execute("PRAGMA table_info(cost_records)")}
    assert "finish_reason" in cols
    assert "empty_response" in cols
    row = s.connection.execute(
        "SELECT model, total_tokens, finish_reason, empty_response "
        "FROM cost_records WHERE request_id='old-1'"
    ).fetchone()
    assert row[0] == "m" and row[1] == 3
    assert row[2] is None  # back-compat: old rows stay NULL
    assert row[3] == 0


def test_insert_records_finish_reason_and_empty_flag(store: CostStore) -> None:
    store.insert(
        UsageRecord(
            request_id="r1", api_key="k", user_id=None, team=None, model="m",
            provider="direct", prompt_tokens=10, completion_tokens=0,
            total_tokens=10, input_cost=0.0, output_cost=0.0, total_cost=0.0,
            latency_ms=5, status="success", timestamp=int(time.time()),
            finish_reason="length",
            empty_response=True,
        )
    )
    row = store.connection.execute(
        "SELECT finish_reason, empty_response FROM cost_records WHERE request_id='r1'"
    ).fetchone()
    assert row[0] == "length"
    assert row[1] == 1


def test_extract_finish_reason_from_body_and_chunks() -> None:
    """`length` is the decisive marker for "the answer got cut off" — a
    natural stop and a provider-side cap must be distinguishable after the
    fact, from the recorded row alone."""
    from llm_budget_gateway.gateway_proxy import GatewayProxy as P

    assert P._extract_finish_reason(
        {"choices": [{"finish_reason": "length"}]}
    ) == "length"
    assert P._extract_finish_reason(
        {"choices": [{"finish_reason": "stop"}]}
    ) == "stop"
    assert P._extract_finish_reason({"status": "incomplete"}) == "length"
    assert P._extract_finish_reason({"status": "completed"}) == "completed"
    # stream chunks: the terminal finish_reason lives in the LAST chunk
    chunks = [
        {"choices": [{"delta": {"content": "a"}}]},
        {"choices": [{"delta": {}, "finish_reason": "length"}]},
    ]
    assert P._extract_finish_reason(chunks) == "length"
    assert P._extract_finish_reason({}) is None
    assert P._extract_finish_reason(None) is None


def test_usage_by_period_excludes_empty_success_from_success_rate(store: CostStore) -> None:
    """A 200 that produced zero output tokens is NOT a success: the Usage
    page used to read 100% while clients were getting empty answers."""
    now = int(time.time())
    base = dict(api_key="k", user_id=None, team=None, model="m", provider="direct",
                input_cost=0.0, output_cost=0.0, total_cost=0.0, latency_ms=5)
    store.insert(UsageRecord(**base, request_id="ok-1", status="success",
                             timestamp=now, prompt_tokens=10, completion_tokens=5,
                             total_tokens=15, empty_response=False))
    store.insert(UsageRecord(**base, request_id="empty-1", status="success",
                             timestamp=now, prompt_tokens=10, completion_tokens=0,
                             total_tokens=10, finish_reason="stop", empty_response=True))
    store.insert(UsageRecord(**base, request_id="err-1", status="error",
                             timestamp=now, prompt_tokens=10, completion_tokens=0,
                             total_tokens=10, status_code=502))

    data = store.usage_by_period(days=1, period="hour")
    buckets = data.get("days") or data.get("buckets") or []
    assert buckets, f"no buckets in {data}"
    tot = sum(b.get("requests", 0) for b in buckets)
    succ = sum(b.get("success", 0) for b in buckets)
    assert tot == 3
    # 1 real success + 1 empty + 1 error -> success must be 1, not 2
    assert succ == 1, f"empty_response success counted as success: {buckets}"
