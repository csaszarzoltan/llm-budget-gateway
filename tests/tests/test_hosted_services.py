"""Homepage wrapper contracts for every managed service."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI

from llm_budget_gateway.hosted_services import SERVICES, create_hosted_app


def test_every_managed_service_has_unique_homepage_metadata():
    assert len(SERVICES) == 15
    assert len({item.port for item in SERVICES.values()}) == 15
    assert all(
        item.name and item.description and item.factory for item in SERVICES.values()
    )


@pytest.mark.asyncio
async def test_wrapper_adds_root_homepage_without_changing_existing_routes():
    original = FastAPI()

    @original.get("/health")
    async def health():
        return {"status": "ok"}

    with patch(
        "llm_budget_gateway.hosted_services._resolve_factory",
        return_value=lambda: original,
    ):
        app = create_hosted_app("gateway")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://service"
    ) as client:
        home = await client.get("/")
        health_response = await client.get("/health")

    assert home.status_code == 200
    assert "Gateway" in home.text
    assert "OpenAPI documentation" in home.text
    assert "Unified Console" in home.text
    assert health_response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_existing_root_route_is_preserved():
    original = FastAPI()

    @original.get("/")
    async def root():
        return {"original": True}

    with patch(
        "llm_budget_gateway.hosted_services._resolve_factory",
        return_value=lambda: original,
    ):
        app = create_hosted_app("control")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://service"
    ) as client:
        response = await client.get("/")

    assert response.json() == {"original": True}


def test_unknown_service_is_rejected():
    with pytest.raises(ValueError, match="unknown hosted service"):
        create_hosted_app("missing")
