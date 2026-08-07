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
    user_header_mappings: dict[
        str, str
    ] = {}  # header_name -> scope_kind ("user"|"team")
    pricing_overrides: dict[
        str, dict
    ] = {}  # model -> {"input_cost_per_million": x, "output_cost_per_million": y}
    fallback_configs: list[dict] = []  # raw FallbackConfig dicts
    #: Max seconds to wait for the upstream provider (first byte + each
    #: subsequent stream chunk) before failing the request. Env
    #: ``GATEWAY_PROVIDER_TIMEOUT``. A hung upstream must never hang the
    #: worker indefinitely (availability review checklist item 2).
    provider_timeout: float = 60.0
    #: Total wall-clock budget for a route's whole fallback chain. When
    #: several targets are in cooldown or timing out, the chain can exceed
    #: the client's own timeout (Hermes waits ~60-90s) and the client gives
    #: up ("provider failed after retries") even though a fallback would
    #: eventually answer. This caps the sum: once the budget is spent, the
    #: remaining candidates are skipped and the last one is tried with the
    #: leftover time. Env ``GATEWAY_ROUTE_TIMEOUT_BUDGET``.
    route_timeout_budget: float = 90.0

    model_config = SettingsConfigDict(env_prefix="GATEWAY_")
