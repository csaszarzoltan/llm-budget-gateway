"""Tenant-authenticated Activation Center API."""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException

from . import activation_suite as s


def create_activation_app(api_key: str | None = None) -> FastAPI:
    key = (
        api_key if api_key is not None else os.getenv("GATEWAY_ACTIVATION_API_KEY", "")
    )
    actions = {
        "setup-progress": lambda b: s.SetupProgress().evaluate(**b),
        "environment-template": lambda b: s.EnvironmentTemplate().build(**b),
        "provider-credentials": lambda b: s.ProviderCredentialCheck().evaluate(**b),
        "port-plan": lambda b: s.PortPlan().validate(**b),
        "configuration-doctor": lambda b: s.ConfigurationDoctor().inspect(**b),
        "first-request": lambda b: s.FirstRequestBuilder().build(**b),
        "budget-starter": lambda b: s.BudgetStarter().build(**b),
        "service-profile": lambda b: s.ServiceProfile().resolve(**b),
        "diagnostic-bundle": lambda b: s.DiagnosticBundle().build(**b),
        "activation-gate": lambda b: s.ActivationGate().decide(**b),
    }
    app = FastAPI(title="Gateway Activation API", version="8.0.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/activation/{capability}")
    async def run(
        capability: str,
        body: dict[str, Any],
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict[str, object]:
        if not key:
            raise HTTPException(503, "activation API key is not configured")
        if authorization != f"Bearer {key}" or not x_tenant_id:
            raise HTTPException(401, "authentication and tenant are required")
        action = actions.get(capability)
        if action is None:
            raise HTTPException(404, "unknown activation capability")
        try:
            return action(dict(body))
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    return app
