"""Atomic pre-dispatch budget reservation (hard dollar limits under concurrency).

``check_hard`` reads committed spend and returns; cost is recorded only after
the upstream response. Parallel requests on one key therefore all pass the
check. ``BudgetEnforcer.reserve`` closes that window: it checks committed spend
plus in-flight holds and takes a hold in one locked step, and the proxy releases
the hold on every exit path after the cost is recorded.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from llm_budget_gateway.budget_enforcement import (
    BudgetConfig,
    BudgetEnforcer,
    BudgetExceededError,
    BudgetScope,
)
from llm_budget_gateway.config import Settings
from llm_budget_gateway.gateway_proxy import (
    GatewayProxy,
    ProviderResponse,
    ProviderTimeoutError,
)
from llm_budget_gateway.model_fallback import FallbackManager

KEY = BudgetScope(kind="key", key="key1")
GLOBAL = BudgetScope(kind="global", key="default")


class ClockedTracker:
    """Async spend source. ``sleep(0)`` makes concurrent calls interleave."""

    def __init__(self, spend: dict[str, float] | None = None) -> None:
        self.spend = dict(spend or {})

    async def spend_since(
        self, scope_key: str, since_epoch: int, tool_name: str | None = None
    ) -> float:
        await asyncio.sleep(0)
        return self.spend.get(scope_key, 0.0)


def _enforcer(
    limits: dict[BudgetScope, float],
    spend: dict[str, float] | None = None,
    now: list[int] | None = None,
    ttl: int = 900,
) -> BudgetEnforcer:
    configs = [
        BudgetConfig(scope=scope, hard_limit=limit, window="30d")
        for scope, limit in limits.items()
    ]
    clock = now if now is not None else [1_000]
    return BudgetEnforcer(
        configs=configs,
        cost_tracker=ClockedTracker(spend),
        now_fn=lambda: clock[0],
        hold_ttl_seconds=ttl,
    )


class TestReserveHeadroom:
    @pytest.mark.asyncio
    async def test_hold_counts_against_the_limit(self) -> None:
        enforcer = _enforcer({KEY: 10.0})
        await enforcer.reserve([KEY], 6.0)
        with pytest.raises(BudgetExceededError) as exc:
            await enforcer.reserve([KEY], 6.0)
        assert exc.value.spend == pytest.approx(6.0)

    @pytest.mark.asyncio
    async def test_release_frees_the_headroom(self) -> None:
        enforcer = _enforcer({KEY: 10.0})
        reservation = await enforcer.reserve([KEY], 6.0)
        enforcer.release(reservation)
        await enforcer.reserve([KEY], 6.0)

    @pytest.mark.asyncio
    async def test_release_is_idempotent(self) -> None:
        enforcer = _enforcer({KEY: 10.0})
        first = await enforcer.reserve([KEY], 6.0)
        enforcer.release(first)
        second = await enforcer.reserve([KEY], 6.0)
        enforcer.release(first)  # must not free the second hold
        assert enforcer.held_amount(KEY.scope_key()) == pytest.approx(6.0)
        with pytest.raises(BudgetExceededError):
            await enforcer.reserve([KEY], 6.0)
        enforcer.release(second)
        assert enforcer.held_amount(KEY.scope_key()) == 0.0

    @pytest.mark.asyncio
    async def test_committed_spend_counts_against_the_limit(self) -> None:
        enforcer = _enforcer({KEY: 10.0}, spend={KEY.scope_key(): 7.0})
        with pytest.raises(BudgetExceededError):
            await enforcer.reserve([KEY], 4.0)

    @pytest.mark.asyncio
    async def test_multi_scope_reserve_is_all_or_nothing(self) -> None:
        enforcer = _enforcer({KEY: 10.0, GLOBAL: 5.0}, spend={GLOBAL.scope_key(): 5.0})
        with pytest.raises(BudgetExceededError):
            await enforcer.reserve([KEY, GLOBAL], 1.0)
        # The key scope must not keep a hold from the refused attempt.
        assert enforcer.held_amount(KEY.scope_key()) == 0.0
        await enforcer.reserve([KEY], 10.0)

    @pytest.mark.asyncio
    async def test_expired_hold_stops_counting(self) -> None:
        clock = [1_000]
        enforcer = _enforcer({KEY: 10.0}, now=clock, ttl=60)
        await enforcer.reserve([KEY], 10.0)
        clock[0] += 61  # a crashed request must not hold budget forever
        await enforcer.reserve([KEY], 10.0)

    @pytest.mark.asyncio
    async def test_scope_without_hard_limit_is_not_held(self) -> None:
        soft_only = BudgetEnforcer(
            configs=[BudgetConfig(scope=KEY, soft_limit=1.0, window="30d")],
            cost_tracker=ClockedTracker(),
        )
        await soft_only.reserve([KEY], 1_000.0)
        assert soft_only.held_amount(KEY.scope_key()) == 0.0

    @pytest.mark.asyncio
    async def test_no_cost_tracker_reserves_nothing(self) -> None:
        enforcer = BudgetEnforcer(configs=[], cost_tracker=None)  # type: ignore[arg-type]
        reservation = await enforcer.reserve([KEY], 1.0)
        assert reservation.holds == []

    @pytest.mark.asyncio
    async def test_negative_amount_is_rejected(self) -> None:
        enforcer = _enforcer({KEY: 10.0})
        with pytest.raises(ValueError):
            await enforcer.reserve([KEY], -1.0)

    @pytest.mark.asyncio
    async def test_unbounded_amount_is_refused_when_a_hard_limit_applies(self) -> None:
        """Unestimable cost fails closed: an infinite hold never fits a cap."""
        enforcer = _enforcer({KEY: 10.0})
        with pytest.raises(BudgetExceededError):
            await enforcer.reserve([KEY], float("inf"))


class TestConcurrentReserve:
    @pytest.mark.asyncio
    async def test_concurrent_reserves_admit_exactly_the_headroom(self) -> None:
        """Twenty parallel requests of 10 against a cap of 50 admit exactly 5.

        With ``check_hard`` alone (read, then dispatch) all twenty pass, because
        none of them is recorded while the others are checking.
        """
        enforcer = _enforcer({KEY: 50.0})
        results = await asyncio.gather(
            *(enforcer.reserve([KEY], 10.0) for _ in range(20)),
            return_exceptions=True,
        )
        admitted = [r for r in results if not isinstance(r, BaseException)]
        refused = [r for r in results if isinstance(r, BudgetExceededError)]
        assert len(admitted) == 5
        assert len(refused) == 15
        assert enforcer.held_amount(KEY.scope_key()) == pytest.approx(50.0)


def _proxy(enforcer: BudgetEnforcer, estimate: float, forward) -> GatewayProxy:
    tracker = Mock()
    tracker.estimate_cost = Mock(return_value=(0.0, 0.0, estimate))
    tracker.record = AsyncMock(return_value=None)
    proxy = GatewayProxy(
        settings=Settings(virtual_keys={"sk_test_abc": "key1"}),
        cost_tracker=tracker,
        budget_enforcer=enforcer,
        fallback_manager=FallbackManager([]),
    )
    proxy.forward = AsyncMock(side_effect=forward)  # type: ignore[method-assign]
    return proxy


class TestProxyReservation:
    @pytest.mark.asyncio
    async def test_hold_is_active_during_upstream_call_and_gone_after(self) -> None:
        enforcer = _enforcer({KEY: 100.0})
        seen: list[float] = []

        async def forward(model: str, body: dict) -> ProviderResponse:
            seen.append(enforcer.held_amount(KEY.scope_key()))
            return ProviderResponse(200, {}, {}, model, None, 1)

        proxy = _proxy(enforcer, estimate=2.5, forward=forward)
        await proxy.handle_chat_completion(
            {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            "sk_test_abc",
            {},
        )
        assert seen == [pytest.approx(2.5)]
        assert enforcer.held_amount(KEY.scope_key()) == 0.0

    @pytest.mark.asyncio
    async def test_hold_is_released_when_upstream_fails(self) -> None:
        enforcer = _enforcer({KEY: 100.0})

        async def forward(model: str, body: dict) -> ProviderResponse:
            raise RuntimeError("upstream down")

        proxy = _proxy(enforcer, estimate=2.5, forward=forward)
        response = await proxy.handle_chat_completion(
            {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            "sk_test_abc",
            {},
        )
        assert response.status_code == 502
        assert enforcer.held_amount(KEY.scope_key()) == 0.0

    @pytest.mark.asyncio
    async def test_concurrent_requests_cannot_overshoot_the_hard_limit(self) -> None:
        """Ten parallel requests, each estimated at 1.0, against a cap of 3.5.

        Exactly three reach the upstream; the other seven get 412. Without the
        reservation every request passes ``check_hard`` (committed spend is 0 for
        all of them) and all ten are dispatched.
        """
        enforcer = _enforcer({KEY: 3.5})
        dispatched: list[str] = []

        async def forward(model: str, body: dict) -> ProviderResponse:
            dispatched.append(model)
            await asyncio.sleep(0.01)
            return ProviderResponse(200, {}, {}, model, None, 1)

        proxy = _proxy(enforcer, estimate=1.0, forward=forward)
        body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
        responses = await asyncio.gather(
            *(proxy.handle_chat_completion(dict(body), "sk_test_abc", {}) for _ in range(10))
        )
        statuses = sorted(r.status_code for r in responses)
        assert len(dispatched) == 3
        assert statuses.count(412) == 7
        assert enforcer.held_amount(KEY.scope_key()) == 0.0


class TestHoldReleasedOnEveryExit:
    @pytest.mark.asyncio
    async def test_hold_is_released_when_upstream_times_out(self) -> None:
        enforcer = _enforcer({KEY: 100.0})

        async def forward(model: str, body: dict) -> ProviderResponse:
            raise ProviderTimeoutError("upstream too slow")

        proxy = _proxy(enforcer, estimate=2.5, forward=forward)
        response = await proxy.handle_chat_completion(
            {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            "sk_test_abc",
            {},
        )
        assert response.status_code == 502
        assert enforcer.held_amount(KEY.scope_key()) == 0.0

    @pytest.mark.asyncio
    async def test_hold_is_released_when_the_request_is_cancelled(self) -> None:
        enforcer = _enforcer({KEY: 100.0})

        async def forward(model: str, body: dict) -> ProviderResponse:
            await asyncio.sleep(3600)
            raise AssertionError("unreachable")

        proxy = _proxy(enforcer, estimate=2.5, forward=forward)
        task = asyncio.ensure_future(
            proxy.handle_chat_completion(
                {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
                "sk_test_abc",
                {},
            )
        )
        for _ in range(50):
            await asyncio.sleep(0)
            if enforcer.held_amount(KEY.scope_key()) > 0:
                break
        assert enforcer.held_amount(KEY.scope_key()) == pytest.approx(2.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert enforcer.held_amount(KEY.scope_key()) == 0.0


class TestCapBoundaryMatchesCheckHard:
    @pytest.mark.asyncio
    async def test_spend_exactly_at_the_limit_refuses_a_zero_hold(self) -> None:
        """check_hard refuses at spend >= limit; reserve must agree, even for a
        request whose estimated cost is zero (unpriced model)."""
        enforcer = _enforcer({KEY: 10.0}, spend={KEY.scope_key(): 10.0})
        with pytest.raises(BudgetExceededError):
            await enforcer.reserve([KEY], 0.0)


def _managed_proxy(enforcer: BudgetEnforcer, estimate: float, chain, finalize) -> GatewayProxy:
    tracker = Mock()
    tracker.estimate_cost = Mock(return_value=(0.0, 0.0, estimate))
    tracker.record = AsyncMock(return_value=None)
    proxy = GatewayProxy(
        settings=Settings(virtual_keys={"sk_test_abc": "key1"}),
        cost_tracker=tracker,
        budget_enforcer=enforcer,
        fallback_manager=FallbackManager([]),
    )
    proxy._resolve_route_plan = Mock(  # type: ignore[method-assign]
        return_value={
            "alias": "hermes-default",
            "metadata": {},
            "capabilities": set(),
            "route": {"name": "hermes-default"},
            "from_plane": False,
            "candidates": ["gpt-4o", "gpt-4o-mini"],
            "fallback": "none",
            "fallback_statuses": [],
            "target_cooldowns": {},
            "route_name": "hermes-default",
            "client_id": None,
            "client_profile": None,
            "conversation_id": None,
        }
    )
    proxy._prepare_route_request = AsyncMock(  # type: ignore[method-assign]
        return_value=(
            {},
            {
                "hit": None,
                "candidates": ["gpt-4o", "gpt-4o-mini"],
                "fallback": "none",
                "want_cache": False,
            },
            {"session_id": None, "model": None},
        )
    )
    proxy._run_candidate_chain = AsyncMock(side_effect=chain)  # type: ignore[method-assign]
    proxy._finalize_route_response = AsyncMock(side_effect=finalize)  # type: ignore[method-assign]
    return proxy


async def _chain_ok(**kwargs):
    await asyncio.sleep(0.01)
    return (ProviderResponse(200, {}, {}, "gpt-4o", None, 1), "gpt-4o", "none", 0.0)


async def _finalize_passthrough(**kwargs):
    return kwargs["response"]


class TestManagedRouteReservation:
    @pytest.mark.asyncio
    async def test_concurrent_managed_requests_respect_the_hard_limit(self) -> None:
        """Managed application keys are capped too: 10 parallel requests at an
        estimated 1.0 each against a cap of 3.5 admit exactly three."""
        enforcer = _enforcer({BudgetScope(kind="key", key="gw_app1"): 3.5})
        proxy = _managed_proxy(enforcer, 1.0, _chain_ok, _finalize_passthrough)
        body = {"model": "hermes-default", "messages": [{"role": "user", "content": "hi"}]}
        responses = await asyncio.gather(
            *(proxy._handle_logical_route(dict(body), "gw_app1", {}, "req") for _ in range(10))
        )
        statuses = [r.status_code for r in responses]
        assert statuses.count(200) == 3
        assert statuses.count(412) == 7
        assert proxy._run_candidate_chain.await_count == 3
        assert enforcer.held_amount("key:gw_app1") == 0.0

    @pytest.mark.asyncio
    async def test_managed_hold_released_when_the_chain_fails(self) -> None:
        enforcer = _enforcer({BudgetScope(kind="key", key="gw_app1"): 100.0})

        async def boom(**kwargs):
            raise ProviderTimeoutError("slow")

        proxy = _managed_proxy(enforcer, 2.0, boom, _finalize_passthrough)
        with pytest.raises(ProviderTimeoutError):
            await proxy._handle_logical_route(
                {"model": "hermes-default", "messages": []}, "gw_app1", {}, "req"
            )
        assert enforcer.held_amount("key:gw_app1") == 0.0

    @pytest.mark.asyncio
    async def test_stream_hold_lasts_until_the_stream_is_consumed(self) -> None:
        """A streamed answer's cost is only recorded when the stream ends, so the
        hold must stay until then, not drop when the handler returns."""
        enforcer = _enforcer({BudgetScope(kind="key", key="gw_app1"): 100.0})
        held_during: list[float] = []

        async def chunks():
            held_during.append(enforcer.held_amount("key:gw_app1"))
            yield "data: one\n\n"
            held_during.append(enforcer.held_amount("key:gw_app1"))
            yield "data: [DONE]\n\n"

        async def stream_finalize(**kwargs):
            return ProviderResponse(200, chunks(), {}, "gpt-4o", None, 1)

        proxy = _managed_proxy(enforcer, 2.0, _chain_ok, stream_finalize)
        response = await proxy._handle_logical_route(
            {"model": "hermes-default", "messages": [], "stream": True}, "gw_app1", {}, "req"
        )
        assert enforcer.held_amount("key:gw_app1") == pytest.approx(2.0)
        async for _ in response.body:
            pass
        assert held_during == [pytest.approx(2.0), pytest.approx(2.0)]
        assert enforcer.held_amount("key:gw_app1") == 0.0

    @pytest.mark.asyncio
    async def test_abandoned_stream_releases_the_hold_on_close(self) -> None:
        enforcer = _enforcer({BudgetScope(kind="key", key="gw_app1"): 100.0})

        async def chunks():
            yield "data: one\n\n"
            yield "data: two\n\n"

        async def stream_finalize(**kwargs):
            return ProviderResponse(200, chunks(), {}, "gpt-4o", None, 1)

        proxy = _managed_proxy(enforcer, 2.0, _chain_ok, stream_finalize)
        response = await proxy._handle_logical_route(
            {"model": "hermes-default", "messages": [], "stream": True}, "gw_app1", {}, "req"
        )
        await response.body.__anext__()  # client reads one chunk, then disconnects
        await response.body.aclose()
        assert enforcer.held_amount("key:gw_app1") == 0.0
