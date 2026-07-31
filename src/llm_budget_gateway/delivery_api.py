"""Fail-closed tenant-authenticated Delivery API."""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException

from . import delivery_suite as s


def create_delivery_app(api_key: str | None = None) -> FastAPI:
    key = api_key if api_key is not None else os.getenv("GATEWAY_DELIVERY_API_KEY", "")
    services = {
        "environment-readiness": lambda b: s.EnvironmentReadiness().evaluate(**b),
        "configuration-drift": lambda b: s.ConfigurationDrift().compare(**b),
        "capacity-plan": lambda b: s.CapacityPlanner().plan(**b),
        "dependency-health": lambda b: s.HealthDependencyPolicy().evaluate(**b),
        "rollout-plan": lambda b: s.RolloutPlanner().build(**b),
        "rollback-decision": lambda b: s.RollbackDecision().decide(**b),
        "observability-coverage": lambda b: s.ObservabilityCoverage().assess(**b),
        "alert-routes": lambda b: s.AlertRouteValidator().validate(**b),
        "runbook-coverage": lambda b: s.RunbookCoverage().assess(**b),
        "release-manifest": lambda b: s.ReleaseManifest().build(**b),
    }
    app = FastAPI(title="Gateway Delivery API", version="6.0.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/delivery/{capability}")
    async def execute(
        capability: str,
        body: dict[str, Any],
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict[str, object]:
        if not key:
            raise HTTPException(503, "delivery API key is not configured")
        if authorization != f"Bearer {key}" or not x_tenant_id:
            raise HTTPException(401, "authentication and tenant are required")
        service = services.get(capability)
        if service is None:
            raise HTTPException(404, "unknown delivery capability")
        try:
            return service(dict(body))
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    return app
