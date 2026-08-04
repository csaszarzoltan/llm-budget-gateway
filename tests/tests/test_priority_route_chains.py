"""TDD tests for prioritized, schedule-aware multi-model route chains."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from llm_budget_gateway.console_api import create_console_app
from llm_budget_gateway.priority_routes import PriorityRouteStore


def target(
    model: str,
    priority: int,
    *,
    timezone: str = "Europe/Zurich",
    start: str = "00:00",
    end: str = "23:59",
    days: list[int] | None = None,
    budget: float = 100.0,
) -> dict[str, object]:
    return {
        "model": model,
        "priority": priority,
        "timezone": timezone,
        "days": days if days is not None else [0, 1, 2, 3, 4, 5, 6],
        "start": start,
        "end": end,
        "monthly_budget": budget,
        "enabled": True,
        "fallback_statuses": [429, 500, 502, 503, 504],
        "required_capabilities": [],
    }


def test_multiple_fallbacks_are_sorted_by_priority_and_versioned() -> None:
    store = PriorityRouteStore(sqlite3.connect(":memory:"))
    route = store.create_route(
        "support", [target("third", 30), target("first", 10), target("second", 20)]
    )
    assert [x["model"] for x in route["draft"]["targets"]] == [
        "first",
        "second",
        "third",
    ]
    assert route["draft_version"] == 1
    published = store.publish(route["id"])
    assert published["published_version"] == 1
    updated = store.update_route(
        route["id"],
        "support",
        [
            target("first", 10),
            target("night", 15, start="18:00", end="08:00"),
            target("second", 20),
        ],
    )
    assert updated["draft_version"] == 2
    assert updated["published"]["targets"][1]["model"] == "second"


def test_each_fallback_has_own_timezone_and_overnight_schedule() -> None:
    store = PriorityRouteStore(sqlite3.connect(":memory:"))
    route = store.create_route(
        "global",
        [
            target(
                "zurich-day",
                10,
                timezone="Europe/Zurich",
                start="08:00",
                end="18:00",
                days=[0, 1, 2, 3, 4],
            ),
            target(
                "new-york-day",
                20,
                timezone="America/New_York",
                start="08:00",
                end="18:00",
                days=[0, 1, 2, 3, 4],
            ),
            target(
                "tokyo-night", 30, timezone="Asia/Tokyo", start="18:00", end="08:00"
            ),
        ],
    )
    store.publish(route["id"])
    decision = store.resolve(
        "global",
        at=datetime(2026, 8, 4, 19, 0, tzinfo=ZoneInfo("Europe/Zurich")),
        capabilities=[],
    )
    assert decision["selected_model"] == "new-york-day"
    assert decision["attempt_order"] == ["new-york-day", "tokyo-night"]
    excluded = {x["model"]: x["reason"] for x in decision["excluded"]}
    assert excluded["zurich-day"] == "outside_schedule"


def test_budget_health_capability_and_disabled_targets_are_explained() -> None:
    store = PriorityRouteStore(sqlite3.connect(":memory:"))
    items = [
        target("budgeted", 10, budget=1),
        target("unhealthy", 20),
        target("tools-only", 30),
        target("winner", 40),
    ]
    items[2]["required_capabilities"] = ["tools"]
    items[3]["enabled"] = True
    route = store.create_route("complex", items)
    store.publish(route["id"])
    store.record_spend(
        "complex", "budgeted", 1, at=datetime(2026, 8, 4, tzinfo=ZoneInfo("UTC"))
    )
    store.set_health("complex", "unhealthy", False)
    decision = store.resolve(
        "complex", at=datetime(2026, 8, 4, 12, tzinfo=ZoneInfo("UTC")), capabilities=[]
    )
    assert decision["selected_model"] == "winner"
    reasons = [x["reason"] for x in decision["excluded"]]
    assert reasons == ["budget_exhausted", "unhealthy", "missing_capabilities"]


def test_validation_rejects_duplicate_priorities_bad_timezones_and_no_targets() -> None:
    store = PriorityRouteStore(sqlite3.connect(":memory:"))
    with pytest.raises(ValueError, match="target"):
        store.create_route("empty", [])
    with pytest.raises(ValueError, match="priority"):
        store.create_route("dup", [target("a", 10), target("b", 10)])
    with pytest.raises(ValueError, match="timezone"):
        store.create_route("bad", [target("a", 10, timezone="Mars/Base")])
    with pytest.raises(ValueError, match="time"):
        store.create_route("badtime", [target("a", 10, start="25:00")])


def test_failover_attempts_only_configured_transient_statuses() -> None:
    store = PriorityRouteStore(sqlite3.connect(":memory:"))
    route = store.create_route(
        "support", [target("one", 10), target("two", 20), target("three", 30)]
    )
    store.publish(route["id"])
    first = store.resolve("support", at=datetime.now(ZoneInfo("UTC")), capabilities=[])
    second = store.next_after_failure(first, status_code=429)
    third = store.next_after_failure(second, status_code=503)
    assert second["selected_model"] == "two"
    assert third["selected_model"] == "three"
    with pytest.raises(ValueError, match="not configured"):
        store.next_after_failure(first, status_code=400)


@pytest.mark.asyncio
async def test_priority_route_api_real_http_flow() -> None:
    app = create_console_app(
        priority_routing_connection=sqlite3.connect(":memory:", check_same_thread=False)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://console"
    ) as client:
        created = await client.post(
            "/v1/admin/priority-routes",
            json={
                "name": "support",
                "targets": [
                    target("primary", 10),
                    target("backup", 20, timezone="America/New_York"),
                ],
            },
        )
        assert created.status_code == 201
        route_id = created.json()["id"]
        assert (
            await client.post(f"/v1/admin/priority-routes/{route_id}/publish")
        ).status_code == 200
        simulation = await client.post(
            f"/v1/admin/priority-routes/{route_id}/simulate",
            json={"at": "2026-08-04T12:00:00+02:00", "capabilities": []},
        )
        assert simulation.status_code == 200
        assert simulation.json()["selected_model"] == "primary"
        assert (
            len((await client.get("/v1/admin/priority-routes")).json()["routes"]) == 1
        )


def test_disabled_target_and_chain_exhaustion() -> None:
    store = PriorityRouteStore(sqlite3.connect(":memory:"))
    disabled = target("disabled", 10)
    disabled["enabled"] = False
    route = store.create_route("edge", [disabled, target("only", 20)])
    store.publish(route["id"])
    decision = store.resolve("edge", at=datetime.now(ZoneInfo("UTC")), capabilities=[])
    assert decision["excluded"] == [{"model": "disabled", "reason": "disabled"}]
    with pytest.raises(RuntimeError, match="exhausted"):
        store.next_after_failure(decision, status_code=429)
    with pytest.raises(ValueError, match="non-negative"):
        store.record_spend("edge", "only", -1, at=datetime.now(ZoneInfo("UTC")))


def test_get_update_publish_missing_and_duplicate_name() -> None:
    store = PriorityRouteStore(sqlite3.connect(":memory:"))
    store.create_route("existing", [target("one", 10)])
    with pytest.raises(ValueError, match="already exists"):
        store.create_route("existing", [target("two", 10)])
    with pytest.raises(KeyError):
        store.get_route("missing")
    with pytest.raises(KeyError):
        store.publish("missing")
    with pytest.raises(KeyError):
        store.update_route("missing", "x", [target("one", 10)])


@pytest.mark.asyncio
async def test_priority_route_api_validation_and_missing_contracts() -> None:
    app = create_console_app(
        priority_routing_connection=sqlite3.connect(":memory:", check_same_thread=False)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://console"
    ) as client:
        assert (
            await client.post("/v1/admin/priority-routes", json={})
        ).status_code == 422
        assert (
            await client.get("/v1/admin/priority-routes/missing")
        ).status_code == 404
        assert (
            await client.put(
                "/v1/admin/priority-routes/missing",
                json={"name": "x", "targets": [target("one", 10)]},
            )
        ).status_code == 404
        assert (
            await client.post("/v1/admin/priority-routes/missing/publish")
        ).status_code == 404
        assert (
            await client.post(
                "/v1/admin/priority-routes/missing/simulate",
                json={"at": "bad", "capabilities": []},
            )
        ).status_code == 404
