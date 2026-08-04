"""Provider connection, encrypted credentials and model discovery acceptance tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from llm_budget_gateway.provider_connections import (
    CredentialVault,
    ProviderConnectionStore,
    ProviderDiscovery,
)


@pytest.fixture
def store(tmp_path: Path) -> ProviderConnectionStore:
    return ProviderConnectionStore(
        sqlite3.connect(":memory:"), CredentialVault(tmp_path / "master.key")
    )


def test_same_provider_type_can_be_added_under_multiple_aliases(
    store: ProviderConnectionStore,
) -> None:
    first = store.create(
        {
            "name": "OpenAI Production",
            "slug": "openai-prod",
            "provider_type": "openai",
            "api_key": "sk-one",
            "base_url": "https://api.openai.com/v1",
            "organization": "org-a",
        }
    )
    second = store.create(
        {
            "name": "OpenAI Development",
            "slug": "openai-dev",
            "provider_type": "openai",
            "api_key": "sk-two",
            "base_url": "https://api.openai.com/v1",
        }
    )
    assert first["provider_type"] == second["provider_type"] == "openai"
    assert {item["slug"] for item in store.list()} == {"openai-prod", "openai-dev"}
    assert all("api_key" not in item for item in store.list())
    assert all(item["credential_status"] == "configured" for item in store.list())


def test_provider_specific_schema_and_secret_roundtrip(
    store: ProviderConnectionStore,
) -> None:
    schemas = {item["id"]: item for item in store.provider_types()}
    assert [field["name"] for field in schemas["azure_openai"]["fields"]] == [
        "api_key",
        "base_url",
        "api_version",
    ]
    assert "project_id" in [field["name"] for field in schemas["vertex_ai"]["fields"]]
    created = store.create(
        {
            "name": "Azure EU",
            "slug": "azure-eu",
            "provider_type": "azure_openai",
            "api_key": "secret",
            "base_url": "https://example.openai.azure.com",
            "api_version": "2025-04-01-preview",
        }
    )
    secret = store.connection_secret(created["id"])
    assert secret["api_key"] == "secret"
    assert "secret" not in store.raw_encrypted_value(created["id"])


@pytest.mark.asyncio
async def test_openai_compatible_model_discovery_and_sync(
    store: ProviderConnectionStore,
) -> None:
    created = store.create(
        {
            "name": "Custom",
            "slug": "custom-eu",
            "provider_type": "openai_compatible",
            "api_key": "token",
            "base_url": "https://models.example/v1",
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://models.example/v1/models"
        assert request.headers["authorization"] == "Bearer token"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "model-b", "owned_by": "acme"},
                    {"id": "model-a", "owned_by": "acme"},
                ]
            },
        )

    discovery = ProviderDiscovery(store, transport=httpx.MockTransport(handler))
    result = await discovery.sync(created["id"])
    assert [model["id"] for model in result["models"]] == ["model-a", "model-b"]
    assert store.get(created["id"])["model_count"] == 2
    assert store.models(created["id"])[0]["gateway_model"] == "@custom-eu/model-a"


@pytest.mark.asyncio
async def test_anthropic_and_google_discovery_formats(
    store: ProviderConnectionStore,
) -> None:
    anthropic = store.create(
        {
            "name": "Anthropic Prod",
            "slug": "anthropic-prod",
            "provider_type": "anthropic",
            "api_key": "ant",
            "base_url": "https://api.anthropic.com/v1",
        }
    )
    google = store.create(
        {
            "name": "Gemini Prod",
            "slug": "gemini-prod",
            "provider_type": "gemini",
            "api_key": "gem",
            "base_url": "https://generativelanguage.googleapis.com/v1beta",
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.anthropic.com":
            assert request.headers["x-api-key"] == "ant"
            return httpx.Response(
                200, json={"data": [{"id": "claude-sonnet", "display_name": "Sonnet"}]}
            )
        assert request.url.params["key"] == "gem"
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-pro",
                        "displayName": "Gemini Pro",
                        "supportedGenerationMethods": ["generateContent"],
                    }
                ]
            },
        )

    discovery = ProviderDiscovery(store, transport=httpx.MockTransport(handler))
    assert (await discovery.sync(anthropic["id"]))["models"][0]["id"] == "claude-sonnet"
    assert (await discovery.sync(google["id"]))["models"][0]["id"] == "gemini-pro"


@pytest.mark.asyncio
async def test_discovery_errors_are_actionable(store: ProviderConnectionStore) -> None:
    item = store.create(
        {
            "name": "Broken",
            "slug": "broken",
            "provider_type": "openai",
            "api_key": "bad",
            "base_url": "https://api.openai.com/v1",
        }
    )
    discovery = ProviderDiscovery(
        store,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                401, json={"error": {"message": "invalid key"}}
            )
        ),
    )
    with pytest.raises(ValueError, match="authentication failed"):
        await discovery.sync(item["id"])
    assert store.get(item["id"])["status"] == "error"


@pytest.mark.asyncio
async def test_provider_connection_http_wizard_flow(tmp_path: Path) -> None:
    from llm_budget_gateway.console_api import create_console_app

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "gpt-test"}]})

    app = create_console_app(
        provider_connection=sqlite3.connect(":memory:", check_same_thread=False),
        credential_key_path=tmp_path / "key",
        provider_discovery_transport=httpx.MockTransport(handler),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://console"
    ) as client:
        kinds = await client.get("/v1/product/provider-types")
        assert kinds.status_code == 200
        created = await client.post(
            "/v1/product/provider-connections",
            json={
                "name": "OpenAI Prod",
                "slug": "openai-prod",
                "provider_type": "openai",
                "api_key": "sk-test",
                "base_url": "https://api.openai.com/v1",
                "region": "eu",
            },
        )
        assert created.status_code == 201
        provider_id = created.json()["id"]
        sync = await client.post(
            f"/v1/product/provider-connections/{provider_id}/sync-models"
        )
        assert sync.status_code == 200
        models = await client.get(
            f"/v1/product/provider-connections/{provider_id}/models"
        )
        assert models.json()["models"][0]["gateway_model"] == "@openai-prod/gpt-test"
        listing = await client.get("/v1/product/provider-connections")
        assert "api_key" not in str(listing.json())


@pytest.mark.asyncio
async def test_home_and_global_model_catalog_use_named_connections(
    tmp_path: Path,
) -> None:
    from llm_budget_gateway.console_api import create_console_app

    app = create_console_app(
        provider_connection=sqlite3.connect(":memory:", check_same_thread=False),
        credential_key_path=tmp_path / "key",
        provider_discovery_transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"data": [{"id": "gpt-test"}]})
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://console"
    ) as client:
        created = (
            await client.post(
                "/v1/product/provider-connections",
                json={
                    "name": "Prod",
                    "slug": "prod",
                    "provider_type": "openai",
                    "api_key": "x",
                    "base_url": "https://api.openai.com/v1",
                },
            )
        ).json()
        await client.post(
            f"/v1/product/provider-connections/{created['id']}/sync-models"
        )
        assert (await client.get("/v1/product/home")).json()["counts"]["providers"] == 1
        assert (await client.get("/v1/product/discovered-models")).json()["models"][0][
            "gateway_model"
        ] == "@prod/gpt-test"
