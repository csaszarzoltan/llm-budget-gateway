"""Mid-stream upstream death must surface as an Anthropic `event: error`.

Patching the CLASS method is the workable seam: the proxy is a closure local
inside create_app, so the stub has to be installed at
DirectProviderClient.stream_chunks level. A bare `except: pass` around the
upstream iteration plus an unconditional `message_stop` previously handed the
client a SUCCESSFUL-looking, silently truncated answer.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from llm_budget_gateway.config import Settings
from llm_budget_gateway.main import create_app

# A key must be one the app's Settings actually accept, otherwise the request
# fails auth (401) before the stream path is ever reached.
GW_KEY = "sk_test_midstream"
MODEL = "hermes-default"


def _client_with_stub(monkeypatch, *, fail: bool) -> TestClient:
    """Stub the direct client's stream at the seam the route loop calls.

    The route resolves `hermes-default` from product.db, so patching
    DirectProviderClient.stream_chunks alone is not enough — litellm is asked
    for a route name it cannot resolve. Drive the Anthropic edge against a
    proxy whose `handle_chat_completion` returns a mid-stream-failing
    response, which is exactly the shape the endpoint must survive.
    """
    from llm_budget_gateway.gateway_proxy import ProviderResponse

    async def failing_body():
        yield "data: {\"choices\":[{\"delta\":{\"content\":\"partial \"}}]}\n\n"
        if fail:
            raise RuntimeError("upstream died mid-stream")
        yield (
            "data: {\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}],"
            "\"usage\":{\"prompt_tokens\":5,\"completion_tokens\":2,\"total_tokens\":7}}\n\n"
        )
        yield "data: [DONE]\n\n"

    async def handle(body, api_key, headers):
        return ProviderResponse(
            status_code=200,
            body=failing_body(),
            headers={},
            model="stub-model",
            usage=None,
            latency_ms=1,
        )

    app = create_app(Settings(virtual_keys={GW_KEY: "test"}))
    # The proxy is a closure local in create_app; reach it through the route.
    for route in app.routes:
        if getattr(route, "path", "") == "/v1/messages":
            cells = route.endpoint.__closure__ or ()
            for cell in cells:
                obj = cell.cell_contents
                if hasattr(obj, "handle_chat_completion"):
                    monkeypatch.setattr(
                        obj, "handle_chat_completion", handle, raising=False
                    )
    return TestClient(app, raise_server_exceptions=False)


def test_midstream_failure_emits_error_not_clean_stop(monkeypatch) -> None:
    c = _client_with_stub(monkeypatch, fail=True)
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
        assert r.status_code == 200
        text = "".join(line for line in r.iter_lines() if line.strip())
    assert "event: error" in text
    assert "upstream died mid-stream" in text
    # A message_stop after an error would present the truncation as success.
    assert "event: message_stop" not in text


def test_clean_stream_still_emits_message_stop(monkeypatch) -> None:
    c = _client_with_stub(monkeypatch, fail=False)
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
        text = "".join(line for line in r.iter_lines() if line.strip())
    assert "event: message_start" in text
    assert "event: message_stop" in text
    assert "event: error" not in text
    assert "partial " in text
