"""TDD contracts for review-remediation integration workflows."""

from __future__ import annotations

import sqlite3

import httpx
import pytest
from llm_budget_gateway.replay_execution import LocalReplayExecutor, ReplayRequest

from llm_budget_gateway.console_api import create_console_app


@pytest.mark.asyncio
async def test_local_replay_executes_real_http_and_returns_measured_evidence() -> None:
    async def provider(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer app-key"
        return httpx.Response(
            200,
            json={
                "model": "candidate-model",
                "choices": [{"message": {"content": "candidate answer"}}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            },
        )

    executor = LocalReplayExecutor(
        api_key="app-key",
        transport=httpx.MockTransport(provider),
        clock_ns=iter([100, 300]).__next__,
    )
    result = await executor.execute(
        ReplayRequest(
            "req-1",
            "candidate-model",
            ({"role": "user", "content": "safe test"},),
            20,
            0.004,
        )
    )
    assert result.output == "candidate answer"
    assert result.tokens == 15
    assert result.latency_ms == pytest.approx(0.0002)
    assert result.estimated_cost_usd == 0.004


@pytest.mark.asyncio
async def test_local_replay_rejects_missing_key_bad_input_and_provider_error() -> None:
    with pytest.raises(ValueError, match="API key"):
        await LocalReplayExecutor(api_key="").execute(
            ReplayRequest("r", "m", ({"role": "user", "content": "x"},), 1, 0)
        )
    executor = LocalReplayExecutor(
        api_key="k",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(500, json={"detail": "secret upstream"})
        ),
    )
    with pytest.raises(ValueError, match="provider replay failed"):
        await executor.execute(
            ReplayRequest("r", "m", ({"role": "user", "content": "x"},), 1, 0)
        )
    with pytest.raises(ValueError, match="messages"):
        await LocalReplayExecutor(api_key="k").execute(
            ReplayRequest("r", "m", (), 1, 0)
        )


@pytest.mark.asyncio
async def test_replay_run_api_executes_candidate_and_compares() -> None:
    async def provider(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "m2",
                "choices": [{"message": {"content": "new answer"}}],
                "usage": {"total_tokens": 7},
            },
        )

    executor = LocalReplayExecutor(
        api_key="k",
        transport=httpx.MockTransport(provider),
        clock_ns=iter([0, 2_000_000]).__next__,
    )
    app = create_console_app(replay_executor=executor)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/console/replay/run",
            json={
                "request_id": "req",
                "baseline_output": "old answer",
                "baseline_model": "m1",
                "baseline_cost_usd": 0.01,
                "baseline_tokens": 8,
                "baseline_latency_ms": 4,
                "candidate_model": "m2",
                "messages": [{"role": "user", "content": "safe replay"}],
                "max_completion_tokens": 20,
                "estimated_cost_usd": 0.004,
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["candidate"]["output"] == "new answer"
    assert body["impact"]["cost_delta_usd"] == pytest.approx(-0.006)
    assert body["executed"] is True


def test_compatibility_result_can_populate_contract_catalog() -> None:
    from llm_budget_gateway.market_priority import CompatibilityContractCatalog
    from llm_budget_gateway.p0_workflows import (
        CompatibilityProbe,
        ProviderCompatibilityLab,
    )

    catalog = CompatibilityContractCatalog(
        sqlite3.connect(":memory:"), now_fn=lambda: 100
    )
    result = ProviderCompatibilityLab().evaluate(
        provider_id="p",
        probes=[
            CompatibilityProbe("tools", True, 2),
            CompatibilityProbe("streaming", False, 3),
        ],
    )
    saved = catalog.record_result(
        result, model_id="m", checked_at=90, region="eu", price_per_million=1.5
    )
    assert saved == 2
    assert catalog.eligible(
        provider_id="p",
        model_id="m",
        required=("tools",),
        max_age_seconds=20,
        region="eu",
        require_price=True,
    )
    assert not catalog.eligible(
        provider_id="p",
        model_id="m",
        required=("streaming",),
        max_age_seconds=20,
        region="eu",
    )


@pytest.mark.asyncio
async def test_replay_executor_validation_boundaries_and_malformed_response() -> None:
    with pytest.raises(ValueError, match="local gateway"):
        LocalReplayExecutor(api_key="k", base_url="https://example.com")
    executor = LocalReplayExecutor(api_key="k")
    valid = ({"role": "user", "content": "x"},)
    with pytest.raises(ValueError, match="request_id"):
        await executor.execute(ReplayRequest("", "m", valid, 1, 0))
    with pytest.raises(ValueError, match="max_completion_tokens"):
        await executor.execute(ReplayRequest("r", "m", valid, 0, 0))
    with pytest.raises(ValueError, match="estimated replay cost"):
        await executor.execute(ReplayRequest("r", "m", valid, 1, float("nan")))
    with pytest.raises(ValueError, match="supported role"):
        await executor.execute(
            ReplayRequest("r", "m", ({"role": "tool", "content": "x"},), 1, 0)
        )
    malformed = LocalReplayExecutor(
        api_key="k",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})),
    )
    with pytest.raises(ValueError, match="no assistant output"):
        await malformed.execute(ReplayRequest("r", "m", valid, 1, 0))
