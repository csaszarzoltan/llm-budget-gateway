"""rollback_route can restore a version that was never live.

`rollback_route` sets `published_version = published - 1` and refuses only
`published <= 1`. It never checks that the target version was ever
published, so:

    create route            -> draft v1
    publish                 -> published v1
    update (draft)          -> draft v2   (never published)
    update (draft)          -> draft v3   (never published)
    publish                 -> published v3
    rollback                -> published v2  <- NEVER RAN IN PRODUCTION

`route_versions` stores only (route_id, version, config_json, created_at) —
there is no published flag and no history, so the code cannot tell a version
that served traffic from one that only ever existed as a draft. The rollback
silently promotes untested config, which is the opposite of what a rollback
is for.

The fix needs the publication history: mark a version published at
publish_route time and have rollback walk BACK to the most recent previously
published version, raising when there is none.
"""
from __future__ import annotations

import pytest

from llm_budget_gateway.routing_control_plane import RoutingControlPlane


@pytest.fixture()
def plane() -> RoutingControlPlane:
    import sqlite3

    cp = RoutingControlPlane(sqlite3.connect(":memory:"))
    yield cp
    cp.connection.close()


def _config(
    name: str = "app-prod", cost: float = 0.5
) -> dict[str, object]:
    """A valid route shape (mirrors tests/test_routing_control_plane.py::_route)
    with a distinct name per test and a tunable cost cap."""
    return {
        "name": name,
        "default_model": "gpt-mini",
        "fallback_models": ["gemini-flash", "claude-haiku"],
        "monthly_budget": 100.0,
        "timezone": "Europe/Zurich",
        "schedule": {
            "weekdays": [0, 1, 2, 3, 4],
            "start": "08:00",
            "end": "18:00",
            "scheduled_model": "gpt-premium",
        },
        "quality_models": {
            "fast": "gemini-flash",
            "balanced": "gpt-mini",
            "smart": "claude-sonnet",
            "reasoning": "o3",
        },
        "fallback_statuses": [429, 500, 502, 503, 504],
        "max_cost_per_request": cost,
        "required_region": "eu",
        "required_capabilities": [],
    }


def test_rollback_never_promotes_an_unpublished_version(
    plane: RoutingControlPlane,
) -> None:
    route = plane.create_route(_config("app-prod"))
    rid = route["id"]
    plane.publish_route(rid)  # v1 live
    assert plane.get_route(rid)["published_version"] == 1

    plane.update_route(rid, _config(cost=0.75))  # draft v2, never published
    plane.update_route(rid, _config(cost=0.9))  # draft v3, never published
    plane.publish_route(rid)  # v3 live
    assert plane.get_route(rid)["published_version"] == 3

    after = plane.rollback_route(rid)
    assert after["published_version"] == 1, (
        f"rollback promoted version {after['published_version']}, which was "
        "only ever a draft — it has never served production traffic"
    )


def test_rollback_walks_back_through_real_publication_history(
    plane: RoutingControlPlane,
) -> None:
    route = plane.create_route(_config("app-prod2"))
    rid = route["id"]
    plane.publish_route(rid)  # v1 live
    plane.update_route(rid, _config(cost=0.8))
    plane.publish_route(rid)  # v2 live
    plane.update_route(rid, _config(cost=0.6))
    plane.publish_route(rid)  # v3 live
    assert plane.get_route(rid)["published_version"] == 3

    # rollback must land on v2 — the previous version that really served
    assert plane.rollback_route(rid)["published_version"] == 2
    assert plane.rollback_route(rid)["published_version"] == 1


def test_rollback_with_no_previous_publication_raises(
    plane: RoutingControlPlane,
) -> None:
    route = plane.create_route(_config("app-prod3"))
    rid = route["id"]
    plane.publish_route(rid)  # only v1 was ever published
    with pytest.raises(ValueError):
        plane.rollback_route(rid)


def test_rollback_after_unpublished_drafts_only_raises(
    plane: RoutingControlPlane,
) -> None:
    """One publication plus drafts that were never live: nothing to go back to."""
    route = plane.create_route(_config("app-prod4"))
    rid = route["id"]
    plane.publish_route(rid)  # v1 live
    plane.update_route(rid, _config(cost=0.7))  # draft v2, never published
    with pytest.raises(ValueError):
        plane.rollback_route(rid)
