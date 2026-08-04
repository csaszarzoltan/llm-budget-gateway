"""TDD acceptance tests for research-ranked P0 features."""

from __future__ import annotations

import sqlite3

import httpx
import pytest

from llm_budget_gateway.console_api import create_console_app
from llm_budget_gateway.priority_features import (
    CockpitService,
    RunawayFirewall,
    RunawayLedger,
    RunLimits,
    RunState,
    SchemaFormService,
)


def test_cockpit_summary_prioritizes_critical_actions() -> None:
    summary = CockpitService().summarize(
        spend={"current": 120.0, "budget": 100.0, "change_pct": 22.0},
        quality={"score": 0.83, "minimum": 0.9},
        operations={"incidents": 2, "failing_models": 1},
        governance={"pending_approvals": 3, "policy_coverage": 0.75},
    )
    assert summary["status"] == "critical"
    assert summary["metrics"][0]["id"] == "spend"
    assert summary["actions"][0]["severity"] == "critical"
    assert {a["kind"] for a in summary["actions"]} >= {
        "budget",
        "quality",
        "incident",
        "policy",
    }


def test_cockpit_summary_healthy_and_validates_values() -> None:
    service = CockpitService()
    result = service.summarize(
        spend={"current": 10, "budget": 100, "change_pct": -2},
        quality={"score": 0.98, "minimum": 0.9},
        operations={"incidents": 0, "failing_models": 0},
        governance={"pending_approvals": 0, "policy_coverage": 1},
    )
    assert result["status"] == "healthy"
    assert result["actions"] == []
    with pytest.raises(ValueError, match="budget"):
        service.summarize(
            spend={"current": 1, "budget": 0}, quality={}, operations={}, governance={}
        )


def test_runaway_firewall_allows_boundary_and_explains_block() -> None:
    firewall = RunawayFirewall()
    limits = RunLimits(
        max_cost_usd=2,
        max_tokens=1000,
        max_tool_calls=5,
        max_depth=3,
        max_elapsed_seconds=60,
        max_retries=2,
    )
    allowed = firewall.evaluate(RunState("r1", 1.5, 900, 4, 2, 40, 1), limits)
    assert allowed.allowed is True
    blocked = firewall.evaluate(RunState("r1", 2, 900, 4, 2, 40, 1), limits)
    assert blocked.allowed is False
    assert blocked.code == "cost_limit"
    assert "2.0000" in blocked.explanation


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("tokens", 1000, "token_limit"),
        ("tool_calls", 5, "tool_call_limit"),
        ("depth", 3, "depth_limit"),
        ("elapsed_seconds", 60, "elapsed_limit"),
        ("retries", 2, "retry_limit"),
    ],
)
def test_runaway_firewall_each_limit(field: str, value: int, code: str) -> None:
    values = {
        "cost_usd": 0,
        "tokens": 0,
        "tool_calls": 0,
        "depth": 0,
        "elapsed_seconds": 0,
        "retries": 0,
    }
    values[field] = value
    decision = RunawayFirewall().evaluate(
        RunState(run_id="r", **values), RunLimits(1, 1000, 5, 3, 60, 2)
    )
    assert decision.code == code


def test_runaway_firewall_emergency_stop_and_invalid_state() -> None:
    limits = RunLimits(1, 100, 2, 2, 10, 1)
    decision = RunawayFirewall().evaluate(
        RunState("r", 0, 0, 0, 0, 0, 0, emergency_stop=True), limits
    )
    assert decision.code == "emergency_stop"
    with pytest.raises(ValueError, match="non-negative"):
        RunawayFirewall().evaluate(RunState("r", -1, 0, 0, 0, 0, 0), limits)


def test_runaway_ledger_real_sqlite_io_and_reconciliation() -> None:
    conn = sqlite3.connect(":memory:")
    ledger = RunawayLedger(conn)
    limits = RunLimits(5, 1000, 10, 4, 120, 3)
    ledger.reserve("run-1", limits)
    state = ledger.reconcile(
        "run-1",
        cost_usd=1.2,
        tokens=120,
        tool_calls=2,
        depth=1,
        elapsed_seconds=3,
        retries=0,
    )
    assert state.cost_usd == 1.2
    assert ledger.get("run-1") == state
    with pytest.raises(ValueError, match="already exists"):
        ledger.reserve("run-1", limits)
    with pytest.raises(KeyError):
        ledger.get("missing")


