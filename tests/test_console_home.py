"""Root-route compatibility tests for the Unified Gateway Console."""
from __future__ import annotations

import httpx
import pytest

from llm_budget_gateway.console_api import create_console_app


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
