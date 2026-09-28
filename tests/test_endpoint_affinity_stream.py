"""Streamed requests must skip the dead endpoint too, not just non-streamed.

The user's OpenCode console shows the pattern directly, one 200 next to one
`400 Rejected · interface_failed_failed` per request, same model, same
credentials, 65–282ms between the two timestamps:

    13:12:43   200  Space Bunny  8.24s
    13:12:43   400  Space Bunny  65ms   <- the /responses attempt
    13:12:52   200  Space Bunny  5.2s
    13:12:52   400  Space Bunny  120ms
    13:12:59   200  Space Bunny  5.53s
    13:12:59   400  Space Bunny  81ms
    13:13:06   200  Space Bunny  12.32s
    13:13:06   400  Space Bunny  107ms
    13:13:21   200  Space Bunny  6.32s
    13:13:21   400  Space Bunny  282ms

The gateway's own `cost_records` shows ONE 200 per request, so the rejected
call is the internal /responses attempt, not a second client request.

`forward` learned the endpoint affinity (test_endpoint_affinity.py), but
`forward_stream` still tries /responses first on every streamed request — and
Hermes streams, so this is the path the user actually pays on.

Measured cost of the dead step, 8 calls:
    avg 0.477s   median 0.461s   max 0.617s

Each test below fails against the current code.
"""
from __future__ import annotations

import sys
import types

import pytest

SRC = "/home/zoltan/llm-budget-gateway/src"
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from llm_budget_gateway.provider_direct import (  # noqa: E402
    DirectProviderClient,
    ProviderEndpoint,
    UpstreamProviderError,
)

PROTOCOL_400 = (
    '{"type":"error","error":{"type":"ModelProtocolUnsupported",'
    '"message":"Model does not support this protocol."}}'
)


def _client() -> DirectProviderClient:
    cl = DirectProviderClient(
        registry={"opencode-go": {
            "base_url": "https://example.invalid/v1",
            "api_key_env": "__vault_opencode-go__",
            "api_key": "k",
            "models": ["m"],
            "api_mode": "codex_responses",
            "extra_headers": {"x-opencode-session": "hermes-gateway"},
        }},
        timeout=30.0,
        idle_timeout=30.0,
    )
    cl.resolve = lambda model: ProviderEndpoint(
        name="opencode-go",
        base_url="https://example.invalid/v1",
        api_key_env="__vault_opencode-go__",
        api_key_value="k",
        models=("m",),
        api_mode="codex_responses",
        extra_headers={"x-opencode-session": "hermes-gateway"},
    )
    cl._request_url = lambda ep, kind: "https://example.invalid/v1/chat/completions"
    cl._client = types.SimpleNamespace()
    return cl


class _FakeStreamResponse:
    """The shape the chat stream path consumes.

    `self._client.stream(...)` is used as an async context manager, so the
    object returned by the mock must be the CM itself, not a coroutine that
    produces one — that mismatch reads as "coroutine object does not support
    the asynchronous context manager protocol".
    """

    def __init__(self, status: int, lines: list[str]) -> None:
        self.status_code = status
        self.headers: dict[str, str] = {}
        self.text = ""
        self._lines = lines

    def __call__(self, *args, **kwargs) -> _FakeStreamResponse:
        """Let the same object stand in for `client.stream(...)`."""
        return self

    async def __aenter__(self) -> _FakeStreamResponse:
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line


def _body() -> dict:
    return {"max_tokens": 32, "messages": [{"role": "user", "content": "hi"}], "stream": True}


async def _collect(gen) -> list:
    return [chunk async for chunk in gen]


@pytest.mark.asyncio
async def test_a_streamed_request_skips_the_dead_endpoint_after_learning() -> None:
    """The whole point: Hermes streams, so the stream path is the one used."""
    cl = _client()
    attempts: list[str] = []

    async def _sr(endpoint, model, body):
        attempts.append("responses")
        raise UpstreamProviderError(
            400, "upstream provider error: opencode-go (HTTP 400)", body=PROTOCOL_400
        )
        yield b""  # pragma: no cover - makes this an async generator

    def _stream(*args, **kwargs):
        attempts.append("chat")
        return _FakeStreamResponse(200, ['data: {"x": 1}', "", "data: [DONE]", ""])

    cl._stream_responses = _sr
    cl._client.stream = _stream

    for i in range(3):
        chunks = await _collect(cl.stream_chunks("@opencode-go/m", _body(), kind="chat"))
        assert chunks, "the stream must still deliver its chunks"

    assert attempts == ["responses", "chat", "chat", "chat"], (
        f"a streamed request must not retry the dead endpoint once it is "
        f"learned; got {attempts}"
    )


@pytest.mark.asyncio
async def test_the_learned_endpoint_is_shared_between_stream_and_forward() -> None:
    """A request that learned on one path must not re-probe on the other.

    Otherwise a mixed workload (some streamed, some not) still pays the dead
    call every time the path alternates.
    """
    cl = _client()
    attempts: list[str] = []

    async def _sr(endpoint, model, body):
        attempts.append("stream-responses")
        raise UpstreamProviderError(
            400, "upstream provider error: opencode-go (HTTP 400)", body=PROTOCOL_400
        )
        yield b""

    async def _fr(endpoint, model, body, target_seconds=None):
        attempts.append("forward-responses")
        raise UpstreamProviderError(
            400, "upstream provider error: opencode-go (HTTP 400)", body=PROTOCOL_400
        )

    def _stream(*args, **kwargs):
        attempts.append("chat")
        return _FakeStreamResponse(200, ['data: {"x": 1}', ""])

    async def _post(url, **kwargs):
        attempts.append("chat")
        return types.SimpleNamespace(
            status_code=200, headers={}, text="",
            json=lambda: {"model": "m", "choices": [{"message": {"content": "ok"}}]},
        )

    cl._stream_responses = _sr
    cl._forward_responses = _fr
    cl._client.stream = _stream
    cl._client.post = _post

    await _collect(cl.stream_chunks("@opencode-go/m", _body(), kind="chat"))
    await cl.forward("@opencode-go/m", _body(), kind="chat")

    assert attempts == ["stream-responses", "chat", "chat"], (
        f"the stream path records the affinity, and the non-stream path must "
        f"honour it instead of probing again; got {attempts}"
    )


@pytest.mark.asyncio
async def test_a_mid_stream_death_is_not_learned_as_an_endpoint_refusal() -> None:
    """Only a pre-first-byte refusal teaches the affinity.

    A stream that already delivered content and then died is a different
    failure: switching endpoints mid-stream would mix two streams, and the
    existing code refuses to do it. It must not be recorded either, or a
    healthy /responses model that died once would be pinned to chat.
    """
    cl = _client()
    attempts: list[str] = []

    async def _sr(endpoint, model, body):
        attempts.append("responses")
        yield b"data: {\"partial\": 1}\n\n"
        raise UpstreamProviderError(500, "upstream blew up mid-stream", body="boom")
        yield b""  # pragma: no cover

    cl._stream_responses = _sr
    cl._client.post = None

    with pytest.raises(UpstreamProviderError):
        await _collect(cl.stream_chunks("@opencode-go/m", _body(), kind="chat"))

    assert attempts == ["responses"], (
        f"a mid-stream death must not switch endpoints, got {attempts}"
    )
    assert cl._known_endpoint("@opencode-go/m") is None, (
        "a mid-stream death says nothing about which endpoint to use"
    )
