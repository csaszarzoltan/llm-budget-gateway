"""RED-first contracts for the OpenTelemetry/OpenInference evidence plane."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from llm_budget_gateway.console_api import create_console_app
from llm_budget_gateway.evidence_plane import EvidenceEvent, EvidencePlane


def test_evidence_plane_normalizes_openinference_span_and_redacts_secrets() -> None:
    plane = EvidencePlane(sqlite3.connect(":memory:"), now_fn=lambda: 100)
    event = plane.record(
        EvidenceEvent(
            tenant_id="tenant-a",
            trace_id="a" * 32,
            span_id="b" * 16,
            parent_span_id=None,
            kind="model",
            name="chat",
            started_at_ns=10,
            ended_at_ns=20,
            status="ok",
            attributes={
                "llm.model_name": "gpt-4o",
                "authorization": "Bearer secret",
                "input.value": "private prompt",
            },
            metrics={"llm.cost_usd": 0.12, "llm.token_count.total": 42},
        )
    )
    assert event.attributes["authorization"] == "[REDACTED]"
    assert event.attributes["input.value"] == "[REDACTED]"
    exported = plane.export_trace(tenant_id="tenant-a", trace_id="a" * 32)
    span = exported["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert span["traceId"] == "a" * 32
    assert span["attributes"]["openinference.span.kind"] == "LLM"
    assert span["attributes"]["llm.cost_usd"] == 0.12


def test_evidence_plane_validates_ids_timing_status_and_metrics() -> None:
    plane = EvidencePlane(sqlite3.connect(":memory:"))
    base = dict(
        tenant_id="t",
        trace_id="x",
        span_id="y",
        parent_span_id=None,
        kind="tool",
        name="call",
        started_at_ns=1,
        ended_at_ns=2,
        status="ok",
        attributes={},
        metrics={},
    )
    with pytest.raises(ValueError, match="trace_id"):
        plane.record(EvidenceEvent(**base))
    base.update(trace_id="a" * 32, span_id="b" * 16, ended_at_ns=0)
    with pytest.raises(ValueError, match="ended"):
        plane.record(EvidenceEvent(**base))
    base.update(ended_at_ns=2, status="maybe")
    with pytest.raises(ValueError, match="status"):
        plane.record(EvidenceEvent(**base))
    base.update(status="ok", metrics={"cost": float("nan")})
    with pytest.raises(ValueError, match="finite"):
        plane.record(EvidenceEvent(**base))


def test_evidence_plane_is_tenant_isolated_and_parent_ordered(tmp_path: Path) -> None:
    db = tmp_path / "evidence.db"
    plane = EvidencePlane(sqlite3.connect(db))
    parent = EvidenceEvent(
        "a", "1" * 32, "2" * 16, None, "agent", "run", 1, 9, "ok", {}, {}
    )
    child = EvidenceEvent(
        "a", "1" * 32, "3" * 16, "2" * 16, "tool", "search", 2, 4, "ok", {}, {}
    )
    plane.record(child)
    plane.record(parent)
    plane.record(
        EvidenceEvent(
            "b", "1" * 32, "4" * 16, None, "agent", "other", 1, 2, "ok", {}, {}
        )
    )
    assert [x.name for x in plane.list_trace(tenant_id="a", trace_id="1" * 32)] == [
        "run",
        "search",
    ]
    assert len(plane.list_trace(tenant_id="b", trace_id="1" * 32)) == 1
    reopened = EvidencePlane(sqlite3.connect(db))
    assert (
        reopened.list_trace(tenant_id="a", trace_id="1" * 32)[1].parent_span_id
        == "2" * 16
    )


def test_evidence_plane_jsonl_export_is_deterministic() -> None:
    plane = EvidencePlane(sqlite3.connect(":memory:"))
    plane.record(
        EvidenceEvent(
            "t",
            "a" * 32,
            "b" * 16,
            None,
            "policy",
            "allow",
            1,
            2,
            "ok",
            {"decision": "allow"},
            {},
        )
    )
    first = plane.export_jsonl(tenant_id="t", trace_id="a" * 32)
    assert first == plane.export_jsonl(tenant_id="t", trace_id="a" * 32)
    assert json.loads(first)["name"] == "allow"


@pytest.mark.asyncio
async def test_evidence_plane_real_asgi_flow() -> None:
    app = create_console_app(
        evidence_connection=sqlite3.connect(":memory:", check_same_thread=False)
    )
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "tenant_id": "acme",
            "trace_id": "a" * 32,
            "span_id": "b" * 16,
            "parent_span_id": None,
            "kind": "gateway",
            "name": "route",
            "started_at_ns": 1,
            "ended_at_ns": 2,
            "status": "ok",
            "attributes": {"route": "support"},
            "metrics": {"cost_usd": 0.1},
        }
        assert (
            await client.post("/v1/console/evidence/spans", json=payload)
        ).status_code == 201
        response = await client.get(
            "/v1/console/evidence/traces/" + "a" * 32, params={"tenant_id": "acme"}
        )
        assert response.status_code == 200
        assert (
            response.json()["resourceSpans"][0]["resource"]["attributes"][
                "service.name"
            ]
            == "llm-budget-gateway"
        )
        assert (
            await client.get(
                "/v1/console/evidence/traces/" + "a" * 32, params={"tenant_id": "other"}
            )
        ).status_code == 404
