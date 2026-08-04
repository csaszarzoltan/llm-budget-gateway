"""TDD acceptance coverage for the remaining research roadmap features."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3

import httpx
import pytest

from llm_budget_gateway.completion_features import (
    DistributedCounter,
    IdentityGateway,
    MigrationPlanner,
    PolicyRouteSimulator,
    TenantRepository,
)
from llm_budget_gateway.console_api import create_console_app


def test_tenant_repository_never_crosses_tenants_and_supports_export() -> None:
    repo = TenantRepository(sqlite3.connect(":memory:"))
    repo.put("acme", "policy", "p1", {"effect": "allow"})
    repo.put("beta", "policy", "p1", {"effect": "deny"})
    assert repo.get("acme", "policy", "p1") == {"effect": "allow"}
    assert repo.list("beta", "policy") == [{"id": "p1", "value": {"effect": "deny"}}]
    export = repo.export_tenant("acme")
    assert export["tenant_id"] == "acme"
    assert export["records"][0]["id"] == "p1"
    with pytest.raises(KeyError):
        repo.get("acme", "policy", "missing")
    with pytest.raises(ValueError, match="tenant"):
        repo.put("", "policy", "x", {})


def test_distributed_counter_atomic_limits_and_idempotency() -> None:
    counter = DistributedCounter(sqlite3.connect(":memory:"), now=lambda: 100)
    assert (
        counter.reserve("acme", "rpm", "req-1", amount=2, limit=3, window_seconds=60)[
            "allowed"
        ]
        is True
    )
    duplicate = counter.reserve(
        "acme", "rpm", "req-1", amount=2, limit=3, window_seconds=60
    )
    assert duplicate["used"] == 2
    assert duplicate["idempotent"] is True
    blocked = counter.reserve(
        "acme", "rpm", "req-2", amount=2, limit=3, window_seconds=60
    )
    assert blocked["allowed"] is False
    assert blocked["used"] == 2
    assert (
        counter.reserve("beta", "rpm", "req-3", amount=3, limit=3, window_seconds=60)[
            "allowed"
        ]
        is True
    )


def test_migration_planner_requires_backup_rehearsal_and_rollback() -> None:
    planner = MigrationPlanner()
    blocked = planner.assess(
        source="sqlite",
        target="postgres",
        backup=False,
        schema_validated=True,
        rehearsal=True,
        rollback_tested=True,
        full_regression=True,
    )
    assert blocked["ready"] is False
    assert "backup" in blocked["gaps"]
    ready = planner.assess(
        source="sqlite",
        target="postgres",
        backup=True,
        schema_validated=True,
        rehearsal=True,
        rollback_tested=True,
        full_regression=True,
    )
    assert ready["ready"] is True
    assert ready["steps"][0] == "freeze writes"
    with pytest.raises(ValueError, match="target"):
        planner.assess(
            source="sqlite",
            target="sqlite",
            backup=True,
            schema_validated=True,
            rehearsal=True,
            rollback_tested=True,
            full_regression=True,
        )


def test_identity_gateway_verifies_signed_claims_and_roles() -> None:
    key = b"super-secret-key"
    claims = {"sub": "user-1", "tenant_id": "acme", "roles": ["operator"], "exp": 200}
    raw = json.dumps(claims, sort_keys=True, separators=(",", ":"))
    signature = hmac.new(key, raw.encode(), hashlib.sha256).hexdigest()
    identity = IdentityGateway(key, now=lambda: 100).verify(
        raw, signature, required_role="operator"
    )
    assert identity["tenant_id"] == "acme"
    with pytest.raises(PermissionError, match="signature"):
        IdentityGateway(key, now=lambda: 100).verify(
            raw, "bad", required_role="operator"
        )
    with pytest.raises(PermissionError, match="role"):
        IdentityGateway(key, now=lambda: 100).verify(
            raw, signature, required_role="admin"
        )
    expired = {**claims, "exp": 99}
    expired_raw = json.dumps(expired, sort_keys=True, separators=(",", ":"))
    expired_sig = hmac.new(key, expired_raw.encode(), hashlib.sha256).hexdigest()
    with pytest.raises(PermissionError, match="expired"):
        IdentityGateway(key, now=lambda: 100).verify(expired_raw, expired_sig)


def test_policy_route_simulator_explains_every_decision() -> None:
    result = PolicyRouteSimulator().simulate(
        request={
            "model": "logical",
            "estimated_cost": 1.5,
            "region": "eu",
            "tool": "search",
        },
        policy={"max_cost": 2, "allowed_regions": ["eu"], "allowed_tools": ["search"]},
        routes=[
            {"model": "expensive", "cost": 2.5, "healthy": True, "quality": 0.99},
            {"model": "mini", "cost": 1.0, "healthy": True, "quality": 0.92},
            {"model": "down", "cost": 0.5, "healthy": False, "quality": 0.95},
        ],
        minimum_quality=0.9,
    )
    assert result["allowed"] is True
    assert result["selected_model"] == "mini"
    assert [x["model"] for x in result["rejected_routes"]] == ["down", "expensive"]
    assert result["decision_path"][0]["gate"] == "budget"
    blocked = PolicyRouteSimulator().simulate(
        request={"estimated_cost": 3, "region": "eu", "tool": "search"},
        policy={"max_cost": 2, "allowed_regions": ["eu"], "allowed_tools": ["search"]},
        routes=[],
        minimum_quality=0.9,
    )
    assert blocked["allowed"] is False and blocked["reason"] == "budget"


@pytest.mark.asyncio
async def test_completion_api_real_http_flow() -> None:
    app = create_console_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://console"
    ) as client:
        simulation = await client.post(
            "/v1/console/simulate",
            json={
                "request": {"estimated_cost": 0.2, "region": "eu", "tool": "search"},
                "policy": {
                    "max_cost": 1,
                    "allowed_regions": ["eu"],
                    "allowed_tools": ["search"],
                },
                "routes": [
                    {"model": "mini", "cost": 0.1, "healthy": True, "quality": 0.9}
                ],
                "minimum_quality": 0.8,
            },
        )
        assert (
            simulation.status_code == 200
            and simulation.json()["selected_model"] == "mini"
        )
        migration = await client.post(
            "/v1/console/production/migration-readiness",
            json={
                "source": "sqlite",
                "target": "postgres",
                "backup": True,
                "schema_validated": True,
                "rehearsal": True,
                "rollback_tested": True,
                "full_regression": True,
            },
        )
        assert migration.status_code == 200 and migration.json()["ready"] is True


@pytest.mark.asyncio
async def test_completion_api_validation_paths() -> None:
    app = create_console_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://console"
    ) as client:
        bad_simulation = await client.post(
            "/v1/console/simulate",
            json={"request": {}, "policy": {}, "routes": [], "minimum_quality": "bad"},
        )
        bad_migration = await client.post(
            "/v1/console/production/migration-readiness",
            json={"source": "sqlite", "target": "sqlite"},
        )
    assert bad_simulation.status_code == 422
    assert bad_migration.status_code == 422
