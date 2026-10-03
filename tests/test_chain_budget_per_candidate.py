"""The chain budget must not disqualify the rest of the chain.

Measured on 2026-09-29, three `smart`-route 502s (10:33:13, 10:36, 10:41) all
carry the same cause in `.gateway-console/logs/gateway.log`:

    10:32:43 route=smart model=@opencode-go/mimo-v2.6-pro timed out after 90s
    10:32:43 route=smart chain budget 115.0s spent (115.0s)
            skipping @opencode-go/muse-spark-1.2-contributor
    10:33:13 route=smart model=@xiaomi/mimo-v2.6-pro chain budget spent,
            no timeout retry 1/3
    10:33:13 → 502 Bad Gateway

The budget is a single global clock started once per chain. The first
candidate burned 90s of it, so the remaining candidates were SKIPPED
without ever being tried — `muse-spark` and the xiaomi fallback were not
slow, they were never called. The route then answered 502.

Three of five candidates were unreachable by construction, not by failure.

    for index, candidate in enumerate(candidates):
        elapsed = time.perf_counter() - chain_started
        if elapsed >= chain_budget and not is_last:
            continue          # <- disqualifies on a clock the previous
            ...               #    candidate already consumed

The user's reading is the correct one: the budget should bound how long any
SINGLE candidate can hold the chain, not whether later candidates are still
reachable. A 90s stall on candidate 1 must not decide that candidate 3 is
deserving.

`test_the_remaining_candidates_are_still_reachable` is the RED proof. It
fails on the current code with `@b/mid` and `@c/last` absent from the call
list entirely.
"""
from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

SRC = "/home/zoltan/llm-budget-gateway/src"
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import pytest  # noqa: E402

from llm_budget_gateway.config import Settings  # noqa: E402
from llm_budget_gateway.gateway_proxy import (  # noqa: E402
    FallbackManager,
    GatewayProxy,
    ProviderTimeoutError,
)


def _route(*models: str, timeout_seconds: float = 120.0) -> Mock:
    """A published route whose targets all have the same timeout."""
    store = Mock()
    store.published_route_by_name.return_value = {
        "name": "smart",
        "targets": [
            {
                "model": m,
                "priority": (i + 1) * 10,
                "timeout_seconds": timeout_seconds,
                "on_status_codes": [429, 500],
            }
            for i, m in enumerate(models)
        ],
    }
    return store


def _proxy(store: Mock, budget: float) -> GatewayProxy:
    tracker = Mock()
    tracker.model_in_cooldown.return_value = 0
    tracker.build_record.return_value = SimpleNamespace(total_cost=0.0)
    settings = Settings(virtual_keys={"sk_test_abc": "k1"})
    settings.route_timeout_budget = budget
    p = GatewayProxy(
        settings=settings,
        cost_tracker=tracker,
        budget_enforcer=Mock(),
        fallback_manager=FallbackManager([]),
    )
    p.attach_product_console(store)
    p._model_known = lambda model: True
    return p


async def _run(proxy: GatewayProxy) -> SimpleNamespace:
    return await proxy.handle_chat_completion(
        {"model": "smart", "messages": [{"role": "user", "content": "hi"}]},
        "sk_test_abc",
        {},
    )


def _called(proxy: GatewayProxy) -> list[str]:
    return [c.args[0] for c in proxy.forward.call_args_list]


@pytest.mark.asyncio
async def test_the_remaining_candidates_are_still_reachable() -> None:
    """RED: a slow first candidate must not disqualify the rest.

    Candidate 1 burns the entire 0.1s budget, exactly as `mimo-v2.6-pro`
    burned all 115s. Candidates 2 and 3 answer instantly. The current code
    skips both and returns 502.
    """
    store = _route("@a/primary", "@b/mid", "@c/last")
    proxy = _proxy(store, budget=0.1)

    async def _forward(model, body, stream=False, timeout=None):
        if model == "@a/primary":
            await asyncio.sleep(0.2)  # burns the whole budget, like the 90s
            raise ProviderTimeoutError("slow timeout")
        return ProviderResponseOK(model)

    proxy.forward = AsyncMock(side_effect=_forward)  # type: ignore[method-assign]

    result = await _run(proxy)

    assert result.status_code == 200, (
        "a recoverable chain answered 502: "
        f"called={_called(proxy)} — candidates that never ran cannot be "
        "blamed for failing"
    )
    # `@b/mid` is the first candidate behind the slow one, so reaching it is
    # the proof: `@c/last` must NOT run because `@b/mid` answered.
    assert _called(proxy) == ["@a/primary", "@b/mid"], (
        f"the candidate behind the slow one must be reached; got {_called(proxy)}"
    )
    assert result.model == "@b/mid"


