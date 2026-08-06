"""TDD acceptance tests for the task-first gateway product console."""

from __future__ import annotations

import sqlite3

import httpx
import pytest

from llm_budget_gateway.console_api import create_console_app
from llm_budget_gateway.product_console import ProductConsoleStore


@pytest.fixture
def store() -> ProductConsoleStore:
    return ProductConsoleStore(sqlite3.connect(":memory:", check_same_thread=False))


def test_activation_progress_is_derived_from_real_objects(
    store: ProductConsoleStore,
) -> None:
    assert store.home("developer")["activation"]["complete"] == 0
    provider = store.create_provider(
        "OpenAI Production", "openai", "eu", ["gpt-4o", "gpt-4o-mini"]
    )
    route = store.create_route(
        "support-global",
        [
            {
                "model": "openai/gpt-4o",
                "priority": 10,
                "timezone": "Europe/Zurich",
                "start": "08:00",
                "end": "18:00",
            },
            {
                "model": "openai/gpt-4o-mini",
                "priority": 20,
                "timezone": "UTC",
                "start": "00:00",
                "end": "23:59",
            },
        ],
    )
    store.publish_route(route["id"])
    app = store.create_application("Hermes Agent", "support-global")
    assert app["api_key"].startswith("gw_")
    home = store.home("developer")
    assert home["activation"]["complete"] == 4
    assert home["activation"]["next_action"] == "Send the first request"
    assert home["counts"] == {"applications": 1, "routes": 1, "providers": 1}
    assert provider["status"] == "healthy"


def test_published_route_by_name_resolves_only_active_routes(
    store: ProductConsoleStore,
) -> None:
    route = store.create_route(
        "support-global",
        [
            {
                "model": "openai/gpt-4o",
                "priority": 10,
                "timezone": "UTC",
                "start": "00:00",
                "end": "23:59",
            }
        ],
    )
    assert store.published_route_by_name("support-global") is None  # draft
    store.publish_route(route["id"])
    found = store.published_route_by_name("support-global")
    assert found is not None
    assert found["name"] == "support-global"
    assert found["targets"][0]["model"] == "openai/gpt-4o"
    assert store.published_route_by_name("missing") is None


def test_authenticate_application_hashes_key(store: ProductConsoleStore) -> None:
    app = store.create_application("Hermes Agent", "support-global")
    identity = store.authenticate_application(app["api_key"])
    assert identity["id"] == app["id"]
    assert identity["name"] == "Hermes Agent"
    with pytest.raises(PermissionError):
        store.authenticate_application("gw_wrong-key")


def test_provider_catalog_and_connection_health(store: ProductConsoleStore) -> None:
    provider = store.create_provider(
        "Anthropic EU", "anthropic", "eu", ["claude-sonnet"]
    )
    updated = store.set_provider_health(provider["id"], False)
    assert updated["status"] == "unavailable"
    catalog = store.providers()
    assert catalog[0]["models"][0]["capabilities"]
    assert catalog[0]["credential_status"] == "configured"


def test_route_templates_versions_test_and_publish(store: ProductConsoleStore) -> None:
    templates = {item["id"] for item in store.route_templates()}
    assert templates == {
        "reliable-fallback",
        "cost-aware",
        "follow-the-sun",
        "quality-tiers",
        "blank",
    }
    route = store.create_route(
        "global",
        [
            {
                "model": "primary",
                "priority": 10,
                "timezone": "Europe/Zurich",
                "start": "08:00",
                "end": "18:00",
            },
            {
                "model": "backup",
                "priority": 20,
                "timezone": "America/New_York",
                "start": "08:00",
                "end": "18:00",
            },
        ],
    )
    decision = store.test_route(route["id"], "2026-08-04T19:00:00+02:00", [])
    assert decision["selected_model"] == "backup"
    assert decision["excluded"][0]["reason"] == "outside_schedule"
    assert store.publish_route(route["id"])["published_version"] == 1
    edited = store.update_route(
        route["id"],
        [
            {
                "model": "new",
                "priority": 10,
                "timezone": "UTC",
                "start": "00:00",
                "end": "23:59",
            }
        ],
    )
    assert edited["draft_version"] == 2
    assert edited["published_version"] == 1


