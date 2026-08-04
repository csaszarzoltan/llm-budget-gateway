"""TDD contracts for the research-ranked provider lab and incident explainer."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from llm_budget_gateway.console_api import create_console_app
from llm_budget_gateway.p0_workflows import (
    CompatibilityProbe,
    IncidentEvidence,
    IncidentTimelineStore,
    ProviderCompatibilityLab,
)


def test_compatibility_lab_scores_capabilities_and_repairs() -> None:
    result = ProviderCompatibilityLab().evaluate(
        provider_id="openai-prod",
        probes=[
            CompatibilityProbe("authentication", True, 80),
            CompatibilityProbe("model_discovery", True, 120),
            CompatibilityProbe("streaming", False, 250, "HTTP 404 /chat/completions"),
            CompatibilityProbe("tools", False, 190, "tool_choice rejected"),
        ],
    )
    assert result.status == "degraded"
    assert result.score == 50
    assert result.passed == 2
    assert result.total == 4
    assert result.repairs[0].capability == "streaming"
    assert "/v1" in result.repairs[0].action
    assert "tool" in result.repairs[1].action.lower()


def test_compatibility_lab_validates_provider_and_probe_set() -> None:
    lab = ProviderCompatibilityLab()
    with pytest.raises(ValueError, match="provider_id"):
        lab.evaluate(provider_id=" ", probes=[])
    with pytest.raises(ValueError, match="at least one"):
        lab.evaluate(provider_id="p", probes=[])
    with pytest.raises(ValueError, match="duplicate"):
        lab.evaluate(
            provider_id="p",
            probes=[
                CompatibilityProbe("streaming", True, 1),
                CompatibilityProbe("streaming", False, 2),
            ],
        )
    with pytest.raises(ValueError, match="latency"):
        lab.evaluate(provider_id="p", probes=[CompatibilityProbe("chat", True, -1)])


def test_compatibility_lab_reports_ready_and_blocked() -> None:
    lab = ProviderCompatibilityLab()
    ready = lab.evaluate(provider_id="p", probes=[CompatibilityProbe("chat", True, 1)])
    blocked = lab.evaluate(
        provider_id="p", probes=[CompatibilityProbe("authentication", False, 1, "401")]
    )
    assert (ready.status, ready.score) == ("ready", 100)
    assert blocked.status == "blocked"
    assert "credential" in blocked.repairs[0].action.lower()


def test_incident_timeline_real_sqlite_io_and_explanation(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "incidents.db")
    store = IncidentTimelineStore(connection)
    store.append(
        IncidentEvidence(
            "inc-1", 10, "request", "received", "Request accepted", "info", {}
        )
    )
    store.append(
        IncidentEvidence(
            "inc-1",
            12,
            "provider",
            "failed",
            "Primary returned 429",
            "critical",
            {"provider": "openai"},
        )
    )
    store.append(
        IncidentEvidence(
            "inc-1",
            14,
            "fallback",
            "recovered",
            "Fallback served",
            "warning",
            {"model": "claude"},
        )
    )
    report = store.explain("inc-1")
    assert [event["kind"] for event in report["timeline"]] == [
        "request",
        "provider",
        "fallback",
    ]
    assert report["status"] == "recovered"
    assert report["impact"] == "Primary returned 429"
    assert "rate limit" in report["fix"].lower()
    reopened = IncidentTimelineStore(sqlite3.connect(tmp_path / "incidents.db"))
    assert reopened.explain("inc-1")["timeline"] == report["timeline"]


def test_incident_timeline_errors_and_secret_redaction() -> None:
    store = IncidentTimelineStore(sqlite3.connect(":memory:"))
    with pytest.raises(KeyError):
        store.explain("missing")
    with pytest.raises(ValueError, match="severity"):
        store.append(IncidentEvidence("i", 1, "provider", "failed", "bad", "loud", {}))
    stored = store.append(
        IncidentEvidence(
            "i",
            1,
            "policy",
            "blocked",
            "Key sk-abcdefghijklmnopqrst blocked",
            "critical",
            {"authorization": "Bearer abc"},
        )
    )
    assert "sk-" not in stored.summary
    assert stored.details["authorization"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_p0_workflow_api_full_user_flow() -> None:
    app = create_console_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://console"
    ) as client:
        lab = await client.post(
            "/v1/console/compatibility/evaluate",
            json={
                "provider_id": "demo",
                "probes": [
                    {"capability": "authentication", "passed": True, "latency_ms": 20},
                    {
                        "capability": "streaming",
                        "passed": False,
                        "latency_ms": 50,
                        "detail": "404",
                    },
                ],
            },
        )
        assert lab.status_code == 200
        assert lab.json()["repairs"]
        created = await client.post(
            "/v1/console/incidents/events",
            json={
                "incident_id": "api-incident",
                "timestamp": 1,
                "kind": "budget",
                "outcome": "blocked",
                "summary": "Budget ceiling reached",
                "severity": "critical",
                "details": {"scope": "application:demo"},
            },
        )
        assert created.status_code == 201
        explained = await client.get("/v1/console/incidents/api-incident")
        assert explained.status_code == 200
        assert "budget" in explained.json()["fix"].lower()
        missing = await client.get("/v1/console/incidents/missing")
        assert missing.status_code == 404


def test_openapi_documents_p0_endpoints() -> None:
    paths = create_console_app().openapi()["paths"]
    assert "/v1/console/compatibility/evaluate" in paths
    assert "/v1/console/incidents/events" in paths
    assert "/v1/console/incidents/{incident_id}" in paths


def test_compatibility_lab_generic_repair_and_empty_capability() -> None:
    lab = ProviderCompatibilityLab()
    result = lab.evaluate(
        provider_id="custom", probes=[CompatibilityProbe("audio", False, 0)]
    )
    assert "connection settings" in result.repairs[0].action
    with pytest.raises(ValueError, match="capability"):
        lab.evaluate(provider_id="custom", probes=[CompatibilityProbe(" ", True, 0)])


def test_incident_validation_nested_redaction_and_fix_variants() -> None:
    store = IncidentTimelineStore(sqlite3.connect(":memory:"))
    with pytest.raises(ValueError, match="incident_id"):
        store.append(IncidentEvidence("", 0, "", "failed", "bad", "info", {}))
    with pytest.raises(ValueError, match="timestamp"):
        store.append(IncidentEvidence("i", -1, "request", "failed", "bad", "info", {}))
    stored = store.append(
        IncidentEvidence(
            "auth",
            1,
            "provider",
            "failed",
            "Provider returned 401",
            "critical",
            {"nested": {"token": "secret"}, "items": ["Bearer abc", "safe"]},
        )
    )
    assert stored.details["nested"]["token"] == "[REDACTED]"
    assert stored.details["items"][0] == "[REDACTED]"
    assert "credential" in store.explain("auth")["fix"].lower()
    store.append(
        IncidentEvidence(
            "timeout", 1, "provider", "failed", "upstream timeout", "critical", {}
        )
    )
    assert "provider health" in store.explain("timeout")["fix"].lower()
    store.append(
        IncidentEvidence("other", 1, "policy", "blocked", "custom rule", "warning", {})
    )
    assert "linked route" in store.explain("other")["fix"].lower()


def test_compatibility_run_store_persists_history(tmp_path: Path) -> None:
    from llm_budget_gateway.p0_workflows import CompatibilityRunStore

    db = tmp_path / "compatibility.db"
    store = CompatibilityRunStore(sqlite3.connect(db))
    result = ProviderCompatibilityLab().evaluate(
        provider_id="provider-a",
        probes=[CompatibilityProbe("authentication", True, 12)],
    )
    run = store.save(result, checked_at=100)
    assert run["run_id"].startswith("compat_")
    assert run["checked_at"] == 100
    history = CompatibilityRunStore(sqlite3.connect(db)).list("provider-a", limit=10)
    assert history == [run]


def test_compatibility_run_store_validates_limit_and_provider() -> None:
    from llm_budget_gateway.p0_workflows import CompatibilityRunStore

    store = CompatibilityRunStore(sqlite3.connect(":memory:"))
    with pytest.raises(ValueError, match="provider_id"):
        store.list("", limit=10)
    with pytest.raises(ValueError, match="limit"):
        store.list("p", limit=0)


@pytest.mark.asyncio
async def test_compatibility_history_api() -> None:
    app = create_console_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://console"
    ) as client:
        created = await client.post(
            "/v1/console/compatibility/evaluate",
            json={
                "provider_id": "history-provider",
                "probes": [
                    {"capability": "authentication", "passed": True, "latency_ms": 3}
                ],
            },
        )
        assert created.status_code == 200
        history = await client.get(
            "/v1/console/compatibility/history-provider/history?limit=5"
        )
        assert history.status_code == 200
        assert history.json()["runs"][0]["score"] == 100
