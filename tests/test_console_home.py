"""Root-route compatibility tests for the Unified Gateway Console."""
from __future__ import annotations

import sqlite3

import httpx
import pytest

from llm_budget_gateway.console_api import create_console_app
from llm_budget_gateway.service_manager import ServiceManager


@pytest.mark.asyncio
async def test_root_and_console_alias_render_the_same_ui():
    app = create_console_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://console") as client:
        home = await client.get("/")
        alias = await client.get("/console")
        health = await client.get("/health")

    assert home.status_code == 200
    assert alias.status_code == 200
    assert home.text == alias.text
    assert "LLM Budget Gateway Console" in home.text
    assert health.json() == {"status": "ok"}


class _ProbeManager(ServiceManager):
    """ServiceManager whose port probe is set by the test (the probe boundary)."""

    def __init__(self, workdir, *, gateway_up: bool) -> None:
        super().__init__(workdir=workdir)
        self.gateway_up = gateway_up

    def _is_port_open(self, port: int) -> bool:
        return self.gateway_up


def _home_app(tmp_path, *, gateway_up: bool):
    def conn(name: str) -> sqlite3.Connection:
        return sqlite3.connect(tmp_path / name, check_same_thread=False)

    return create_console_app(
        manager=_ProbeManager(tmp_path, gateway_up=gateway_up),
        project_root=tmp_path,
        product_connection=conn("product.db"),
        provider_connection=conn("providers.db"),
        routing_connection=conn("routing.db"),
        cost_connection=conn("cost.db"),
        credential_key_path=tmp_path / "provider-master.key",
        alerts_db_path=tmp_path / "alerts.db",
    )


async def _home(app) -> dict:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://console") as client:
        response = await client.get("/v1/product/home")
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
async def test_home_reports_unreachable_gateway_when_proxy_is_down(tmp_path):
    body = await _home(_home_app(tmp_path, gateway_up=False))

    assert body["gateway"]["status"] == "unreachable"


@pytest.mark.asyncio
async def test_home_attention_names_the_unreachable_gateway(tmp_path):
    body = await _home(_home_app(tmp_path, gateway_up=False))

    titles = [item["title"] for item in body["attention"]]
    assert any("Gateway" in title for title in titles)
    entry = next(item for item in body["attention"] if "Gateway" in item["title"])
    assert entry["severity"] == "critical"
    assert entry["action"]


@pytest.mark.asyncio
async def test_home_reports_healthy_gateway_when_proxy_answers(tmp_path):
    body = await _home(_home_app(tmp_path, gateway_up=True))

    assert body["gateway"]["status"] == "healthy"
    assert not any("Gateway" in item["title"] for item in body["attention"])
