"""FastAPI app factory for the LLM budget gateway.

Placeholder stub for the TDD RED phase — create_app raises
NotImplementedError until implemented (P0-1). Interface is normative per
analysis brief §4 P0-1.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

    from .config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    # Wires Settings -> PriceMap -> CostTracker -> BudgetEnforcer -> FallbackManager
    # -> GatewayProxy -> APIRouter. Mounts POST /v1/chat/completions,
    # /v1/completions, /v1/embeddings, GET /v1/models, GET /health.
    raise NotImplementedError
