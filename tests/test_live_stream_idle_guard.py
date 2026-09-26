"""The live-stream leg must have the same idle guard as the drained one.

`_rest_stream` iterated the upstream generator directly with
`async for chunk in agen:`, so `_iter_with_timeout` — and therefore the
per-target stall deadline — applied ONLY to the drained path. Live streaming
had no mid-stream bound at all: a hung upstream held a worker forever while
the same model on the same route was protected when drained.

The fix routes the live leg through the same deadline and surfaces a stall
as a MidStreamFailure, so the route layer re-drives the remaining candidates
instead of hanging the client.

The source-level checks pin the invariant: a behavioural test of a private
generator would be a rewrite of it, and the real regression here was that the
loop went back to iterating the generator directly.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from llm_budget_gateway.config import Settings
from llm_budget_gateway.gateway_proxy import GatewayProxy


# --- the invariant, pinned at the source level -------------------------------

def test_rest_stream_does_not_iterate_the_generator_directly() -> None:
    """The exact regression: `async for chunk in agen` bypasses the deadline."""
    src = inspect.getsource(GatewayProxy._forward_direct)
    body = src[src.index("async def _rest_stream") :]
    assert "async for chunk in agen" not in body, (
        "_rest_stream iterates the upstream generator directly again, so a "
        "mid-stream stall is unbounded on the live leg"
    )


def test_rest_stream_waits_per_chunk_on_the_idle_deadline() -> None:
    src = inspect.getsource(GatewayProxy._forward_direct)
    body = src[src.index("async def _rest_stream") :]
    assert "stall_deadline" in body
    assert "asyncio.wait_for" in body
    # the floor comes from the shared idle setting, not the target timeout
    assert "stream_idle_timeout" in body


def test_drained_leg_still_guards_on_the_idle_deadline() -> None:
    src = inspect.getsource(GatewayProxy._iter_with_timeout)
    assert "stream_idle_timeout" in src
    assert "max(float(requested), idle)" in src


# --- the setting itself ------------------------------------------------------

def test_idle_default_clears_the_measured_thinking_gap() -> None:
    """scripts/measure_stream_gaps.py measured a 61s mid-stream gap on
    hermes-default (856 frames, p95 0.1s). The default must clear it with
    room to spare, or long Claude Code turns stall again."""
    assert Settings().stream_idle_timeout >= 300.0


def test_idle_is_configurable_from_the_environment() -> None:
    settings = Settings(stream_idle_timeout=42.0)
    assert settings.stream_idle_timeout == 42.0


def test_idle_is_independent_of_provider_timeout() -> None:
    """provider_timeout bounds the FIRST BYTE; the idle budget bounds a
    mid-stream pause. Collapsing them is what caused the symptom."""
    s = Settings(provider_timeout=90.0, stream_idle_timeout=300.0)
    assert s.provider_timeout == 90.0
    assert s.stream_idle_timeout == 300.0
    assert s.provider_timeout != s.stream_idle_timeout


# --- the mechanism, exercised on its own ------------------------------------

class _Stalling:
    def __init__(self, before: list[dict], stall: float) -> None:
        self._before = list(before)
        self._stall = stall

    def __aiter__(self) -> _Stalling:
        return self

    async def __anext__(self) -> dict:
        if self._before:
            return self._before.pop(0)
        await asyncio.sleep(self._stall)
        raise StopAsyncIteration


def _drain_like_rest_stream(agen, effective_timeout: float, idle: float) -> list[dict]:
    """The loop shape _rest_stream now uses, in isolation."""
    stall_deadline = max(float(effective_timeout), idle)
    aiter = agen.__aiter__()
    out: list[dict] = []

    async def run() -> None:
        while True:
            try:
                chunk = await asyncio.wait_for(
                    aiter.__anext__(), timeout=stall_deadline
                )
            except StopAsyncIteration:
                break
            except TimeoutError as stall:
                raise RuntimeError(
                    f"upstream stream stalled for {stall_deadline}s"
                ) from stall
            out.append(chunk)

    asyncio.run(run())
    return out


def test_the_loop_shape_fails_on_a_stall() -> None:
    with pytest.raises(RuntimeError, match="stalled"):
        _drain_like_rest_stream(_Stalling([{"a": 1}], stall=5.0), 0.05, 0.05)


def test_the_loop_shape_survives_a_healthy_stream() -> None:
    out = _drain_like_rest_stream(_Stalling([{"a": 1}, {"a": 2}], stall=0.0), 90.0, 300.0)
    assert out == [{"a": 1}, {"a": 2}]


def test_a_target_timeout_cannot_reintroduce_the_kill() -> None:
    """A 90s target must not shrink the 300s idle floor — that is the whole
    point of max(), and the bug was that only the target value was used."""
    agen = _Stalling([{"a": 1}], stall=0.3)
    out = _drain_like_rest_stream(agen, effective_timeout=0.05, idle=300.0)
    assert out == [{"a": 1}], "the 0.3s pause must survive a 0.05s target"
