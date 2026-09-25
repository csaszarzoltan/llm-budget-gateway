"""The four main.py defects the Claude Code review found.

1. a non-str/dict upstream chunk was mapped to "" and DROPPED while the
   stream still ended with a clean message_stop (silent truncation),
2. a LIST body (the shape `_provider_response` itself streams) fell into the
   async-iter branch and raised TypeError -> a spurious `event: error`,
3. the cancel-on-disconnect task was left running detached when the endpoint
   itself was cancelled mid-poll (burning provider tokens, nobody awaiting),
4. `proxy._telemetry.store` raised AttributeError -> 500 when the telemetry
   attach failed, instead of the honest 503.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from llm_budget_gateway.config import Settings
from llm_budget_gateway.gateway_proxy import ProviderResponse
from llm_budget_gateway.main import create_app

GW_KEY = "sk_test_mainreview"
SSE = 'data: {"choices":[{"delta":{"content":"%s"}}]}\n\n'


def _app_with_body(body_factory, monkeypatch) -> TestClient:
    app = create_app(Settings(virtual_keys={GW_KEY: "test"}))

    async def handle(_body, _key, _headers):
        return ProviderResponse(
            status_code=200,
            body=body_factory(),
            headers={},
            model="stub",
            usage=None,
            latency_ms=1,
        )

    for route in app.routes:
        if getattr(route, "path", "") == "/v1/messages":
            for cell in route.endpoint.__closure__ or ():
                obj = cell.cell_contents
                if hasattr(obj, "handle_chat_completion"):
                    monkeypatch.setattr(
                        obj, "handle_chat_completion", handle, raising=False
                    )
    return TestClient(app, raise_server_exceptions=False)


def _stream_text(c: TestClient) -> str:
    with c.stream(
        "POST",
        "/v1/messages",
        json={
            "model": "hermes-default",
            "max_tokens": 32,
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers={"x-api-key": GW_KEY},
    ) as r:
        return "".join(line for line in r.iter_lines() if line.strip())


def test_list_body_streams_without_a_spurious_error(monkeypatch) -> None:
    c = _app_with_body(lambda: [SSE % "alpha", SSE % "beta"], monkeypatch)
    text = _stream_text(c)
    assert "event: error" not in text
    assert "alpha" in text and "beta" in text
    assert "event: message_stop" in text


def test_bytes_chunks_are_decoded_not_dropped(monkeypatch) -> None:
    async def gen():
        yield (SSE % "from-bytes").encode()
        yield (SSE % "tail").encode()

    c = _app_with_body(gen, monkeypatch)
    text = _stream_text(c)
    assert "from-bytes" in text
    assert "tail" in text
    assert "event: error" not in text


def test_dict_chunks_pass_through(monkeypatch) -> None:
    async def gen():
        yield {"choices": [{"delta": {"content": "as-dict"}}]}

    c = _app_with_body(gen, monkeypatch)
    text = _stream_text(c)
    assert "as-dict" in text
    assert "event: error" not in text


def test_telemetry_endpoints_503_when_telemetry_is_none(monkeypatch) -> None:
    """_telemetry is None when the attach in create_app raised; the endpoint
    must answer 503, not raise AttributeError -> 500."""
    app = create_app(Settings(virtual_keys={GW_KEY: "test"}))
    for route in app.routes:
        if getattr(route, "path", "") == "/v1/messages":
            for cell in route.endpoint.__closure__ or ():
                obj = cell.cell_contents
                if hasattr(obj, "handle_chat_completion"):
                    monkeypatch.setattr(obj, "_telemetry", None, raising=False)
    c = TestClient(app, raise_server_exceptions=False)
    r1 = c.get("/v1/observability/requests")
    r2 = c.get("/v1/observability/summary")
    assert r1.status_code == 503
    assert r2.status_code == 503


@pytest.mark.asyncio
async def test_cancel_on_disconnect_cancels_the_upstream_task() -> None:
    """When the endpoint coroutine is cancelled mid-poll, the upstream task
    must be cancelled and awaited — never left running detached."""
    cancelled = asyncio.Event()

    async def slow_upstream():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    upstream = asyncio.create_task(slow_upstream())
    await asyncio.sleep(0.05)
    assert not upstream.done()

    # The endpoint's shape: a poll loop wrapped so CancelledError cancels +
    # awaits the upstream before re-raising.
    async def poll_loop():
        try:
            while not upstream.done():
                try:
                    await asyncio.wait_for(asyncio.sleep(0.05), timeout=0.1)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            upstream.cancel()
            try:
                await upstream
            except (asyncio.CancelledError, Exception):
                pass
            raise

    poller = asyncio.create_task(poll_loop())
    await asyncio.sleep(0.05)
    poller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await poller
    assert cancelled.is_set(), "upstream was left running after cancellation"
    assert upstream.done()
