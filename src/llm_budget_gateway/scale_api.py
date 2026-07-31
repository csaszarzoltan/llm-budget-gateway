"""Authenticated Scale Center API."""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException

from . import scale_suite as s


def create_scale_app(api_key: str | None = None) -> FastAPI:
    key = api_key if api_key is not None else os.getenv("GATEWAY_SCALE_API_KEY", "")
    services = {
        "storage-topology": lambda b: s.StorageTopology().assess(**b),
        "replication-quorum": lambda b: s.ReplicationQuorum().calculate(**b),
        "partition-plan": lambda b: s.PartitionPlanner().plan(**b),
        "consistency-policy": lambda b: s.ConsistencyPolicy().decide(**b),
        "failover-plan": lambda b: s.FailoverPlanner().build(**b),
        "migration-readiness": lambda b: s.MigrationReadiness().decide(**b),
        "connection-pool": lambda b: s.ConnectionPoolPlanner().plan(**b),
        "tenant-shard": lambda b: s.TenantShardAssignment().assign(**b),
        "residency-topology": lambda b: s.ResidencyTopology().evaluate(**b),
        "disaster-recovery": lambda b: s.DisasterRecoveryObjective().evaluate(**b),
    }
    app = FastAPI(title="Gateway Scale API", version="7.0.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/scale/{capability}")
    async def execute(
        capability: str,
        body: dict[str, Any],
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict[str, object]:
        if not key:
            raise HTTPException(503, "scale API key is not configured")
        if authorization != f"Bearer {key}" or not x_tenant_id:
            raise HTTPException(401, "authentication and tenant are required")
        service = services.get(capability)
        if service is None:
            raise HTTPException(404, "unknown scale capability")
        try:
            return service(dict(body))
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    return app
