"""TDD tests for P1 trace and cost-to-outcome features."""

from __future__ import annotations

import sqlite3

import httpx
import pytest

from llm_budget_gateway.console_api import create_console_app
from llm_budget_gateway.trace_outcomes import (
    OutcomeAnalytics,
    OutcomeRecord,
    TraceSpan,
    TraceStore,
)


def test_trace_store_round_trip_tree_and_tenant_isolation() -> None:
    store = TraceStore(sqlite3.connect(":memory:"))
    store.append(
        TraceSpan(
            "root",
            "run-1",
            "acme",
            None,
            "agent",
            "planner",
            0,
            120,
            0.02,
            "ok",
            {"model": "small"},
        )
    )
    store.append(
        TraceSpan(
            "tool",
            "run-1",
            "acme",
            "root",
            "tool",
            "search",
            10,
            60,
            0.01,
            "ok",
            {"tool": "docs"},
        )
    )
    store.append(
        TraceSpan("other", "run-1", "other", None, "agent", "hidden", 0, 1, 0, "ok", {})
    )
    tree = store.trace("acme", "run-1")
    assert tree[0]["span_id"] == "root"
    assert tree[0]["children"][0]["span_id"] == "tool"
    assert tree[0]["duration_ms"] == 120
    assert "metadata" not in tree[0]
    assert len(store.list_runs("acme")) == 1


def test_trace_store_rejects_invalid_graph_and_duplicates() -> None:
    store = TraceStore(sqlite3.connect(":memory:"))
    with pytest.raises(ValueError, match="duration"):
        store.append(TraceSpan("x", "r", "t", None, "agent", "x", 2, 1, 0, "ok", {}))
    store.append(TraceSpan("root", "r", "t", None, "agent", "x", 0, 1, 0, "ok", {}))
    with pytest.raises(ValueError, match="already exists"):
        store.append(TraceSpan("root", "r", "t", None, "agent", "x", 0, 1, 0, "ok", {}))
    with pytest.raises(ValueError, match="parent"):
        store.append(
            TraceSpan("child", "r", "t", "missing", "tool", "x", 0, 1, 0, "ok", {})
        )
    with pytest.raises(KeyError):
        store.trace("t", "missing")


def test_outcome_analytics_unit_economics_and_breakdowns() -> None:
    analytics = OutcomeAnalytics()
    result = analytics.summarize(
        [
            OutcomeRecord(
                "1", "acme", "checkout", "team-a", "gpt", "search", 2.0, 0.9, True
            ),
            OutcomeRecord(
                "2", "acme", "checkout", "team-a", "gpt", "search", 1.0, 0.7, False
            ),
            OutcomeRecord(
                "3", "acme", "support", "team-b", "mini", "crm", 0.5, 1.0, True
            ),
        ]
    )
    assert result["total_cost_usd"] == 3.5
    assert result["successful_outcomes"] == 2
    assert result["cost_per_success"] == 1.75
    assert result["quality_weighted_cost"] == pytest.approx(3.5 / 2.6)
    assert result["by_feature"][0]["name"] == "checkout"
    assert result["by_feature"][0]["cost_usd"] == 3.0


def test_outcome_analytics_empty_and_invalid() -> None:
    analytics = OutcomeAnalytics()
    assert analytics.summarize([])["cost_per_success"] is None
    with pytest.raises(ValueError, match="cost"):
        analytics.summarize(
            [OutcomeRecord("1", "t", "f", "p", "m", "tool", -1, 0.5, True)]
        )
    with pytest.raises(ValueError, match="quality"):
        analytics.summarize(
            [OutcomeRecord("1", "t", "f", "p", "m", "tool", 1, 2, True)]
        )


@pytest.mark.asyncio
async def test_trace_and_outcome_api_integration() -> None:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    app = create_console_app(trace_connection=conn)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://console"
    ) as client:
        created = await client.post(
            "/v1/console/traces",
            json={
                "span_id": "root",
                "run_id": "run-7",
                "tenant_id": "acme",
                "parent_span_id": None,
                "kind": "agent",
                "name": "plan",
                "started_ms": 0,
                "ended_ms": 20,
                "cost_usd": 0.1,
                "status": "ok",
                "metadata": {"prompt": "must not persist"},
            },
        )
        assert created.status_code == 201
        trace = await client.get(
            "/v1/console/traces/run-7", headers={"X-Tenant-Id": "acme"}
        )
        assert trace.status_code == 200
        assert trace.json()["trace"][0]["name"] == "plan"
        summary = await client.post(
            "/v1/console/outcomes/summary",
            json={
                "records": [
                    {
                        "record_id": "1",
                        "tenant_id": "acme",
                        "feature": "checkout",
                        "project": "web",
                        "model": "mini",
                        "tool": "search",
                        "cost_usd": 0.5,
                        "quality_score": 0.95,
                        "succeeded": True,
                    }
                ]
            },
        )
        assert summary.status_code == 200
        assert summary.json()["cost_per_success"] == 0.5
        missing_tenant = await client.get("/v1/console/traces/run-7")
        assert missing_tenant.status_code == 422
