"""TDD acceptance tests for the application and logical-route control plane."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from llm_budget_gateway.console_api import create_console_app
from llm_budget_gateway.routing_control_plane import RoutingControlPlane


def _route() -> dict[str, object]:
    return {
        "name": "support-balanced",
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
        "max_cost_per_request": 0.08,
        "required_region": "eu",
        "required_capabilities": [],
    }


def test_application_and_route_lifecycle_real_sqlite() -> None:
    plane = RoutingControlPlane(sqlite3.connect(":memory:"))
    app = plane.create_application("Support App", "support-balanced")
    assert app["api_key"].startswith("gw_")
    assert plane.list_applications()[0]["name"] == "Support App"
    route = plane.create_route(_route())
    assert route["status"] == "draft"
    published = plane.publish_route(route["id"])
    assert published["published_version"] == 1
    draft = plane.update_route(
        route["id"], {**_route(), "default_model": "gpt-mini-v2"}
    )
    assert draft["draft_version"] == 2
    plane.publish_route(route["id"])
    rolled = plane.rollback_route(route["id"])
    assert rolled["published_version"] == 1


def test_route_simulation_explains_schedule_budget_quality_and_health() -> None:
    plane = RoutingControlPlane(sqlite3.connect(":memory:"))
    route = plane.create_route(_route())
    plane.publish_route(route["id"])
    decision = plane.simulate(
        route["id"],
        now=datetime(2026, 8, 4, 21, 30, tzinfo=ZoneInfo("Europe/Zurich")),
        quality_tier="balanced",
        estimated_cost=0.03,
        spend_by_model={"gpt-mini": 100.0},
        health={"gpt-mini": True, "gemini-flash": True, "claude-haiku": True},
        region="eu",
        capabilities=[],
    )
    assert decision["selected_model"] == "gemini-flash"
    assert decision["fallback_reason"] == "budget"
    assert any("Outside" in step["detail"] for step in decision["decision_path"])
    assert any(
        "budget exhausted" in step["detail"] for step in decision["decision_path"]
    )


def test_route_simulation_prefers_smart_tier_and_filters_capabilities() -> None:
    plane = RoutingControlPlane(sqlite3.connect(":memory:"))
    config = _route()
    config["required_capabilities"] = ["tools"]
    route = plane.create_route(config)
    plane.publish_route(route["id"])
    decision = plane.simulate(
        route["id"],
        now=datetime(2026, 8, 4, 10, 0, tzinfo=ZoneInfo("Europe/Zurich")),
        quality_tier="smart",
        estimated_cost=0.02,
        spend_by_model={},
        health={"claude-sonnet": True},
        region="eu",
        capabilities=["tools"],
    )
    assert decision["selected_model"] == "claude-sonnet"
    with pytest.raises(ValueError, match="capabilities"):
        plane.simulate(
            route["id"],
            now=datetime.now(ZoneInfo("Europe/Zurich")),
            quality_tier="smart",
            estimated_cost=0.02,
            spend_by_model={},
            health={},
            region="eu",
            capabilities=[],
        )


def test_route_validation_and_activity() -> None:
    plane = RoutingControlPlane(sqlite3.connect(":memory:"))
    invalid = _route()
    invalid["timezone"] = "Mars/Base"
    with pytest.raises(ValueError, match="timezone"):
        plane.create_route(invalid)
    invalid = _route()
    invalid["fallback_statuses"] = [400]
    with pytest.raises(ValueError, match="fallback"):
        plane.create_route(invalid)
    route = plane.create_route(_route())
    plane.publish_route(route["id"])
    plane.simulate(
        route["id"],
        now=datetime.now(ZoneInfo("Europe/Zurich")),
        quality_tier="fast",
        estimated_cost=0.01,
        spend_by_model={},
        health={"gemini-flash": True},
        region="eu",
        capabilities=[],
    )
    assert plane.route_activity(route["id"])[0]["selected_model"] == "gemini-flash"


@pytest.mark.asyncio
async def test_admin_api_full_user_flow() -> None:
    app = create_console_app(
        routing_connection=sqlite3.connect(":memory:", check_same_thread=False)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://console"
    ) as client:
        application = await client.post(
            "/v1/admin/applications",
            json={"name": "Support App", "default_route": "support-balanced"},
        )
        assert application.status_code == 201
        route = await client.post("/v1/admin/routes", json=_route())
        assert route.status_code == 201
        route_id = route.json()["id"]
        publish = await client.post(f"/v1/admin/routes/{route_id}/publish")
        assert publish.status_code == 200
        simulation = await client.post(
            f"/v1/admin/routes/{route_id}/simulate",
            json={
                "at": "2026-08-04T21:30:00+02:00",
                "quality_tier": "balanced",
                "estimated_cost": 0.03,
                "spend_by_model": {"gpt-mini": 100},
                "health": {"gpt-mini": True, "gemini-flash": True},
                "region": "eu",
                "capabilities": [],
            },
        )
        assert simulation.status_code == 200
        assert simulation.json()["selected_model"] == "gemini-flash"
        assert (await client.get("/v1/admin/routes")).json()["routes"][0][
            "name"
        ] == "support-balanced"
        assert (
            await client.get(f"/v1/admin/routes/{route_id}/activity")
        ).status_code == 200


def test_duplicate_careful_validation_and_no_eligible_route() -> None:
    plane = RoutingControlPlane(sqlite3.connect(":memory:"))
    config = _route()
    route = plane.create_route(config)
    with pytest.raises(ValueError, match="already exists"):
        plane.create_route(config)
    with pytest.raises(ValueError, match="previous"):
        plane.rollback_route(route["id"])
    with pytest.raises(KeyError):
        plane.get_route("missing")
    with pytest.raises(ValueError, match="quality"):
        plane.simulate(
            route["id"],
            now=datetime.now(ZoneInfo("Europe/Zurich")),
            quality_tier="unknown",
            estimated_cost=0.01,
            spend_by_model={},
            health={},
            region="eu",
            capabilities=[],
        )
    with pytest.raises(ValueError, match="cost"):
        plane.simulate(
            route["id"],
            now=datetime.now(ZoneInfo("Europe/Zurich")),
            quality_tier="fast",
            estimated_cost=1,
            spend_by_model={},
            health={},
            region="eu",
            capabilities=[],
        )
    with pytest.raises(ValueError, match="region"):
        plane.simulate(
            route["id"],
            now=datetime.now(ZoneInfo("Europe/Zurich")),
            quality_tier="fast",
            estimated_cost=0.01,
            spend_by_model={},
            health={},
            region="us",
            capabilities=[],
        )
    with pytest.raises(RuntimeError, match="eligible"):
        plane.simulate(
            route["id"],
            now=datetime.now(ZoneInfo("Europe/Zurich")),
            quality_tier="fast",
            estimated_cost=0.01,
            spend_by_model={"gemini-flash": 100, "claude-haiku": 100},
            health={"gemini-flash": False},
            region="eu",
            capabilities=[],
        )


def test_missing_fields_schedule_quality_and_application_validation() -> None:
    plane = RoutingControlPlane(sqlite3.connect(":memory:"))
    with pytest.raises(ValueError, match="required"):
        plane.create_application("", "")
    with pytest.raises(ValueError, match="missing"):
        plane.create_route({})
    bad = _route()
    bad["monthly_budget"] = 0
    with pytest.raises(ValueError, match="positive"):
        plane.create_route(bad)
    bad = _route()
    bad["schedule"] = {"start": "08:00"}
    with pytest.raises(ValueError, match="schedule"):
        plane.create_route(bad)
    bad = _route()
    bad["quality_models"] = {"magic": "model"}
    with pytest.raises(ValueError, match="quality"):
        plane.create_route(bad)


@pytest.mark.asyncio
async def test_admin_api_error_contracts() -> None:
    app = create_console_app(
        routing_connection=sqlite3.connect(":memory:", check_same_thread=False)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://console"
    ) as client:
        assert (await client.post("/v1/admin/applications", json={})).status_code == 422
        assert (await client.post("/v1/admin/routes", json={})).status_code == 422
        assert (await client.get("/v1/admin/routes/missing")).status_code == 404
        assert (
            await client.put("/v1/admin/routes/missing", json=_route())
        ).status_code == 404
        assert (
            await client.post("/v1/admin/routes/missing/publish")
        ).status_code == 404
        assert (
            await client.post("/v1/admin/routes/missing/rollback")
        ).status_code == 404
        assert (
            await client.get("/v1/admin/routes/missing/activity")
        ).status_code == 404
        bad = await client.post("/v1/admin/routes/missing/simulate", json={"at": "bad"})
        assert bad.status_code in {404, 422}
