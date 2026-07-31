"""Core Gateway homepage tests."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from llm_budget_gateway.gateway_home import install_gateway_home


@pytest.mark.asyncio
async def test_gateway_homepage_is_available_at_root():
    app = install_gateway_home(FastAPI())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway"
    ) as client:
        response = await client.get("/")
    assert response.status_code == 200
    assert "LLM Budget Gateway" in response.text
    assert "/v1/chat/completions" in response.text
    assert "Open API documentation" in response.text
    assert "Unified Console" in response.text


def test_existing_root_route_is_preserved():
    app = FastAPI()

    @app.get("/")
    async def existing_root():
        return {"existing": True}

    assert install_gateway_home(app) is app
    assert sum(getattr(route, "path", None) == "/" for route in app.routes) == 1
