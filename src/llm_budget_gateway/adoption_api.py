"""Authenticated Product Adoption API."""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException

from . import adoption_suite as s


def create_adoption_app(api_key: str | None = None) -> FastAPI:
    key = api_key if api_key is not None else os.getenv("GATEWAY_ADOPTION_API_KEY", "")
    actions = {
        "activation-funnel": lambda b: s.ActivationFunnel().calculate(**b),
        "cohort-retention": lambda b: s.CohortRetention().calculate(**b),
        "feature-adoption": lambda b: s.FeatureAdoption().summarize(**b),
        "experiment-assignment": lambda b: s.ExperimentAssignment().assign(**b),
        "experiment-outcome": lambda b: s.ExperimentOutcome().evaluate(**b),
        "feedback-themes": lambda b: s.FeedbackTheme().aggregate(**b),
        "pricing-signal": lambda b: s.PricingSignal().summarize(**b),
        "rollout-cohort": lambda b: s.RolloutCohort().decide(**b),
        "success-threshold": lambda b: s.SuccessThreshold().evaluate(**b),
        "adoption-report": lambda b: s.AdoptionReport().build(**b),
    }
    app = FastAPI(title="Gateway Product Adoption API", version="9.0.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/adoption/{capability}")
    async def run(
        capability: str,
        body: dict[str, Any],
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict[str, object]:
        if not key:
            raise HTTPException(503, "adoption API key is not configured")
        if authorization != f"Bearer {key}" or not x_tenant_id:
            raise HTTPException(401, "authentication and tenant are required")
        action = actions.get(capability)
        if action is None:
            raise HTTPException(404, "unknown adoption capability")
        try:
            return action(dict(body))
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    return app
