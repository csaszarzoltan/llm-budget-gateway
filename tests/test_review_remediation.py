"""Regression tests for independent review blockers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from llm_budget_gateway.console_api import create_console_app
from llm_budget_gateway.p0_workflows import ProviderCompatibilityRunner
from llm_budget_gateway.product_console import ProductConsoleStore
from llm_budget_gateway.provider_connections import (
    CredentialVault,
    ProviderConnectionStore,
)


@pytest.mark.asyncio
async def test_real_compatibility_runner_executes_provider_http_checks(
    tmp_path: Path,
) -> None:
    db = sqlite3.connect(":memory:")
    store = ProviderConnectionStore(db, CredentialVault(tmp_path / "key"))
    provider = store.create(
        {
            "name": "Local",
            "slug": "local",
            "provider_type": "openai_compatible",
            "api_key": "secret",
            "base_url": "https://provider.example/v1",
            "region": "eu",
        }
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "model-a"}]})
        if request.url.path.endswith("/chat/completions"):
            body = request.read().decode()
            if '"stream":true' in body.replace(" ", ""):
                return httpx.Response(
                    200,
                    text='data: {"choices":[]}\n\ndata: [DONE]\n\n',
                    headers={"content-type": "text/event-stream"},
                )
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "ok"}}]}
            )
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"embedding": [0.1]}]})
        return httpx.Response(404)

    result = await ProviderCompatibilityRunner(store, httpx.MockTransport(handler)).run(
        str(provider["id"])
    )
    assert result.provider_id == provider["id"]
    assert {p.capability for p in result.probes} >= {
        "authentication",
        "model_discovery",
        "chat",
        "streaming",
        "tools",
        "structured_output",
        "embeddings",
    }
    assert all(p.latency_ms >= 0 for p in result.probes)
    assert len(requests) >= 7
    assert all(
        request.headers.get("authorization") == "Bearer secret" for request in requests
    )


@pytest.mark.asyncio
async def test_real_compatibility_api_uses_stored_provider_not_submitted_results(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "m"}]})
        return httpx.Response(200, json={"choices": [], "data": []})

    app = create_console_app(
        provider_connection=sqlite3.connect(":memory:", check_same_thread=False),
        credential_key_path=tmp_path / "key",
        provider_discovery_transport=httpx.MockTransport(handler),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://console"
    ) as client:
        created = await client.post(
            "/v1/product/provider-connections",
            json={
                "name": "Local",
                "slug": "local",
                "provider_type": "openai_compatible",
                "api_key": "secret",
                "base_url": "https://provider.example/v1",
                "region": "eu",
            },
        )
        response = await client.post(
            f"/v1/console/compatibility/{created.json()['id']}/run"
        )
    assert response.status_code == 200
    assert response.json()["measured"] is True
    assert response.json()["probes"]


def test_incident_is_derived_from_real_product_activity() -> None:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    product = ProductConsoleStore(connection)
    event = product.record_request(
        "app-1", "support", "model-a", 1.25, 900, False, "provider 429"
    )
    assert product.activity_item(event["id"])["cost_usd"] == 1.25


@pytest.mark.asyncio
async def test_incident_request_api_builds_timeline_from_activity() -> None:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    product = ProductConsoleStore(connection)
    event = product.record_request(
        "app-1", "support", "model-a", 1.25, 900, False, "provider 429"
    )
    app = create_console_app(product_connection=connection)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://console"
    ) as client:
        response = await client.get(f"/v1/console/incidents/from-request/{event['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "product_activity"
    assert body["impact"] == "provider 429"
    assert {x["kind"] for x in body["timeline"]} >= {
        "request",
        "route",
        "provider",
        "cost",
    }


def test_authentication_failure_blocks_regardless_of_probe_order() -> None:
    from llm_budget_gateway.p0_workflows import (
        CompatibilityProbe,
        ProviderCompatibilityLab,
    )

    result = ProviderCompatibilityLab().evaluate(
        provider_id="p",
        probes=[
            CompatibilityProbe("chat", True, 1),
            CompatibilityProbe("authentication", False, 1, "401"),
        ],
    )
    assert result.status == "blocked"


def test_incident_redaction_covers_common_secret_aliases_and_tokens() -> None:
    from llm_budget_gateway.p0_workflows import IncidentEvidence, IncidentTimelineStore

    store = IncidentTimelineStore(sqlite3.connect(":memory:"))
    saved = store.append(
        IncidentEvidence(
            "i",
            1,
            "provider",
            "failed",
            "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature aws AKIA1234567890ABCDEF",
            "critical",
            {"access_token": "abc", "client_secret": "xyz", "x-api-key": "key"},
        )
    )
    assert "eyJ" not in saved.summary and "AKIA" not in saved.summary
    assert set(saved.details.values()) == {"[REDACTED]"}


def test_compatibility_runner_provider_request_shapes_and_helpers() -> None:
    from llm_budget_gateway.p0_workflows import (
        CompatibilityProbe,
        CompatibilityRunStore,
        ProviderCompatibilityLab,
        ProviderCompatibilityRunner,
    )

    runner = ProviderCompatibilityRunner(object())
    for provider, config in [
        (
            "azure_openai",
            {"base_url": "https://azure", "api_key": "k", "api_version": "v"},
        ),
        ("anthropic", {"base_url": "https://anthropic", "api_key": "k"}),
        ("gemini", {"base_url": "https://gemini", "api_key": "k"}),
    ]:
        requests = runner._capability_requests(provider, config, "model")
        assert len(requests) == 5
        assert {name for name, _ in requests} == {
            "chat",
            "streaming",
            "tools",
            "structured_output",
            "embeddings",
        }
    assert runner._capability_requests("vertex_ai", {}, "m") == []
    non_json = httpx.Response(200, text="plain", headers={"content-type": "text/plain"})
    assert runner._validate_response("chat", non_json)[0] is False
    assert "[REDACTED]" in runner._safe_error(
        ValueError("Bearer secret sk-abcdefghijklmnop")
    )
    store = CompatibilityRunStore(sqlite3.connect(":memory:"))
    result = ProviderCompatibilityLab().evaluate(
        provider_id="p", probes=[CompatibilityProbe("chat", True, 1)]
    )
    with pytest.raises(ValueError, match="checked_at"):
        store.save(result, checked_at=-1)
