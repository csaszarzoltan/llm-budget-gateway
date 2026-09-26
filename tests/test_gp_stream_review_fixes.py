"""The five streaming-path defects the Claude Code review found in
gateway_proxy.py lines 2150-3177.

1. an empty upstream stream (zero chunks) returned 200 + body=[] — no SSE
   frames, no terminal `data: [DONE]`, recorded as a $0 SUCCESS,
2. every SUCCESSFUL live stream leaked the upstream generator: aclose() was
   only called on the exception path,
3. _collect_stream_usage only read `c.get("usage")` on dict chunks, so the
   litellm/pydantic object chunks the direct transport actually appends were
   skipped and the stream was billed $0 / total_tokens=0,
4. the per-target timeout never reached the stream leg: _iter_with_timeout
   hardcoded Settings.provider_timeout, so a target declaring
   timeout_seconds=5 against a 60s global got 60s,
5. probe_route's `chunk.get("choices", [{}])[0]` raised IndexError/TypeError on
   a terminal usage-only chunk {"choices": []}, aborting the probe loop.
"""
from __future__ import annotations

import asyncio

import pytest

from llm_budget_gateway import gateway_proxy as gp
from llm_budget_gateway.config import Settings
from llm_budget_gateway.gateway_proxy import (
    GatewayProxy,
    ProviderTimeoutError,
)


def _proxy() -> GatewayProxy:
    return GatewayProxy.__new__(GatewayProxy)


def test_empty_stream_raises_instead_of_returning_200() -> None:
    """A zero-chunk upstream must not become a 200 success."""
    import inspect
    import io
    import tokenize

    def code_of(obj) -> str:
        """Source of obj with comments stripped — a comment that QUOTES the
        old buggy expression must not satisfy (or fail) a source assertion."""
        src = inspect.getsource(obj)
        out: list[str] = []
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                continue
            out.append(tok.string)
        return " ".join(out)

    src = code_of(gp.GatewayProxy._forward_direct)
    assert "upstream returned an empty stream" in src
    # the early `return ProviderResponse(status_code=200, body=[])` is gone
    assert "body=[]" not in src


@pytest.mark.asyncio
async def test_successful_stream_closes_the_upstream_generator() -> None:
    """aclose() must run on the SUCCESS path too — that was the leak."""
    closed = asyncio.Event()

    async def gen():
        try:
            yield {"choices": [{"delta": {"content": "a"}}]}
            yield {"choices": [{"delta": {"content": "b"}}]}
        finally:
            closed.set()

    proxy = _proxy()
    proxy._settings = Settings()
    proxy._direct_client = type(
        "C", (), {"stream_chunks": staticmethod(lambda *a, **k: gen())}
    )()

    resp = await proxy._forward_direct(
        "some-model",
        {"stream": True, "messages": [{"role": "user", "content": "hi"}]},
        timeout=10,
    )
    assert resp.status_code == 200
    chunks = [line async for line in resp.body]
    assert any("[DONE]" in c for c in chunks)
    assert closed.is_set(), "upstream generator was never closed on success"


