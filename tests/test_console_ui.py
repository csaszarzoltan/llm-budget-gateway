"""Unified console catalog, UI, accessibility, and API contracts."""

from __future__ import annotations

import httpx
import pytest

from llm_budget_gateway.console_api import create_console_app
from llm_budget_gateway.console_ui import CENTERS, catalog, render_console


def test_catalog_covers_every_product_center_and_unique_port():
    slugs = {center.slug for center in CENTERS}
    assert slugs == {
        "gateway",
        "control",
        "intelligence",
        "operations",
        "quality",
        "security",
        "resilience",
        "optimization",
        "collaboration",
        "platform",
        "agentops",
        "fleet",
        "assurance",
        "delivery",
        "scale",
    }
    assert len({center.port for center in CENTERS}) == len(CENTERS)
    assert all(center.capabilities for center in CENTERS)
    assert len(catalog()) == 15


def test_console_renders_every_center_and_capability():
    page = render_console()
    for center in CENTERS:
        assert center.name in page
        assert str(center.port) in page
        for capability in center.capabilities:
            assert capability in page


def test_console_has_accessible_responsive_and_operational_states():
    page = render_console()
    required = (
        "Skip to content",
        "aria-label='Primary'",
        "aria-live='polite'",
        "focus-visible",
        "prefers-reduced-motion",
        "@media(max-width:760px)",
        "data-theme=dark",
        "Universal API runner",
        "Command palette",
        "Check service health",
        "sessionStorage",
        "AbortSignal.timeout",
        "Copy cURL",
        "No capability matches your search",
    )
    for marker in required:
        assert marker in page


def test_console_does_not_embed_credentials_or_remote_assets():
    page = render_console().lower()
    assert "sk-test" not in page
    assert "sk-live" not in page
    assert "authorization: bearer replace" not in page
    assert "<script src=" not in page
    assert "<link rel='stylesheet'" not in page


@pytest.mark.asyncio
async def test_console_api_serves_page_catalog_and_health():
    app = create_console_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://console"
    ) as client:
        health = await client.get("/health")
        page = await client.get("/console")
        result = await client.get("/v1/console/catalog")
    assert health.json() == {"status": "ok"}
    assert page.status_code == 200
    assert "LLM Budget Gateway Console" in page.text
    payload = result.json()
    assert payload["version"] == "7.1.0"
    assert payload["center_count"] == 15
    assert payload["capability_count"] == sum(len(x.capabilities) for x in CENTERS)
