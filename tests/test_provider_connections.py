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


def test_custom_provider_schema_is_available_and_configurable(
    store: ProviderConnectionStore,
) -> None:
    """Custom endpoints must be available without pretending to be OpenAI."""
    custom = next(item for item in store.provider_types() if item["id"] == "custom")
    fields = {field["name"]: field for field in custom["fields"]}
    assert fields["base_url"]["required"] is True
    assert fields["api_key"]["required"] is False
    assert {"model_list_path", "auth_header", "auth_prefix", "extra_headers_json", "models_field", "model_id_field"} <= fields.keys()


@pytest.mark.asyncio
async def test_custom_provider_discovers_models_with_configurable_contract(
    store: ProviderConnectionStore,
) -> None:
    """Custom discovery must honor path, authentication, headers and JSON fields."""
    created = store.create(
        {
            "name": "Private Catalog",
            "slug": "private-catalog",
            "provider_type": "custom",
            "api_key": "token-123",
            "base_url": "https://models.example/api",
            "model_list_path": "/catalog",
            "auth_header": "X-Token",
            "auth_prefix": "Token ",
            "extra_headers_json": '{"X-Workspace":"zurich"}',
            "models_field": "items",
            "model_id_field": "key",
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://models.example/api/catalog"
        assert request.headers["X-Token"] == "Token token-123"
        assert request.headers["X-Workspace"] == "zurich"
        return httpx.Response(200, json={"items": [{"key": "local-a", "name": "Local A"}]})

    result = await ProviderDiscovery(
        store, transport=httpx.MockTransport(handler)
    ).sync(created["id"])
    assert result["models"][0]["id"] == "local-a"
    assert result["models"][0]["display_name"] == "Local A"


def test_provider_picker_has_a_real_scroll_overflow_and_custom_option() -> None:
    """The seventh custom card must sit inside a visibly bounded scroll region."""
    source = Path("ui/src/main.tsx").read_text(encoding="utf-8")
    styles = Path("ui/src/styles.css").read_text(encoding="utf-8")
    assert "Available providers" in source
    assert "provider-picker-hint" in source
    assert "Custom provider" in Path("src/llm_budget_gateway/provider_connections.py").read_text(encoding="utf-8")
    rule = styles.split(".provider-picker{", 1)[1].split("}", 1)[0]
    assert "height:250px" in rule
    assert "overflow-y:scroll" in rule


def test_update_keeps_stored_secret_when_key_not_resent(
    store: ProviderConnectionStore,
) -> None:
    created = store.create(
        {
            "name": "OpenAI Production",
            "slug": "openai-prod",
            "provider_type": "openai",
            "api_key": "sk-original",
            "base_url": "https://api.openai.com/v1",
        }
    )
    updated = store.update(
        created["id"],
        {
            "name": "OpenAI Prod Renamed",
            "slug": "openai-prod",
            "base_url": "https://alt.openai.com/v1",
            "api_key": "",  # empty -> keep stored key
        },
    )
    assert updated["name"] == "OpenAI Prod Renamed"
    assert updated["base_url"] == "https://alt.openai.com/v1"
    secret = store.connection_secret(created["id"])
    assert secret["api_key"] == "sk-original"  # untouched
    assert secret["base_url"] == "https://alt.openai.com/v1"


def test_update_replaces_secret_when_key_resent(
    store: ProviderConnectionStore,
) -> None:
    created = store.create(
        {
            "name": "OpenAI Production",
            "slug": "openai-prod",
            "provider_type": "openai",
            "api_key": "sk-old",
            "base_url": "https://api.openai.com/v1",
        }
    )
    store.update(
        created["id"],
        {
            "name": "OpenAI Production",
            "slug": "openai-prod",
            "api_key": "sk-new",
            "base_url": "https://api.openai.com/v1",
        },
    )
    assert store.connection_secret(created["id"])["api_key"] == "sk-new"


def test_update_validates_slug_and_base_url(
    store: ProviderConnectionStore,
) -> None:
    created = store.create(
        {
            "name": "OpenAI Production",
            "slug": "openai-prod",
            "provider_type": "openai",
            "api_key": "sk-one",
            "base_url": "https://api.openai.com/v1",
        }
    )
    with pytest.raises(ValueError):
        store.update(created["id"], {"slug": "bad slug!", "name": "X"})
    with pytest.raises(ValueError):
        store.update(
            created["id"],
            {"base_url": "ftp://nope", "name": "X", "slug": "openai-prod"},
        )


def test_update_unknown_provider_raises_key_error(
    store: ProviderConnectionStore,
) -> None:
    with pytest.raises(KeyError):
        store.update("provider_does_not_exist", {"name": "X"})


def test_get_exposes_user_agent_and_base_url_without_secret(
    store: ProviderConnectionStore,
) -> None:
    created = store.create(
        {
            "name": "OpenAI Production",
            "slug": "openai-prod",
            "provider_type": "openai_compatible",
            "api_key": "sk-hidden",
            "base_url": "https://api.openai.com/v1",
            "user_agent": "opencode/1.14.41",
        }
    )
    item = store.get(created["id"])
    assert item["base_url"] == "https://api.openai.com/v1"
    assert item["user_agent"] == "opencode/1.14.41"
    assert "api_key" not in item
    assert "sk-hidden" not in str(item)


def test_context_length_falls_back_to_openrouter_catalog(
    store: ProviderConnectionStore,
) -> None:
    """Providers without a context window in /models get it from OpenRouter."""
    openrouter = store.create(
        {
            "name": "OpenRouter",
            "slug": "openrouter",
            "provider_type": "openai_compatible",
            "api_key": "sk-or",
            "base_url": "https://openrouter.ai/api/v1",
        }
    )
    store.save_models(
        openrouter["id"],
        [
            {
                "id": "deepseek/deepseek-v4-flash",
                "raw": {"id": "deepseek/deepseek-v4-flash", "context_length": 1048576},
            }
        ],
    )
    og = store.create(
        {
            "name": "OpenCode Go",
            "slug": "opencode-go",
            "provider_type": "openai_compatible",
            "api_key": "sk-go",
            "base_url": "https://opencode.ai/zen/go/v1",
        }
    )
    store.save_models(
        og["id"],
        [{"id": "deepseek-v4-flash", "raw": {"id": "deepseek-v4-flash"}}],
    )
    models = store.models(og["id"])
    assert models[0]["context_length"] == 1048576


def test_context_length_accepts_google_input_token_limit() -> None:
    from llm_budget_gateway.provider_connections import _extract_context_length

    assert (
        _extract_context_length(
            {"name": "models/gemini-2.0-flash", "inputTokenLimit": 1048576}
        )
        == 1048576
    )


def test_context_length_reads_nested_metadata_block() -> None:
    from llm_budget_gateway.provider_connections import _extract_context_length

    assert (
        _extract_context_length(
            {"id": "x", "metadata": {"context_length": 131072, "other": 1}}
        )
        == 131072
    )