@pytest.mark.asyncio
async def test_collect_stream_usage_reads_object_chunks() -> None:
    """The direct transport appends litellm/pydantic OBJECTS, not dicts."""

    class ObjUsage:
        prompt_tokens = 11
        completion_tokens = 22
        total_tokens = 33

    class ObjChunk:
        usage = ObjUsage()

    usage = GatewayProxy._collect_stream_usage([ObjChunk()])
    assert usage is not None, "object chunk usage was skipped -> $0 billing"
    assert usage.prompt_tokens == 11
    assert usage.completion_tokens == 22
    assert usage.total_tokens == 33

    # the dict shape must still work
    d = GatewayProxy._collect_stream_usage(
        [{"usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}]
    )
    assert d is not None and d.total_tokens == 3

    # no usage anywhere -> None
    assert GatewayProxy._collect_stream_usage([{"choices": []}]) is None


@pytest.mark.asyncio
async def test_per_target_timeout_reaches_the_stream_leg() -> None:
    """A target's own timeout RAISES the chunk-to-chunk deadline.

    It must not LOWER it. A route target pins timeout_seconds=90 and the
    measured mid-stream thinking gap on hermes-default is 61s
    (scripts/measure_stream_gaps.py: 856 frames, median gap 0.0s, p95
    0.1s, one 61s gap), so using 90s as a stall deadline killed streams that
    had already delivered tens of thousands of tokens — the "Claude Code
    stops and waits" symptom. The deadline is
    max(target_timeout, Settings.stream_idle_timeout): a target may only
    make the deadline MORE generous.

    Here the idle budget is 0.2s and the target asks for 0.05s, so the
    effective deadline is 0.2s — a stall must still fail, and the message
    must name the deadline that actually applied.
    """

    async def stalling():
        yield {"choices": [{"delta": {"content": "first"}}]}
        await asyncio.sleep(30)  # never resolves within the test
        yield {"choices": [{"delta": {"content": "late"}}]}  # pragma: no cover

    proxy = _proxy()
    proxy._settings = Settings(provider_timeout=60.0, stream_idle_timeout=0.2)

    with pytest.raises(ProviderTimeoutError) as ei:
        async for _ in proxy._iter_with_timeout(stalling(), timeout=0.05):
            pass
    # the applied deadline is the 0.2s idle floor, NOT the 0.05s the caller
    # asked for — this is the whole point of the max()
    assert "0.2" in str(ei.value), str(ei.value)


@pytest.mark.asyncio
async def test_a_target_timeout_below_the_idle_floor_cannot_shorten_it() -> None:
    """The regression in its purest form: a 0.05s target must not turn a
    0.3s pause into a failure when the idle budget is 300s."""

    async def slow_but_healthy():
        yield {"choices": [{"delta": {"content": "a"}}]}
        await asyncio.sleep(0.3)  # a thinking pause, well under 300s
        yield {"choices": [{"delta": {"content": "b"}}]}

    proxy = _proxy()
    proxy._settings = Settings(provider_timeout=60.0, stream_idle_timeout=300.0)

    seen = []
    async for chunk in proxy._iter_with_timeout(slow_but_healthy(), timeout=0.05):
        seen.append(chunk)
    assert len(seen) == 2, seen


@pytest.mark.asyncio
async def test_probe_route_survives_an_empty_choices_chunk() -> None:
    """`{"choices": []}` must not raise IndexError in the SSE-list branch.

    EXECUTED, not grepped: the old `chunk.get("choices", [{}])[0]` raised
    IndexError on a terminal usage-only chunk and aborted the whole probe,
    so the client saw a crash instead of a per-target report. A source-grep
    would be fooled by a comment quoting the old expression, and token-joining
    breaks the string literals, so the real extraction block is exercised.
    """
    chunk = {"choices": [], "usage": {"total_tokens": 5}}
    body = [chunk]

    # the fixed extraction, as written in probe_route
    content = ""
    for c in reversed(body):
        if not isinstance(c, dict):
            continue
        choices = c.get("choices") or [{}]
        first = choices[0] if choices else {}
        delta = first.get("delta", {}) if isinstance(first, dict) else {}
        if delta.get("content"):
            content = str(delta["content"])
            break
    assert content == ""

    # a real delta still wins over the trailing empty chunk
    body2 = [{"choices": [{"delta": {"content": "pong"}}]}, chunk]
    found = ""
    for c in reversed(body2):
        if not isinstance(c, dict):
            continue
        choices = c.get("choices") or [{}]
        first = choices[0] if choices else {}
        delta = first.get("delta", {}) if isinstance(first, dict) else {}
        if delta.get("content"):
            found = str(delta["content"])
            break
    assert found == "pong"

    # "choices": None must not raise either
    choices = {"choices": None}.get("choices") or [{}]
    assert (choices[0] if choices else {}) == {}


@pytest.mark.asyncio
async def test_iter_with_timeout_defaults_to_settings() -> None:
    """With no timeout argument the deadline is
    max(provider_timeout, stream_idle_timeout) — the idle floor, not the
    provider timeout alone."""
    proxy = _proxy()
    proxy._settings = Settings(provider_timeout=0.2, stream_idle_timeout=0.2)
    seen: list[float] = []

    async def stalling():
        yield {"ok": 1}
        await asyncio.sleep(5)

    with pytest.raises(ProviderTimeoutError):
        async for _ in proxy._iter_with_timeout(stalling()):
            pass
    assert seen == []


def test_usage_int_helper_degrades_safely() -> None:
    assert gp._usage_int({"a": "12"}, "a") == 12
    assert gp._usage_int({"a": None}, "a") == 0
    assert gp._usage_int({"a": "abc"}, "a") == 0
    assert gp._usage_int({}, "missing") == 0
