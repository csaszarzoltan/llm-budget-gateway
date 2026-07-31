"""Configuration for the LLM budget gateway.

Loaded from environment with the ``GATEWAY_`` prefix (pydantic-settings v2).
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Gateway settings. Every field is overridable via ``GATEWAY_<FIELD>``.

    Normative interface per analysis brief §4 P0-1 / P0-0.
    """

    database_url: str = "sqlite:///./gateway.db"
    budget_config_path: str = "budgets.yaml"
    virtual_keys: dict[str, str] = {}  # api_key -> key_id (v0.1 static table)
    user_header_mappings: dict[str, str] = {}  # header_name -> scope_kind ("user"|"team")
    pricing_overrides: dict[str, dict] = {}  # model -> {"input_cost_per_million": x, "output_cost_per_million": y}
    fallback_configs: list[dict] = []  # raw FallbackConfig dicts

    model_config = SettingsConfigDict(env_prefix="GATEWAY_")
