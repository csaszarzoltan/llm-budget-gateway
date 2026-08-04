"""TDD acceptance coverage for the production trace explorer flow."""

from __future__ import annotations

import sqlite3

import httpx
import pytest

from llm_budget_gateway.console_api import create_console_app
from llm_budget_gateway.trace_outcomes import TraceSpan, TraceStore


def _seed(connection: sqlite3.Connection) -> None:
    store = TraceStore(connection)
    store.append(
        TraceSpan(
            "root", "run-a", "acme", None, "agent", "Planner", 100, 300, 0.02, "ok", {}
        )
    )
    store.append(
        TraceSpan(
            "tool", "run-a", "acme", "root", "tool", "Search", 150, 220, 0.01, "ok", {}
        )
    )
    store.append(
        TraceSpan(
            "root-b",
            "run-b",
            "acme",
            None,
            "agent",
            "Support",
            400,
            450,
            0.005,
            "failed",
            {},
        )
    )
    store.append(
        TraceSpan("hidden", "run-x", "beta", None, "agent", "Hidden", 0, 1, 0, "ok", {})
    )


@pytest.mark.asyncio
async def test_list_runs_and_nested_trace_real_http() -> None:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    _seed(connection)
    app = create_console_app(trace_connection=connection)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://console"
    ) as client:
        runs = await client.get("/v1/console/traces", headers={"X-Tenant-Id": "acme"})
        trace = await client.get(
            "/v1/console/traces/run-a", headers={"X-Tenant-Id": "acme"}
        )
    assert runs.status_code == 200
    assert [item["run_id"] for item in runs.json()["runs"]] == ["run-b", "run-a"]
    assert all(item["run_id"] != "run-x" for item in runs.json()["runs"])
    assert trace.json()["trace"][0]["children"][0]["name"] == "Search"


@pytest.mark.asyncio
async def test_list_runs_requires_tenant_and_trace_page_is_production_asset() -> None:
    app = create_console_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://console"
    ) as client:
        missing = await client.get("/v1/console/traces")
        page = await client.get("/cockpit")
    assert missing.status_code == 422
    assert page.status_code == 200
    assert "Gateway Cockpit" in page.text
