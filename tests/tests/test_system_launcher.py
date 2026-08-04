"""Acceptance tests for one-command cockpit-first system startup."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from llm_budget_gateway.system_launcher import create_system_app, startup_plan


class FakeManager:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0

    def start_all(self) -> list[dict[str, object]]:
        self.started += 1
        return [{"slug": "gateway", "running": True, "reachable": True}]

    def stop_all(self) -> list[dict[str, object]]:
        self.stopped += 1
        return []

    def statuses(self) -> list[dict[str, object]]:
        return [{"slug": "gateway", "running": True, "reachable": True}]


def test_startup_plan_uses_cockpit_and_all_required_services() -> None:
    plan = startup_plan(Path("/project"))
    assert plan["landing_url"] == "http://127.0.0.1:8013/cockpit"
    assert plan["console_url"] == "http://127.0.0.1:8013/console"
    assert plan["command"][-1] == "8013"
    assert plan["auto_start_services"] is True


@pytest.mark.asyncio
async def test_root_redirects_to_cockpit_and_status_is_available() -> None:
    manager = FakeManager()
    app = create_system_app(manager=manager, open_browser=False)
    async with app.router.lifespan_context(app):
        assert manager.started == 1
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://system"
        ) as client:
            root = await client.get("/", follow_redirects=False)
            status = await client.get("/v1/system/status")
        assert root.status_code == 307
        assert root.headers["location"] == "/cockpit"
        assert status.json()["ready"] is True
    assert manager.stopped == 1


@pytest.mark.asyncio
async def test_partial_service_failure_does_not_hide_cockpit() -> None:
    manager = FakeManager()
    manager.start_all = Mock(
        return_value=[
            {
                "slug": "gateway",
                "running": False,
                "reachable": False,
                "error": "port busy",
            },
            {"slug": "control", "running": True, "reachable": True},
        ]
    )
    app = create_system_app(manager=manager, open_browser=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://system"
        ) as client:
            status = await client.get("/v1/system/status")
        body = status.json()
        assert body["ready"] is False
        assert body["failures"][0]["slug"] == "gateway"
        assert body["cockpit_available"] is True
