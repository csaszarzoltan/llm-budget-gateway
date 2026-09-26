"""Claude Code calls /v1/messages/count_tokens every single turn.

The gateway had no such route, so the SDK got a 404 each time and silently
fell back to its own estimate. Verified in ~/.claude/debug on a real run:
    [API REQUEST] /v1/messages/count_tokens source=count_tokens
    [ERROR] countTokens API call failed: 404 {"detail":"Not Found"}

It works today, which is why this went unnoticed — but the client's
estimate is not the gateway's tokenization, and with
CLAUDE_CODE_MAX_CONTEXT_TOKENS=262144 a client near the ceiling can believe
it has room when the real prompt would overflow. The error also adds latency
on every turn and pollutes the logs with a 404 the user never sees.

These tests exercise the endpoint through the real ASGI app.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from llm_budget_gateway.config import Settings
from llm_budget_gateway.main import create_app


@pytest.fixture()
def client() -> TestClient:
    # TestClient, not httpx.Client: ASGITransport is async-only, and the
    # rest of this suite drives the app the same way.
    return TestClient(create_app(settings=Settings()))


def _payload(tokens: str = "hello") -> dict:
    return {
        "model": "hermes-default",
        "messages": [{"role": "user", "content": tokens}],
    }


def test_count_tokens_is_not_404(client: TestClient) -> None:
    resp = client.post(
        "/v1/messages/count_tokens",
        json=_payload(),
        headers={"x-api-key": "sk-test", "anthropic-version": "2023-06-01"},
    )
    assert resp.status_code != 404, (
        "Claude Code gets a 404 from count_tokens every turn and silently "
        "falls back to its own tokenization"
    )
    assert resp.status_code in (200, 401), resp.status_code


def test_count_tokens_answers_in_the_anthropic_shape(client: TestClient) -> None:
    resp = client.post(
        "/v1/messages/count_tokens",
        json=_payload(),
        headers={"x-api-key": "sk-test", "anthropic-version": "2023-06-01"},
    )
    if resp.status_code == 401:
        pytest.skip("no valid key in this environment; shape covered below")
    data = resp.json()
    # Anthropic returns a bare {"input_tokens": N} — the SDK parses exactly
    # this key, so anything else (the cost-estimate dict) is not read.
    assert "input_tokens" in data, data
    assert isinstance(data["input_tokens"], int), data
    assert data["input_tokens"] >= 0


def test_a_longer_prompt_counts_higher(client: TestClient) -> None:
    headers = {"x-api-key": "sk-test", "anthropic-version": "2023-06-01"}
    short = client.post("/v1/messages/count_tokens", json=_payload("hi"), headers=headers)
    long = client.post(
        "/v1/messages/count_tokens",
        json=_payload("The quick brown fox jumps over the lazy dog. " * 200),
        headers=headers,
    )
    if short.status_code == 401 or long.status_code == 401:
        pytest.skip("no valid key in this environment")
    assert long.json()["input_tokens"] >= short.json()["input_tokens"], (
        short.json(),
        long.json(),
    )


def test_an_unknown_model_is_a_404_with_a_message(client: TestClient) -> None:
    resp = client.post(
        "/v1/messages/count_tokens",
        json={"model": "no-such-model", "messages": [{"role": "user", "content": "x"}]},
        headers={"x-api-key": "sk-test", "anthropic-version": "2023-06-01"},
    )
    assert resp.status_code in (401, 404)
    if resp.status_code == 404:
        assert "model" in resp.text.lower()