def test_request_activity_usage_attention_and_role_views(
    store: ProductConsoleStore,
) -> None:
    provider = store.create_provider("OpenAI", "openai", "eu", ["gpt-4o"])
    route = store.create_route(
        "support",
        [
            {
                "model": "openai/gpt-4o",
                "priority": 10,
                "timezone": "UTC",
                "start": "00:00",
                "end": "23:59",
            }
        ],
    )
    store.publish_route(route["id"])
    application = store.create_application("Support", "support")
    store.record_request(
        application["id"], "support", "openai/gpt-4o", 0.42, 820, True, None
    )
    store.set_provider_health(provider["id"], False)
    operator = store.home("operator")
    finops = store.home("finops")
    developer = store.home("developer")
    assert operator["attention"][0]["kind"] == "provider"
    assert finops["primary_panel"] == "usage"
    assert developer["primary_panel"] == "integration"
    assert developer["metrics"]["requests"] == 1
    assert developer["metrics"]["cost_usd"] == pytest.approx(0.42)
    assert store.activity()[0]["route"] == "support"
    assert store.usage()["by_route"][0]["cost_usd"] == pytest.approx(0.42)


@pytest.mark.asyncio
async def test_complete_product_api_flow_real_http() -> None:
    app = create_console_app(
        product_connection=sqlite3.connect(":memory:", check_same_thread=False)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://console"
    ) as client:
        provider = await client.post(
            "/v1/product/providers",
            json={
                "name": "OpenAI",
                "slug": "openai",
                "region": "eu",
                "models": ["gpt-4o"],
            },
        )
        assert provider.status_code == 201
        route = await client.post(
            "/v1/product/routes",
            json={
                "name": "support",
                "targets": [
                    {
                        "model": "openai/gpt-4o",
                        "priority": 10,
                        "timezone": "UTC",
                        "start": "00:00",
                        "end": "23:59",
                    }
                ],
            },
        )
        route_id = route.json()["id"]
        assert (
            await client.post(f"/v1/product/routes/{route_id}/publish")
        ).status_code == 200
        application = await client.post(
            "/v1/product/applications",
            json={"name": "Hermes", "default_route": "support"},
        )
        assert application.status_code == 201
        assert (await client.get("/v1/product/home?role=developer")).status_code == 200
        assert (await client.get("/v1/product/templates")).status_code == 200
        assert (await client.get("/v1/product/usage")).status_code == 200
        assert (await client.get("/v1/product/activity")).status_code == 200


def test_target_context_length_validation_and_persistence(
    store: ProductConsoleStore,
) -> None:
    """context_length is optional, validated as positive int, and survives
    the route round-trip so the proxy can expose it via /v1/models."""
    route = store.create_route(
        "ctx-test",
        [
            {
                "model": "@openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
                "priority": 10,
                "timezone": "UTC",
                "start": "00:00",
                "end": "23:59",
                "context_length": 128000,
            },
            {
                "model": "@opencode-go/deepseek-v4-flash",
                "priority": 20,
                "timezone": "UTC",
                "context_length": 64000,
            },
        ],
    )
    targets = store.route(route["id"])["targets"]
    assert targets[0]["context_length"] == 128000
    assert targets[1]["context_length"] == 64000

    # omitting context_length stays None (auto)
    route2 = store.create_route(
        "ctx-auto",
        [
            {
                "model": "plain-model",
                "priority": 10,
                "timezone": "UTC",
            }
        ],
    )
    assert store.route(route2["id"])["targets"][0]["context_length"] is None

    # invalid values are rejected
    for bad in ("-5", "abc", "0"):
        try:
            store.create_route(
                "ctx-bad",
                [
                    {
                        "model": "bad-model",
                        "priority": 10,
                        "timezone": "UTC",
                        "context_length": bad,
                    }
                ],
            )
            raise AssertionError(f"expected ValueError for context_length={bad!r}")
        except ValueError:
            pass
