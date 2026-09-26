"""An overnight service window never opens.

`simulate` and `_within_window` both test membership with
`start <= now < end`. That is correct for a same-day window (08:00-18:00)
and silently WRONG for an overnight one (22:00-06:00): 22:00 <= 02:00 is
false, so the window is never inside, every hour of every day. The
`scheduled_model` in the premium schedule, and any `model_windows` entry
with an overnight range, are unreachable — and nothing reports it.

`_validate_config` checks that start/end/wEEKDAYS are PRESENT, never that
start < end, so the configuration is accepted and stored. A route that an
operator believes schedules a premium model overnight silently never uses
it.

The fix: a window that wraps past midnight is inside when
`now >= start or now < end`, and only on the day(s) the window opens on.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from llm_budget_gateway.routing_control_plane import RoutingControlPlane


@pytest.fixture()
def plane() -> RoutingControlPlane:
    cp = RoutingControlPlane(sqlite3.connect(":memory:"))
    yield cp
    cp.connection.close()


def _route(name: str, start: str, end: str) -> dict[str, object]:
    return {
        "name": name,
        "default_model": "gpt-mini",
        "fallback_models": ["gemini-flash"],
        "monthly_budget": 100.0,
        "timezone": "UTC",
        "schedule": {
            "weekdays": [0, 1, 2, 3, 4, 5, 6],
            "start": start,
            "end": end,
            "scheduled_model": "gpt-premium",
        },
        "quality_models": {
            # 'fast' deliberately unmapped so the schedule can decide the
            # primary (simulate prefers quality_models[tier] over
            # scheduled_model). A mapped tier would hide the schedule.
            "balanced": "gpt-mini",
            "smart": "claude-sonnet",
            "reasoning": "o3",
        },
        "fallback_statuses": [429, 500, 502, 503, 504],
        "max_cost_per_request": 0.08,
        "required_region": "eu",
        "required_capabilities": [],
    }


def _decide(plane: RoutingControlPlane, rid: str, when: datetime) -> str:
    """Select with a tier that is NOT mapped, so the schedule decides.

    `simulate` prefers `quality_models[tier]` over `scheduled_model` — the
    schedule only supplies the primary when the tier has no mapped model.
    A mapped tier therefore hides the schedule entirely, which is the
    intended design, not a bug; the schedule must be exercised through a
    tier with no entry in quality_models.
    """
    out = plane.simulate(
        rid,
        now=when,
        quality_tier="fast",
        estimated_cost=0.01,
        spend_by_model={},
        health={},
        region="eu",
        capabilities=[],
    )
    return out["selected_model"]


def test_an_overnight_schedule_actually_opens(plane: RoutingControlPlane) -> None:
    route = plane.create_route(_route("overnight", "22:00", "06:00"))
    rid = route["id"]
    plane.publish_route(rid)

    # 02:00 on a Wednesday — inside a 22:00-06:00 window
    inside = _decide(plane, rid, datetime(2026, 9, 23, 2, 0, tzinfo=ZoneInfo("UTC")))
    assert inside == "gpt-premium", (
        "a 22:00-06:00 overnight window never opens: 02:00 is inside it, but "
        "the start<=now<end test can never be true across midnight"
    )


def test_an_overnight_schedule_closes_in_the_morning(
    plane: RoutingControlPlane,
) -> None:
    route = plane.create_route(_route("overnight2", "22:00", "06:00"))
    rid = route["id"]
    plane.publish_route(rid)

    # 12:00 — outside
    outside = _decide(plane, rid, datetime(2026, 9, 23, 12, 0, tzinfo=ZoneInfo("UTC")))
    assert outside != "gpt-premium", "12:00 must not be inside 22:00-06:00"


def test_a_same_day_schedule_still_works(plane: RoutingControlPlane) -> None:
    """The common case must not regress."""
    route = plane.create_route(_route("daytime", "08:00", "18:00"))
    rid = route["id"]
    plane.publish_route(rid)

    assert _decide(plane, rid, datetime(2026, 9, 23, 10, 0, tzinfo=ZoneInfo("UTC"))) == (
        "gpt-premium"
    )
    assert _decide(plane, rid, datetime(2026, 9, 23, 20, 0, tzinfo=ZoneInfo("UTC"))) != (
        "gpt-premium"
    )


def test_the_boundary_hours_behave(plane: RoutingControlPlane) -> None:
    route = plane.create_route(_route("overnight3", "22:00", "06:00"))
    rid = route["id"]
    plane.publish_route(rid)

    at_start = _decide(plane, rid, datetime(2026, 9, 23, 22, 0, tzinfo=ZoneInfo("UTC")))
    assert at_start == "gpt-premium", "the start hour is inclusive"
    just_before_end = _decide(
        plane, rid, datetime(2026, 9, 23, 5, 59, tzinfo=ZoneInfo("UTC"))
    )
    assert just_before_end == "gpt-premium", "the end hour is exclusive"
