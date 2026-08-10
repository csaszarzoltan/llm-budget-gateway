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
    #: Dynamic cooldown ladder in seconds. A target that fails repeatedly
    #: (429/5xx/timeout) escalates through this ladder instead of being
    #: parked for a fixed duration: 1m → 5m → 15m → 1h → 2h → 4h → 8h →
    #: 12h → 18h → 1d. A successful call resets the strike count to zero.
    #: Env ``GATEWAY_COOLDOWN_LADDER`` (JSON array of ints).
    cooldown_ladder: list[int] = [
        60, 300, 900, 3600, 7200, 14400, 28800, 43200, 64800, 86400,
    ]
    #: Disable the dynamic cooldown ladder and use the static per-target
    #: ``cooldown_seconds`` (default 3600) instead. When ``True``, the ladder
    #: is ignored and every failure applies the same fixed cooldown.
    #: Env ``GATEWAY_COOLDOWN_DYNAMIC`` (default ``true``).
    cooldown_dynamic: bool = True

    model_config = SettingsConfigDict(env_prefix="GATEWAY_")
