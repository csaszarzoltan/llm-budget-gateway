"""Core OpenAI-compatible proxy router.

Owns the request lifecycle: auth -> scopes -> sync enforce -> forward -> cost
record. Placeholder stub for the TDD RED phase — behavioral methods raise
NotImplementedError until implemented (P0-1). Interface is normative per
analysis brief §4 P0-1.
"""

from __future__ import annotations

from dataclasses import dataclass

from .budget_enforcement import BudgetEnforcer, BudgetScope
from .config import Settings
from .cost_tracking import CostTracker, TokenUsage
from .model_fallback import FallbackManager


class ApiKeyError(Exception):
    """Raised when the virtual API key is missing or unknown (HTTP 401)."""


@dataclass
class ProviderResponse:
    status_code: int
    body: dict | str
    headers: dict[str, str]
    model: str  # actual model that served the request
    usage: TokenUsage | None
    latency_ms: int


class GatewayProxy:
    """Owns the request lifecycle: auth -> scopes -> sync enforce -> forward -> cost record."""

    def __init__(
        self,
        settings: Settings,
        cost_tracker: CostTracker,
        budget_enforcer: BudgetEnforcer,
        fallback_manager: FallbackManager,
    ) -> None:
        self._settings = settings
        self._cost_tracker = cost_tracker
        self._budget_enforcer = budget_enforcer
        self._fallback_manager = fallback_manager

    async def handle_chat_completion(self, body: dict, api_key: str, headers: dict) -> ProviderResponse:
        raise NotImplementedError

    async def handle_completion(self, body: dict, api_key: str, headers: dict) -> ProviderResponse:
        raise NotImplementedError

    async def handle_embeddings(self, body: dict, api_key: str, headers: dict) -> ProviderResponse:
        raise NotImplementedError

    async def forward(self, model: str, body: dict, stream: bool = False) -> ProviderResponse:
        # Uses litellm.acompletion / litellm.acompletion(stream=True) internally.
        raise NotImplementedError

    def resolve_scopes(self, api_key: str, headers: dict) -> list[BudgetScope]:
        # Combines key scope + header-mapped user/team scopes + global scope.
        # Raises ApiKeyError (401) if api_key not in Settings.virtual_keys.
        raise NotImplementedError
