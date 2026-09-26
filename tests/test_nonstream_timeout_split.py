"""The non-streaming provider call kills any answer that takes longer than
the target's `timeout_seconds`.

The reported symptom: an agent on this gateway "stops and waits", and stops
for good when a long terminal command has to be waited on. A direct call to
the same model waits fine, for half an hour. The gateway is the only
difference, so the difference is in what the gateway imposes.

It is here. Every non-streaming call is a single

    response = await self._client.post(url, json=payload, headers=headers)

with no `timeout=` argument, so it inherits the client's. The client is built
in main.py as `DirectProviderClient(registry, timeout=settings.provider_timeout)`
— one scalar, 300s in production — and `_target_timeout` then clamps it with
`min(to, provider_timeout)` where `to` is the route target's
`timeout_seconds`. Every hermes-default target declares 90.

httpx applies a scalar as a WHOLE-REQUEST budget: not "the first byte must
arrive within 90s" but "the entire response must be COMPLETE within 90s". A
reasoning model that thinks for 100s and then answers perfectly is killed
mid-flight; the exception becomes a `timeout` provider error; the fallback
manager parks that model with a 90s cooldown; every later request then skips
it. Measured live in `model_cooldowns`:
`@opencode-go/muse-spark-1.3-contributor` parked with
`{"type": "timeout", "seconds": 90}`, while the DB shows dozens of 200s
between 60s and 87s — answers that did finish, with almost no headroom.

The streaming leg was already fixed for exactly this: the idle deadline is
`max(target_timeout, stream_idle_timeout)`, so a target's 90s cannot kill a
stream that is actively delivering tokens. The non-streaming leg never got
the same treatment — and an agentic client that does not stream is precisely
where the stall is most visible.

The fix is to stop letting one scalar cover the whole request, and to bound
the phases separately, the way the streaming leg already does.
"""
from __future__ import annotations

import inspect

import httpx

from llm_budget_gateway.provider_direct import DirectProviderClient


def _non_streaming_posts() -> list[str]:
    """Every `self._client.post(...)` in the direct client, minus the
    streaming one (which is a `stream()` call, not a post)."""
    src = inspect.getsource(DirectProviderClient)
    return src.split("self._client.post(")[1:]


def test_the_non_streaming_timeout_is_not_a_whole_request_budget() -> None:
    """A single scalar httpx timeout caps the WHOLE request.

    That was the defect: every answer that thought longer than
    `timeout_seconds` became a timeout error, and the model got parked. A
    structured `httpx.Timeout` expresses "bound the handshake, and bound a
    stalled read separately".
    """
    posts = _non_streaming_posts()
    assert posts, "no non-streaming provider call found to check"
    for i, call in enumerate(posts):
        # The call spans to its closing paren, so the timeout kwarg is inside
        # it — a source-level guard that no post falls back to the client's
        # scalar budget again.
        call_src = call.split("\n\n    except", 1)[0]
        assert "timeout=split_timeout(" in call_src, (
            f"non-streaming post #{i + 1} does not pass a structured "
            f"timeout, so it inherits the client's scalar budget:\n{call_src}"
        )


def test_split_timeout_floors_the_read_at_the_idle_budget() -> None:
    """The regression: a 90s target must NOT become a 90s read budget."""
    from llm_budget_gateway.provider_direct import split_timeout

    t = split_timeout(90.0, 300.0)
    assert t.connect == 90.0, "the handshake stays bounded by the target"
    assert t.read == 300.0, (
        f"the read must be floored at the idle budget, got {t.read}"
    )
    assert t.read > t.connect


def test_split_timeout_without_a_target_uses_the_idle_budget() -> None:
    from llm_budget_gateway.provider_direct import split_timeout

    for none_target in (None, 0, 0.0):
        t = split_timeout(none_target, 300.0)
        assert t.read == 300.0, t
        assert t.connect == 300.0, t


def test_split_timeout_survives_a_garbage_target_value() -> None:
    """A UI-entered non-numeric timeout must not raise inside the request."""
    from llm_budget_gateway.provider_direct import split_timeout

    t = split_timeout("ninety", 300.0)  # type: ignore[arg-type]
    assert t.read == 300.0, t


def test_a_target_above_the_idle_budget_still_wins_on_the_read() -> None:
    """A target asking for MORE than the idle budget is honoured — the idle
    budget is a floor, not a ceiling."""
    from llm_budget_gateway.provider_direct import split_timeout

    t = split_timeout(900.0, 300.0)
    assert t.read == 900.0, t
    assert t.connect == 900.0, t


def test_a_scalar_timeout_really_is_a_whole_request_budget() -> None:
    """Document the trap so it cannot come back silently.

    One value sets EVERY phase to the same number — including the read
    phase, which for a non-streaming call covers the whole upstream think
    plus the whole answer.
    """
    scalar = httpx.Timeout(90.0)
    assert scalar.read == 90.0
    assert scalar.connect == 90.0
    assert scalar.read == scalar.connect == scalar.write == scalar.pool


def test_a_structured_timeout_separates_the_phases() -> None:
    """`httpx.Timeout(connect=..., read=...)` is the only form that can say
    "bound the handshake, and separately bound how long the body may stall"."""
    t = httpx.Timeout(connect=90.0, read=300.0, write=90.0, pool=90.0)
    assert t.connect == 90.0
    assert t.read == 300.0
    assert t.read > t.connect


def test_the_target_timeout_cannot_shrink_the_body_read() -> None:
    """The regression that matters: a 90s target must not cap the read.

    `_target_timeout` returns `min(to, provider_timeout)`, so a target
    declaring 90 produces 90. If that value is handed to the post as a
    scalar, a 100s think is killed. It may bound the connect, never the
    read — the read has to be at least the streaming idle budget, which is
    what the streaming leg already guarantees.
    """
    from llm_budget_gateway.config import Settings

    idle = float(Settings().stream_idle_timeout or 0)
    assert idle >= 300.0, (
        f"the idle budget dropped to {idle}s; the non-streaming read must "
        "have room to match it"
    )

    # A target asking for 90 must not become a 90s read budget.
    target_seconds = 90.0
    read_budget = max(float(target_seconds), idle)
    assert read_budget == idle, (
        "the read budget must be floored at the idle timeout, not clamped "
        "down to the target's timeout_seconds"
    )
