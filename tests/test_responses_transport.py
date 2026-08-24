"""TDD: Codex Responses API transport for provider connections.

muse-spark on opencode.ai/zen/go serves /responses (Codex protocol) but its
/chat/completions shim returns HTTP 500 (2026-08-23 outage). Hermes talks to
it via api_mode=codex_responses; the gateway direct transport only spoke
chat_completions. Add per-connection ``api_mode`` so a provider connection
can route chat traffic through POST /responses with request/response
translation.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from llm_budget_gateway.provider_direct import (
    DirectProviderClient,
    ProviderEndpoint,
)


def _endpoint(api_mode: str = "chat_completions") -> ProviderEndpoint:
    return ProviderEndpoint(
        name="opencode-go",
        base_url="https://zen.example/v1",
        api_key_env="",
        models=("muse-spark-1.2-contributor",),
        api_key_value="test-key",
        user_agent="opencode/1.14.41",
        extra_body=None,
    )


def _client_with(monkeypatch, respond) -> tuple[DirectProviderClient, list[httpx.Request]]:
    """Build a client whose HTTP posts are captured; ``respond`` builds the JSON."""
    calls: list[dict] = []

    async def _post(url, json=None, headers=None):  # noqa: A002
        calls.append({"url": url, "json": json, "headers": headers})
        return httpx.Response(200, json=respond(json), request=httpx.Request("POST", url))


    client = DirectProviderClient(registry={})
    ep = _endpoint()
    client._registry["opencode-go"] = ep
    client._model_index["muse-spark-1.2-contributor"] = ep
    client._model_index["@opencode-go/muse-spark-1.2-contributor"] = ep
    # Inject the stub transport (bypasses real network).
    object.__setattr__(client, "_client", type("C", (), {"post": staticmethod(_post), "aclose": staticmethod(lambda: None)})())
    return client, calls


def _responses_payload(request_json: dict[str, Any]) -> dict[str, Any]:
    """Mirror the real opencode zen /responses shape (verified live 2026-08-24)."""
    return {
        "id": "resp_123",
        "object": "response",
        "status": "completed",
        "model": request_json.get("model"),
        "output": [
            {
                "type": "reasoning",
                "id": "rs_1",
                "summary": [],
            },
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": "pong", "annotations": []}
                ],
            },
        ],
        "usage": {
            "input_tokens": 12,
            "output_tokens": 3,
            "total_tokens": 15,
        },
    }


@pytest.mark.asyncio
async def test_api_mode_responses_posts_to_responses_endpoint(monkeypatch):
    """api_mode=codex_responses → POST {base}/responses with translated body."""
    client, calls = _client_with(
        monkeypatch, lambda payload: _responses_payload(payload)
    )
    ep = client.resolve("muse-spark-1.2-contributor")
    object.__setattr__(ep, "api_mode", "codex_responses")

    status, data, served = await client.forward(
        "muse-spark-1.2-contributor",
        {
            "messages": [{"role": "user", "content": "Say exactly: pong"}],
            "max_tokens": 30,
        },
    )

    assert status == 200
    assert len(calls) == 1
    assert calls[0]["url"].endswith("/responses")
    sent = calls[0]["json"]
    assert sent["model"] == "muse-spark-1.2-contributor"
    assert "input" in sent and "messages" not in sent
    assert sent["max_output_tokens"] == 30
    # Response translated back to chat-completions shape for callers.
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert data["choices"][0]["message"]["content"] == "pong"
    assert data["usage"]["prompt_tokens"] == 12
    assert data["usage"]["completion_tokens"] == 3
    assert data["usage"]["total_tokens"] == 15
    assert served == "muse-spark-1.2-contributor"


@pytest.mark.asyncio
async def test_api_mode_default_stays_chat_completions(monkeypatch):
    """Without api_mode the URL must remain /chat/completions (no regression)."""
    client, calls = _client_with(
        monkeypatch,
        lambda p: {
            "id": "c1",
            "model": p["model"],
            "choices": [{"message": {"role": "assistant", "content": "pong"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    )
    status, data, _served = await client.forward(
        "muse-spark-1.2-contributor",
        {"messages": [{"role": "user", "content": "hi"}]},
    )
    assert status == 200
    assert calls[0]["url"].endswith("/chat/completions")
    assert "messages" in calls[0]["json"]


@pytest.mark.asyncio
async def test_stream_chunks_uses_responses_and_translates(monkeypatch):
    """api_mode=codex_responses + stream → /responses SSE translated to chat chunks."""
    lines = [
        'data: {"type":"response.output_text.delta","item_id":"it1","delta":"po"}',
        'data: {"type":"response.output_text.delta","item_id":"it1","delta":"ng"}',
        'data: {"type":"response.completed","response":{"id":"resp_9","model":"muse-spark-1.2-contributor","status":"completed","usage":{"input_tokens":10,"output_tokens":5,"total_tokens":15}}}',
        "data: [DONE]",
    ]

    class _StreamCtx:
        async def __aenter__(self):
            class R:
                status_code = 200

                async def aread(self):
                    return b""

                async def aiter_lines(self):
                    for ln in lines:
                        yield ln

            return R()

        async def __aexit__(self, *exc):
            return False

    captured = {}

    def _stream(method, url, json=None, headers=None):  # noqa: A002
        captured["url"] = url
        captured["json"] = json
        return _StreamCtx()

    client = DirectProviderClient(registry={})
    ep = _endpoint()
    object.__setattr__(ep, "api_mode", "codex_responses")
    client._registry["opencode-go"] = ep
    client._model_index["muse-spark-1.2-contributor"] = ep
    object.__setattr__(
        client, "_client", type("C", (), {"stream": staticmethod(_stream)})()
    )

    chunks = []
    async for chunk in client.stream_chunks(
        "muse-spark-1.2-contributor",
        {
            "messages": [{"role": "user", "content": "Say exactly: pong"}],
            "max_tokens": 100,
            "stream": True,
        },
    ):
        chunks.append(chunk)

    assert captured["url"].endswith("/responses")
    assert captured["json"]["stream"] is True
    assert captured["json"]["input"][0]["content"][0]["text"] == "Say exactly: pong"
    texts = [
        c["choices"][0]["delta"].get("content", "")
        for c in chunks
        if c["choices"][0]["delta"].get("content")
    ]
    assert "".join(texts) == "pong"
    last = chunks[-1]
    assert last["choices"][0]["finish_reason"] == "stop"
    assert last["usage"]["total_tokens"] == 15


@pytest.mark.asyncio
async def test_stream_chunks_default_stays_chat_completions():
    """Without api_mode the streaming URL must remain /chat/completions."""
    captured = {}

    class _StreamCtx:
        async def __aenter__(self):
            class R:
                status_code = 200

                async def aread(self):
                    return b""

                async def aiter_lines(self):
                    yield 'data: {"id":"c1","choices":[{"index":0,"delta":{"content":"hi"}}]}'
                    yield "data: [DONE]"

            return R()

        async def __aexit__(self, *exc):
            return False

    def _stream(method, url, json=None, headers=None):  # noqa: A002
        captured["url"] = url
        return _StreamCtx()

    client = DirectProviderClient(registry={})
    ep = _endpoint()  # default chat_completions
    client._registry["opencode-go"] = ep
    client._model_index["muse-spark-1.2-contributor"] = ep
    object.__setattr__(
        client, "_client", type("C", (), {"stream": staticmethod(_stream)})()
    )
    chunks = []
    async for chunk in client.stream_chunks(
        "muse-spark-1.2-contributor", {"messages": [{"role": "user", "content": "x"}]}
    ):
        chunks.append(chunk)
    assert captured["url"].endswith("/chat/completions")
