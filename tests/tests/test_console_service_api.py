"""API contracts for starting and stopping services from the console UI."""

from __future__ import annotations

import httpx
import pytest

from llm_budget_gateway.console_api import create_console_app


class FakeManager:
    def __init__(self) -> None:
        self.running: set[str] = set()

    def _state(self, slug: str) -> dict[str, object]:
        if slug not in {"gateway", "control"}:
            raise ValueError(f"unknown service: {slug}")
        running = slug in self.running
        port = 8000 if slug == "gateway" else 8001
        return {
            "slug": slug,
            "name": slug.title(),
            "port": port,
            "running": running,
            "reachable": running,
            "managed": running,
            "pid": 42 if running else None,
            "url": f"http://127.0.0.1:{port}/docs",
        }

    def statuses(self):
        return [self._state("gateway"), self._state("control")]

    def start(self, slug: str):
        self._state(slug)
        self.running.add(slug)
        return self._state(slug)

    def stop(self, slug: str):
        self._state(slug)
        self.running.discard(slug)
        return self._state(slug)

    def start_all(self):
        self.running.update({"gateway", "control"})
        return self.statuses()

    def stop_all(self):
        self.running.clear()
        return self.statuses()


@pytest.mark.asyncio
async def test_console_page_contains_service_manager_controls():
    app = create_console_app(FakeManager())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 50000)),
        base_url="http://console",
    ) as client:
        response = await client.get("/")
    assert response.status_code == 200
    assert "Manage services" in response.text
    assert "Start all" in response.text
    assert "Stop all" in response.text


@pytest.mark.asyncio
async def test_start_stop_and_batch_actions_require_local_custom_header():
    manager = FakeManager()
    app = create_console_app(manager)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    headers = {"X-Console-Action": "1"}
    async with httpx.AsyncClient(
        transport=transport, base_url="http://console"
    ) as client:
        forbidden = await client.post("/v1/console/services/gateway/start")
        started = await client.post(
            "/v1/console/services/gateway/start", headers=headers
        )
        all_started = await client.post(
            "/v1/console/services/start-all", headers=headers
        )
        stopped = await client.post("/v1/console/services/stop-all", headers=headers)
    assert forbidden.status_code == 403
    assert started.json()["running"] is True
    assert all(x["running"] for x in all_started.json()["services"])
    assert not any(x["running"] for x in stopped.json()["services"])


@pytest.mark.asyncio
async def test_unknown_service_returns_404():
    app = create_console_app(FakeManager())
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://console"
    ) as client:
        response = await client.post(
            "/v1/console/services/missing/start", headers={"X-Console-Action": "1"}
        )
    assert response.status_code == 404