@pytest.mark.asyncio
async def test_a_single_candidate_still_gets_a_bounded_attempt() -> None:
    """The budget's real job survives: no candidate runs unbounded."""
    store = _route("@a/slow", "@b/fast")
    proxy = _proxy(store, budget=0.1)

    seen_timeouts: list[float | None] = []

    async def _forward(model, body, stream=False, timeout=None):
        seen_timeouts.append(timeout)
        if model == "@a/slow":
            await asyncio.sleep(0.2)
            raise ProviderTimeoutError("slow timeout")
        return ProviderResponseOK(model)

    proxy.forward = AsyncMock(side_effect=_forward)  # type: ignore[method-assign]

    result = await _run(proxy)

    assert result.status_code == 200
    assert result.model == "@b/fast", "the fast candidate must serve"
    assert seen_timeouts[0] is not None, (
        "the first candidate was given no timeout cap at all"
    )
    assert seen_timeouts[0] <= 0.5, (
        f"the per-candidate cap must stay bounded; got {seen_timeouts[0]}"
    )


@pytest.mark.asyncio
async def test_the_tail_candidate_still_gets_a_real_reserve() -> None:
    """`@c/last` must not be handed a token timeout.

    The 2026-09-19 incident: budget 115s, 175.6s elapsed, four candidates
    skipped, 0.01s on the tail, and a 502 after the client waited three
    minutes. `_LAST_CANDIDATE_RESERVE` exists for exactly this.
    """
    store = _route("@a/primary", "@b/mid", "@c/last")
    proxy = _proxy(store, budget=0.1)

    seen: list[float | None] = []

    async def _forward(model, body, stream=False, timeout=None):
        seen.append(timeout)
        if model != "@c/last":
            await asyncio.sleep(0.2)
            raise ProviderTimeoutError("slow timeout")
        return ProviderResponseOK(model)

    proxy.forward = AsyncMock(side_effect=_forward)  # type: ignore[method-assign]

    result = await _run(proxy)

    assert result.status_code == 200
    tail_timeout = seen[-1]
    assert tail_timeout is not None and tail_timeout > 0.01, (
        f"the last candidate got a token timeout: {tail_timeout}"
    )


@pytest.mark.asyncio
async def test_a_timeout_retry_cannot_burn_a_second_full_window() -> None:
    """Per-candidate bounding still applies to retries.

    The regression from `test_gp_chain_review_fixes`: 80 + backoff + 80
    against a 90s budget. The retry must be capped by what is left for
    THIS candidate, not by a fresh full target timeout.
    """
    store = _route("@a/primary", "@b/last")
    proxy = _proxy(store, budget=0.4)

    timeouts: list[float | None] = []

    async def _forward(model, body, stream=False, timeout=None):
        timeouts.append(timeout)
        if model == "@a/primary":
            await asyncio.sleep(0.05)
            raise ProviderTimeoutError("slow timeout")
        return ProviderResponseOK(model)

    proxy.forward = AsyncMock(side_effect=_forward)  # type: ignore[method-assign]

    result = await _run(proxy)

    assert result.status_code == 200
    first_window = timeouts[0]
    assert first_window is not None
    for t in timeouts[1:]:
        if t is not None:
            # epsilon: the retry budget is recomputed from a fresh clock, so
            # the same value can differ by float rounding
            assert t <= first_window + 0.01, (
                "a retry of the same candidate was handed MORE time than "
                f"its first attempt: {first_window} then {t}"
            )


def ProviderResponseOK(model: str):  # noqa: N802
    from llm_budget_gateway.gateway_proxy import ProviderResponse

    return ProviderResponse(200, {}, {}, model, None, 5)
