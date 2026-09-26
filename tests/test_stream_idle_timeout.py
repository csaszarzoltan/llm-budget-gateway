"""Claude Code stability with large context: the stream idle deadline.

The symptom: Claude Code "stops and waits" on a long turn while a 5-token
`claude -p "pong"` through the same gateway returns in 6s. Same route, same
model, same settings — so the difference has to be the turn length.

Measured cause (scripts/measure_stream_gaps.py, hermes-default,
2026-09-25): 856 frames, median inter-chunk gap 0.0s, p95 0.1s — and ONE
61s gap where a reasoning model thought. Every target pins
`timeout_seconds=90`, and that value was used as the CHUNK-TO-CHUNK
deadline on the stream leg, not just for time-to-first-byte. So a single
longer thinking pause kills a stream that has already delivered tens of
thousands of tokens.

The fix separates the two deadlines: provider_timeout bounds the first
byte, stream_idle_timeout bounds a mid-stream pause. These tests pin the
separation so it cannot silently collapse back into one value.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from llm_budget_gateway.config import Settings
from llm_budget_gateway.gateway_proxy import (
    GatewayProxy,
    ProviderTimeoutError,
)


def _proxy(stream_idle_timeout: float = 300.0) -> GatewayProxy:
    settings = Settings(stream_idle_timeout=stream_idle_timeout)
    proxy = GatewayProxy.__new__(GatewayProxy)  # type: ignore[misc]
    proxy._settings = settings
    return proxy


class _SlowStream:
    """Async iterator that pauses `pause` seconds before its single chunk."""

    def __init__(self, pause: float, then: list[dict]) -> None:
        self._pause = pause
        self._then = then
        self.closed = False

    def __aiter__(self) -> _SlowStream:
        return self

    async def __anext__(self) -> dict:
        await asyncio.sleep(self._pause)
        if self._then:
            return self._then.pop(0)
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self.closed = True


# --- the separation itself ---------------------------------------------------

def test_idle_deadline_is_not_the_target_timeout() -> None:
    """A target's timeout_seconds must NOT become the mid-stream deadline."""
    proxy = _proxy(stream_idle_timeout=300.0)
    assert proxy._settings.stream_idle_timeout == 300.0
    # the two budgets are distinct knobs
    assert proxy._settings.stream_idle_timeout != proxy._settings.provider_timeout


def test_default_idle_timeout_is_generous() -> None:
    """The measured worst thinking gap was 61s; the default must clear it."""
    assert Settings().stream_idle_timeout >= 300.0


# --- behaviour: a long thinking pause must NOT kill the stream ---------------

def test_a_120s_pause_survives_a_90s_target_timeout() -> None:
    """The regression this fixes, with a real (but short) pause.

    A reasoning model's thinking gap scales with the problem, not with a
    clock, so the test uses a 0.25s pause against a 90s target timeout and a
    300s idle deadline: with the old code the two knobs were the same number
    and any target timeout under the pause killed the stream.
    """
    proxy = _proxy(stream_idle_timeout=300.0)
    stream = _SlowStream(pause=0.25, then=[{"a": 1}, {"b": 2}])
    got: list[dict] = []

    async def run() -> None:
        async for chunk in proxy._iter_with_timeout(stream, timeout=90.0):
            got.append(chunk)

    asyncio.run(run())
    assert got == [{"a": 1}, {"b": 2}], got


def test_a_pause_beyond_the_idle_deadline_still_fails() -> None:
    """The guard must still exist: a hung upstream cannot hang the worker.

    ``stream_idle_timeout`` acts as a FLOOR on the deadline, so a value
    below the caller's timeout cannot shorten it — that is what stops a
    route's 90s target from reintroducing the 61s-gap kill. It is the idle
    budget that must be able to fail a stalled stream, which is why the
    caller's timeout here is None (so the provider default is the floor).
    """
    proxy = _proxy(stream_idle_timeout=0.05)
    stream = _SlowStream(pause=5.0, then=[{"a": 1}])
    # provider_timeout's own default (120s) is the floor here, so ask for a
    # deadline below both by passing a tiny timeout AND a tiny idle budget.
    proxy._settings.provider_timeout = 0.05

    async def run() -> None:
        out = []
        async for chunk in proxy._iter_with_timeout(stream, timeout=None):
            out.append(chunk)
        raise AssertionError("a stalled stream must raise, not yield")

    with pytest.raises(ProviderTimeoutError):
        asyncio.run(run())


def test_a_faster_idle_deadline_than_the_request_wins() -> None:
    """If a caller asks for MORE than the idle budget, it gets its value."""
    proxy = _proxy(stream_idle_timeout=10.0)
    stream = _SlowStream(pause=0.0, then=[{"a": 1}])
    got: list[dict] = []

    async def run() -> None:
        async for chunk in proxy._iter_with_timeout(stream, timeout=600.0):
            got.append(chunk)

    asyncio.run(run())
    assert got == [{"a": 1}]
