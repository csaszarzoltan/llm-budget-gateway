"""Provider preset expansion derived from official compatibility documentation."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from llm_budget_gateway.provider_connections import (
    CredentialVault,
    ProviderConnectionStore,
)


@pytest.fixture
def store(tmp_path: Path) -> ProviderConnectionStore:
    return ProviderConnectionStore(
        sqlite3.connect(":memory:"), CredentialVault(tmp_path / "key")
    )


def test_catalog_contains_researched_provider_presets_with_ready_defaults(
    store: ProviderConnectionStore,
) -> None:
    schemas = {item["id"]: item for item in store.provider_types()}
    expected = {
        "zai": "https://api.z.ai/api/paas/v4",
        "zai_coding_plan": "https://api.z.ai/api/coding/paas/v4",
        "xiaomi_mimo": "https://api.xiaomimimo.com/v1",
        "xiaomi_coding_plan": "https://token-plan-cn.xiaomimimo.com/v1",
        "deepinfra": "https://api.deepinfra.com/v1/openai",
        "together": "https://api.together.ai/v1",
        "fireworks": "https://api.fireworks.ai/inference/v1",
        "nebius": "https://api.tokenfactory.nebius.com/v1",
        "siliconflow": "https://api.siliconflow.com/v1",
        "moonshot": "https://api.moonshot.ai/v1",
        "minimax": "https://api.minimax.io/v1",
        "dashscope": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "volcengine_ark": "https://ark.cn-beijing.volces.com/api/v3",
    }
    assert {key: schemas[key]["default_base_url"] for key in expected} == expected
    for key in expected:
        schema = schemas[key]
        assert schema["discovery"] == "openai"
        assert schema["protocol"] == "openai"
        assert schema["docs_url"].startswith("https://")
        assert schema["fields"][0]["name"] == "api_key"
        assert schema["fields"][1]["name"] == "base_url"


def test_preset_connection_uses_default_url_without_user_entry(
    store: ProviderConnectionStore,
) -> None:
    created = store.create(
        {
            "provider_type": "deepinfra",
            "name": "DeepInfra production",
            "slug": "deepinfra-prod",
            "api_key": "secret",
            "region": "global",
        }
    )
    secret = store.connection_secret(created["id"])
    assert secret["base_url"] == "https://api.deepinfra.com/v1/openai"
    assert secret["api_key"] == "secret"


def test_coding_plan_is_separate_from_payg_and_key_is_still_required(
    store: ProviderConnectionStore,
) -> None:
    schemas = {item["id"]: item for item in store.provider_types()}
    assert (
        schemas["xiaomi_mimo"]["default_base_url"]
        != schemas["xiaomi_coding_plan"]["default_base_url"]
    )
    assert (
        schemas["zai"]["default_base_url"]
        != schemas["zai_coding_plan"]["default_base_url"]
    )
    with pytest.raises(ValueError, match="API key"):
        store.create({"provider_type": "zai", "name": "Z.AI", "slug": "zai-global"})


def test_provider_catalog_has_unique_ids_and_sorted_featured_groups(
    store: ProviderConnectionStore,
) -> None:
    schemas = store.provider_types()
    ids = [item["id"] for item in schemas]
    assert len(ids) == len(set(ids))
    groups = {item["group"] for item in schemas}
    assert {
        "Frontier providers",
        "Open model clouds",
        "Coding plans",
        "Cloud platforms",
        "Custom",
    } <= groups