def test_schema_form_generation_supports_objects_arrays_and_enums() -> None:
    form = SchemaFormService().generate(
        "demo",
        {
            "type": "object",
            "required": ["model"],
            "properties": {
                "model": {
                    "type": "string",
                    "title": "Model",
                    "enum": ["small", "large"],
                },
                "limit": {"type": "integer", "minimum": 1, "default": 10},
                "tags": {"type": "array", "items": {"type": "string"}},
                "enabled": {"type": "boolean", "description": "Enable policy"},
            },
        },
    )
    assert form["id"] == "demo"
    controls = {x["name"]: x for x in form["controls"]}
    assert controls["model"]["widget"] == "select"
    assert controls["model"]["required"] is True
    assert controls["tags"]["widget"] == "list"
    assert controls["enabled"]["widget"] == "checkbox"


def test_schema_form_rejects_unsupported_root_and_secret_names() -> None:
    service = SchemaFormService()
    with pytest.raises(ValueError, match="object"):
        service.generate("bad", {"type": "array"})
    form = service.generate(
        "safe", {"type": "object", "properties": {"api_key": {"type": "string"}}}
    )
    assert form["controls"][0]["sensitive"] is True
    assert "persist" in form["controls"][0]["help"].lower()


@pytest.mark.asyncio
async def test_priority_api_real_http_flow() -> None:
    app = create_console_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://console"
    ) as client:
        cockpit = await client.post(
            "/v1/console/cockpit/summary",
            json={
                "spend": {"current": 50, "budget": 100},
                "quality": {"score": 0.95, "minimum": 0.9},
                "operations": {"incidents": 0},
                "governance": {"pending_approvals": 0, "policy_coverage": 1},
            },
        )
        assert cockpit.status_code == 200
        assert cockpit.json()["status"] == "healthy"
        firewall = await client.post(
            "/v1/console/runaway/evaluate",
            json={
                "state": {
                    "run_id": "agent-1",
                    "cost_usd": 5,
                    "tokens": 1,
                    "tool_calls": 0,
                    "depth": 0,
                    "elapsed_seconds": 1,
                    "retries": 0,
                },
                "limits": {
                    "max_cost_usd": 2,
                    "max_tokens": 1000,
                    "max_tool_calls": 5,
                    "max_depth": 3,
                    "max_elapsed_seconds": 60,
                    "max_retries": 2,
                },
            },
        )
        assert firewall.status_code == 200
        assert firewall.json()["code"] == "cost_limit"
        form = await client.post(
            "/v1/console/forms/generate",
            json={
                "form_id": "onboard",
                "schema": {
                    "type": "object",
                    "required": ["model"],
                    "properties": {"model": {"type": "string"}},
                },
            },
        )
        assert form.status_code == 200
        assert form.json()["controls"][0]["name"] == "model"
        bad = await client.post(
            "/v1/console/forms/generate",
            json={"form_id": "bad", "schema": {"type": "array"}},
        )
        assert bad.status_code == 422


@pytest.mark.asyncio
async def test_react_cockpit_is_served_with_accessible_flow() -> None:
    app = create_console_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://console"
    ) as client:
        response = await client.get("/cockpit")
    assert response.status_code == 200
    assert "Gateway Cockpit" in response.text
    assert "Skip to main content" in response.text


def test_runaway_ledger_rejects_invalid_reservations_and_reconciliation() -> None:
    ledger = RunawayLedger(sqlite3.connect(":memory:"))
    with pytest.raises(ValueError, match="non-empty"):
        ledger.reserve(" ", RunLimits(1, 1, 1, 1, 1, 1))
    ledger.reserve("r", RunLimits(1, 1, 1, 1, 1, 1))
    with pytest.raises(ValueError, match="non-negative"):
        ledger.reconcile(
            "r",
            cost_usd=float("nan"),
            tokens=0,
            tool_calls=0,
            depth=0,
            elapsed_seconds=0,
            retries=0,
        )
    with pytest.raises(KeyError):
        ledger.reconcile(
            "missing",
            cost_usd=0,
            tokens=0,
            tool_calls=0,
            depth=0,
            elapsed_seconds=0,
            retries=0,
        )


def test_services_reject_bad_numbers_and_property_shapes() -> None:
    with pytest.raises(ValueError, match="numeric"):
        CockpitService().summarize(
            spend={"current": "x"}, quality={}, operations={}, governance={}
        )
    with pytest.raises(ValueError, match="finite"):
        CockpitService().summarize(
            spend={"current": float("inf")}, quality={}, operations={}, governance={}
        )
    with pytest.raises(ValueError, match="schema object"):
        SchemaFormService().generate(
            "bad", {"type": "object", "properties": {"x": "bad"}}
        )


@pytest.mark.asyncio
async def test_priority_api_validation_paths() -> None:
    app = create_console_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://console"
    ) as client:
        bad_cockpit = await client.post(
            "/v1/console/cockpit/summary", json={"spend": {"budget": 0}}
        )
        bad_run = await client.post(
            "/v1/console/runaway/evaluate", json={"state": {}, "limits": {}}
        )
    assert bad_cockpit.status_code == 422
    assert bad_run.status_code == 422
