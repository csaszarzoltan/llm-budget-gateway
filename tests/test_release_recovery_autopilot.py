"""RED-first tests for safe releases and outcome-aware autopilot."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import httpx
import pytest

from llm_budget_gateway.console_api import create_console_app
from llm_budget_gateway.production_readiness import (
    AutopilotCandidate,
    OutcomeAutopilot,
    ReleaseRecoveryService,
)


def test_release_recovery_backup_verify_restore_real_io(tmp_path: Path) -> None:
    source = tmp_path / "gateway.db"
    conn = sqlite3.connect(source)
    conn.execute("CREATE TABLE item(id INTEGER PRIMARY KEY,value TEXT)")
    conn.execute("INSERT INTO item(value) VALUES ('before')")
    conn.commit()
    conn.close()
    service = ReleaseRecoveryService(tmp_path / "backups")
    backup = service.create_backup(source, release_id="13.7.0")
    assert backup.path.is_file()
    assert backup.sha256 == hashlib.sha256(backup.path.read_bytes()).hexdigest()
    assert service.verify_backup(backup) is True
    conn = sqlite3.connect(source)
    conn.execute("UPDATE item SET value='after'")
    conn.commit()
    conn.close()
    service.restore(backup, source)
    assert (
        sqlite3.connect(source).execute("SELECT value FROM item").fetchone()[0]
        == "before"
    )


def test_release_recovery_rejects_unsafe_release_and_tampering(tmp_path: Path) -> None:
    source = tmp_path / "db.sqlite"
    sqlite3.connect(source).close()
    service = ReleaseRecoveryService(tmp_path / "backups")
    with pytest.raises(ValueError, match="release_id"):
        service.create_backup(source, release_id="../bad")
    backup = service.create_backup(source, release_id="safe-1")
    backup.path.write_bytes(b"tampered")
    assert service.verify_backup(backup) is False
    with pytest.raises(ValueError, match="verification"):
        service.restore(backup, source)


def test_release_recovery_plans_fail_closed_and_canary_decision() -> None:
    service = ReleaseRecoveryService(Path("unused"))
    blocked = service.plan_rollout(
        provenance_verified=False,
        backup_verified=True,
        migration_ready=True,
        regression_passed=True,
        canary_percent=10,
    )
    assert blocked["allowed"] is False and "provenance" in blocked["gaps"]
    with pytest.raises(ValueError, match="canary"):
        service.plan_rollout(
            provenance_verified=True,
            backup_verified=True,
            migration_ready=True,
            regression_passed=True,
            canary_percent=0,
        )
    assert (
        service.canary_decision(
            error_rate=0.02,
            max_error_rate=0.01,
            p95_latency_ms=200,
            max_p95_latency_ms=500,
            quality=0.9,
            minimum_quality=0.8,
        )["action"]
        == "rollback"
    )
    assert (
        service.canary_decision(
            error_rate=0,
            max_error_rate=0.01,
            p95_latency_ms=200,
            max_p95_latency_ms=500,
            quality=0.9,
            minimum_quality=0.8,
        )["action"]
        == "promote"
    )


def test_outcome_autopilot_recommends_only_bounded_improvement() -> None:
    autopilot = OutcomeAutopilot()
    result = autopilot.recommend(
        baseline=AutopilotCandidate("current", 0.10, 800, 0.90, 0.99),
        candidates=[
            AutopilotCandidate("cheap", 0.06, 700, 0.92, 0.995),
            AutopilotCandidate("bad", 0.01, 400, 0.6, 1.0),
        ],
        minimum_quality=0.85,
        minimum_success_rate=0.98,
        maximum_latency_ms=900,
    )
    assert result["action"] == "recommend"
    assert result["candidate"] == "cheap"
    assert result["estimated_savings_percent"] == 40.0
    assert result["requires_approval"] is True


def test_outcome_autopilot_holds_without_evidence_or_improvement() -> None:
    autopilot = OutcomeAutopilot()
    with pytest.raises(ValueError, match="candidate"):
        autopilot.recommend(
            baseline=AutopilotCandidate("b", 1, 1, 1, 1),
            candidates=[],
            minimum_quality=0.8,
            minimum_success_rate=0.9,
            maximum_latency_ms=10,
        )
    result = autopilot.recommend(
        baseline=AutopilotCandidate("b", 0.1, 100, 0.9, 0.99),
        candidates=[AutopilotCandidate("worse", 0.2, 100, 0.9, 0.99)],
        minimum_quality=0.8,
        minimum_success_rate=0.9,
        maximum_latency_ms=200,
    )
    assert result["action"] == "hold"


@pytest.mark.asyncio
async def test_release_and_autopilot_api_integration(tmp_path: Path) -> None:
    app = create_console_app(recovery_root=tmp_path / "backups")
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        plan = await client.post(
            "/v1/console/releases/plan",
            json={
                "provenance_verified": True,
                "backup_verified": True,
                "migration_ready": True,
                "regression_passed": True,
                "canary_percent": 10,
            },
        )
        assert plan.status_code == 200 and plan.json()["allowed"] is True
        auto = await client.post(
            "/v1/console/autopilot/recommend",
            json={
                "baseline": {
                    "name": "b",
                    "cost_usd": 0.1,
                    "latency_ms": 100,
                    "quality": 0.9,
                    "success_rate": 0.99,
                },
                "candidates": [
                    {
                        "name": "c",
                        "cost_usd": 0.05,
                        "latency_ms": 90,
                        "quality": 0.91,
                        "success_rate": 0.99,
                    }
                ],
                "minimum_quality": 0.8,
                "minimum_success_rate": 0.98,
                "maximum_latency_ms": 200,
            },
        )
        assert auto.status_code == 200 and auto.json()["candidate"] == "c"
