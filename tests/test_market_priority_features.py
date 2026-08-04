"""TDD coverage for the three market-priority workflows."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from llm_budget_gateway.market_priority import (
    ChangeImpactLab,
    CompatibilityContract,
    CompatibilityContractCatalog,
    ReplayCandidate,
    ReplayTrace,
    RuntimeGovernor,
    RuntimeStep,
)


def test_replay_lab_compares_semantic_operational_and_safety_change() -> None:
    result = ChangeImpactLab().compare(
        ReplayTrace("req-1", "answer", ("search",), 0.10, 100, 250, "allow"),
        ReplayCandidate(
            "gpt-new", "answer improved", ("search", "cite"), 0.08, 120, 210, "allow"
        ),
    )
    assert result.request_id == "req-1"
    assert result.cost_delta_usd == pytest.approx(-0.02)
    assert result.token_delta == 20
    assert result.latency_delta_ms == -40
    assert result.tool_changes == {"added": ["cite"], "removed": []}
    assert result.recommendation == "review"


def test_replay_lab_rejects_invalid_or_unsafe_candidate() -> None:
    lab = ChangeImpactLab()
    with pytest.raises(ValueError, match="request_id"):
        lab.compare(
            ReplayTrace("", "x", (), 0, 0, 0, "allow"),
            ReplayCandidate("m", "x", (), 0, 0, 0, "allow"),
        )
    blocked = lab.compare(
        ReplayTrace("r", "x", (), 1, 1, 1, "allow"),
        ReplayCandidate("m", "x", (), 1, 1, 1, "deny"),
    )
    assert blocked.recommendation == "reject"
    assert blocked.safety_changed is True


def test_runtime_governor_detects_loop_drift_and_irreversible_action() -> None:
    governor = RuntimeGovernor(loop_threshold=3)
    steps = [RuntimeStep("search", "read", False)] * 3
    decision = governor.evaluate(intent="read", steps=steps, approved_actions=set())
    assert decision.allowed is False
    assert decision.code == "loop_detected"
    drift = governor.evaluate(
        intent="read",
        steps=[RuntimeStep("delete", "write", True)],
        approved_actions=set(),
    )
    assert drift.code == "intent_drift"
    approved = governor.evaluate(
        intent="write",
        steps=[RuntimeStep("delete", "write", True)],
        approved_actions={"delete"},
    )
    assert approved.allowed is True


def test_runtime_governor_validates_boundaries() -> None:
    with pytest.raises(ValueError, match="loop_threshold"):
        RuntimeGovernor(loop_threshold=1)
    with pytest.raises(ValueError, match="intent"):
        RuntimeGovernor().evaluate(intent="", steps=[], approved_actions=set())


def test_contract_catalog_real_sqlite_io_and_route_eligibility(tmp_path: Path) -> None:
    db = tmp_path / "contracts.db"
    catalog = CompatibilityContractCatalog(sqlite3.connect(db), now_fn=lambda: 1_000)
    catalog.record(CompatibilityContract("p1", "m1", "tools", True, 900, 0.01, "eu"))
    catalog.record(
        CompatibilityContract("p1", "m1", "streaming", True, 950, 0.01, "eu")
    )
    assert (
        catalog.eligible(
            provider_id="p1",
            model_id="m1",
            required=("tools", "streaming"),
            max_age_seconds=200,
            region="eu",
        )
        is True
    )
    reopened = CompatibilityContractCatalog(sqlite3.connect(db), now_fn=lambda: 1_200)
    assert reopened.matrix("p1")[0]["capability"] == "streaming"
    assert (
        reopened.eligible(
            provider_id="p1",
            model_id="m1",
            required=("tools",),
            max_age_seconds=100,
            region="eu",
        )
        is False
    )


def test_contract_catalog_rejects_stale_price_and_bad_contract() -> None:
    catalog = CompatibilityContractCatalog(
        sqlite3.connect(":memory:"), now_fn=lambda: 100
    )
    with pytest.raises(ValueError, match="capability"):
        catalog.record(CompatibilityContract("p", "m", "", True, 1, 1, "eu"))
    catalog.record(CompatibilityContract("p", "m", "tools", True, 50, None, "eu"))
    assert (
        catalog.eligible(
            provider_id="p",
            model_id="m",
            required=("tools",),
            max_age_seconds=100,
            region="eu",
            require_price=True,
        )
        is False
    )


@pytest.mark.asyncio
async def test_market_priority_api_end_to_end() -> None:
    import httpx

    from llm_budget_gateway.console_api import create_console_app

    app = create_console_app(
        market_connection=sqlite3.connect(":memory:", check_same_thread=False)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        replay = await client.post(
            "/v1/console/replay/compare",
            json={
                "baseline": {
                    "request_id": "r1",
                    "output": "a",
                    "tools": [],
                    "cost_usd": 1,
                    "tokens": 2,
                    "latency_ms": 3,
                    "policy": "allow",
                },
                "candidate": {
                    "model": "m2",
                    "output": "a",
                    "tools": [],
                    "cost_usd": 0.5,
                    "tokens": 2,
                    "latency_ms": 2,
                    "policy": "allow",
                },
            },
        )
        assert replay.status_code == 200
        assert replay.json()["recommendation"] == "accept"
        governor = await client.post(
            "/v1/console/governor/evaluate",
            json={
                "intent": "read",
                "steps": [{"action": "search", "intent": "read"}],
                "approved_actions": [],
            },
        )
        assert governor.json()["allowed"] is True
        saved = await client.post(
            "/v1/console/contracts",
            json={
                "provider_id": "p",
                "model_id": "m",
                "capability": "tools",
                "supported": True,
                "checked_at": 100,
                "price_per_million": 1.0,
                "region": "eu",
            },
        )
        assert saved.status_code == 201
        assert (await client.get("/v1/console/contracts/p")).json()["contracts"][0][
            "model_id"
        ] == "m"
