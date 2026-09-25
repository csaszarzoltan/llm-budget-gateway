"""The five chain/stream defects the Claude Code review found in
gateway_proxy.py lines 1100-2150.

1. CACHE NAMESPACE COLLISION: the cache key was a hardcoded "default", so an
   identical body sent to route A and then route B returned route A's cached
   body AND model to route B — a different model's output than requested,
2. MIXED-MODEL STREAM: once any chunk was yielded the wrapper re-drove a
   fallback candidate, splicing the dead model's partial prefix onto the
   fallback's full reply — two models' output in one SSE stream,
3. DOUBLE MID-STREAM DEATH: the re-driven candidate's own MidStreamFailure
   escaped uncaught to ASGI (no [DONE]) while the finally still billed it
   as success,
4. RETRY DISCARDED A REAL RESPONSE: _retry_on_timeout returned the retry's
   ProviderResponse but the caller stayed in the except branch, reporting a
   timeout and hiding the provider's real status/body,
5. RETRIES IGNORED THE CHAIN BUDGET: both retry helpers reused the first
   candidate's target_timeout and slept full backoffs, so an 80s target
   with 2 retries blew far past a 90s chain budget.
"""
from __future__ import annotations

import time

import pytest

from llm_budget_gateway import gateway_proxy as gp
from llm_budget_gateway.config import Settings
from llm_budget_gateway.gateway_proxy import (
    GatewayProxy,
    MidStreamFailure,
    ProviderResponse,
    ProviderTimeoutError,
)


def _proxy() -> GatewayProxy:
    p = GatewayProxy.__new__(GatewayProxy)
    p._settings = Settings()
    return p


def test_cache_namespace_is_per_route() -> None:
    """Two routes must not share a cache entry."""
    a = gp._cache_namespace("route-a", "alias-a")
    b = gp._cache_namespace("route-b", "alias-b")
    assert a != b
    assert a.startswith("route:")
    # the same route is stable
    assert gp._cache_namespace("route-a", "route-a") == a
    # a missing route still yields a usable key
    assert gp._cache_namespace(None, None)


def test_cache_key_is_namespaced_in_both_directions() -> None:
    """The lookup AND the write must use the route namespace."""
    import inspect

    prep = inspect.getsource(GatewayProxy._prepare_route_request)
    final = inspect.getsource(GatewayProxy._finalize_route_response)
    assert '_intel_cache.get("default"' not in prep
    assert '_intel_cache.put(\n                    "default"' not in final
    assert "_cache_namespace(" in prep
    assert "_cache_namespace(" in final


@pytest.mark.asyncio
async def test_midstream_after_chunks_does_not_redrive(monkeypatch) -> None:
    """After the client has chunks, a re-drive would MIX two models."""
    import inspect

    src = inspect.getsource(GatewayProxy._finalize_route_response)
    # an emitted-chunks guard must exist before the re-drive
    assert "emitted" in src
    assert "if emitted:" in src
    # the re-drive itself is still there for the zero-chunk case
    assert "_run_candidate_chain" in src


@pytest.mark.asyncio
async def test_double_midstream_failure_is_caught() -> None:
    """The re-driven candidate's own death must be caught, not escape."""
    import inspect

    src = inspect.getsource(GatewayProxy._finalize_route_response)
    assert src.count("except MidStreamFailure") >= 2, (
        "the fallback drain needs its own handler"
    )


@pytest.mark.asyncio
async def test_retry_on_timeout_returns_the_real_response() -> None:
    """A retry that came back 429 must be surfaced, not reported as a timeout."""
    proxy = _proxy()
    calls: list[str] = []

    async def forward(model, body, timeout=None):
        calls.append(model)
        if len(calls) == 1:
            raise ProviderTimeoutError("first attempt")
        return ProviderResponse(
            status_code=429,
            body={"error": "rate limited"},
            headers={},
            model=model,
            usage=None,
            latency_ms=1,
        )

    proxy.forward = forward
    proxy._cost_tracker = type("T", (), {"record_success": lambda *a, **k: None})()
    resp, succeeded = await proxy._retry_on_timeout(
        candidate="m",
        outbound={},
        route_name="r",
        target_retries=2,
        target_timeout=5.0,
        is_last=True,
        request_id="req",
    )
    assert succeeded is False
    # the 429 is the retry's real answer and must be returned to the caller
    assert resp is not None
    assert resp.status_code == 429
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_retry_timeout_is_capped_by_the_chain_deadline() -> None:
    """A spent chain budget must stop the retry, not burn 80s more."""
    proxy = _proxy()
    seen_timeouts: list[float | None] = []

    async def forward(model, body, timeout=None):
        seen_timeouts.append(timeout)
        raise ProviderTimeoutError("nope")

    proxy.forward = forward
    proxy._settings = Settings(retry_backoff_seconds=0.0, retry_backoff_max_seconds=0.0)
    proxy._cost_tracker = type("T", (), {"record_success": lambda *a, **k: None})()

    # a deadline already in the past -> no attempt at all
    resp, succeeded = await proxy._retry_on_timeout(
        candidate="m",
        outbound={},
        route_name="r",
        target_retries=3,
        target_timeout=80.0,
        is_last=False,
        request_id="req",
        chain_deadline=time.perf_counter() - 1.0,
    )
    assert seen_timeouts == [], "retried despite a spent chain budget"
    assert succeeded is False

    # a live deadline caps the per-attempt timeout below the target's own
    seen_timeouts.clear()
    await proxy._retry_on_timeout(
        candidate="m",
        outbound={},
        route_name="r",
        target_retries=1,
        target_timeout=80.0,
        is_last=False,
        request_id="req",
        chain_deadline=time.perf_counter() + 2.0,
    )
    assert seen_timeouts and seen_timeouts[0] is not None
    assert seen_timeouts[0] <= 2.0, seen_timeouts


@pytest.mark.asyncio
async def test_retry_transient_respects_the_chain_deadline() -> None:
    proxy = _proxy()
    calls: list[float | None] = []

    async def forward(model, body, timeout=None):
        calls.append(timeout)
        return ProviderResponse(
            status_code=503,
            body={},
            headers={},
            model=model,
            usage=None,
            latency_ms=1,
        )

    proxy.forward = forward
    proxy._settings = Settings(retry_backoff_seconds=0.0, retry_backoff_max_seconds=0.0)
    first = ProviderResponse(
        status_code=503, body={}, headers={}, model="m", usage=None, latency_ms=1
    )
    await proxy._retry_transient(
        response=first,
        candidate="m",
        outbound={},
        route_name="r",
        target_retries=3,
        target_timeout=60.0,
        is_last=False,
        request_id="req",
        chain_deadline=time.perf_counter() - 1.0,
    )
    assert calls == [], "retried despite a spent chain budget"


@pytest.mark.asyncio
async def test_midstream_failure_carries_the_candidate() -> None:
    """Sanity: the exception the wrapper catches is the real one."""
    exc = MidStreamFailure("model-x", "boom", body="", chunks_yielded=3)
    assert exc.candidate == "model-x"
    assert exc.chunks_yielded == 3
