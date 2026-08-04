"""Final research-roadmap controls: tenancy, counters, identity, migration and simulation."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import sqlite3
import time
from collections.abc import Callable
from typing import Any


class TenantRepository:
    """Strict tenant-keyed JSON repository with portable export semantics."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        connection.execute(
            "CREATE TABLE IF NOT EXISTS tenant_records(tenant_id TEXT NOT NULL, kind TEXT NOT NULL, record_id TEXT NOT NULL, value_json TEXT NOT NULL, PRIMARY KEY(tenant_id,kind,record_id))"
        )
        connection.commit()

    def put(
        self, tenant_id: str, kind: str, record_id: str, value: dict[str, Any]
    ) -> None:
        """Upsert one record inside an explicit non-empty tenant boundary."""
        if not tenant_id.strip():
            raise ValueError("tenant must be non-empty")
        if not kind.strip() or not record_id.strip():
            raise ValueError("kind and record id must be non-empty")
        self._connection.execute(
            "INSERT INTO tenant_records VALUES(?,?,?,?) ON CONFLICT(tenant_id,kind,record_id) DO UPDATE SET value_json=excluded.value_json",
            (
                tenant_id,
                kind,
                record_id,
                json.dumps(value, sort_keys=True, separators=(",", ":")),
            ),
        )
        self._connection.commit()

    def get(self, tenant_id: str, kind: str, record_id: str) -> dict[str, Any]:
        """Read one tenant-owned record or raise ``KeyError``."""
        row = self._connection.execute(
            "SELECT value_json FROM tenant_records WHERE tenant_id=? AND kind=? AND record_id=?",
            (tenant_id, kind, record_id),
        ).fetchone()
        if row is None:
            raise KeyError(record_id)
        return json.loads(row[0])

    def list(self, tenant_id: str, kind: str) -> list[dict[str, Any]]:
        """List records for exactly one tenant and kind."""
        rows = self._connection.execute(
            "SELECT record_id,value_json FROM tenant_records WHERE tenant_id=? AND kind=? ORDER BY record_id",
            (tenant_id, kind),
        ).fetchall()
        return [{"id": r[0], "value": json.loads(r[1])} for r in rows]

    def export_tenant(self, tenant_id: str) -> dict[str, Any]:
        """Create a canonical, migration-ready tenant export."""
        rows = self._connection.execute(
            "SELECT kind,record_id,value_json FROM tenant_records WHERE tenant_id=? ORDER BY kind,record_id",
            (tenant_id,),
        ).fetchall()
        return {
            "tenant_id": tenant_id,
            "records": [
                {"kind": r[0], "id": r[1], "value": json.loads(r[2])} for r in rows
            ],
        }


class DistributedCounter:
    """Transactional shared counter with idempotent reservations and tenant windows."""

    def __init__(
        self, connection: sqlite3.Connection, now: Callable[[], int] | None = None
    ) -> None:
        self._connection = connection
        self._now = now or (lambda: int(time.time()))
        connection.execute(
            "CREATE TABLE IF NOT EXISTS shared_counters(tenant_id TEXT, counter_key TEXT, bucket INTEGER, used INTEGER NOT NULL, PRIMARY KEY(tenant_id,counter_key,bucket))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS counter_reservations(tenant_id TEXT, counter_key TEXT, bucket INTEGER, request_id TEXT, amount INTEGER NOT NULL, PRIMARY KEY(tenant_id,counter_key,bucket,request_id))"
        )
        connection.commit()

    def reserve(
        self,
        tenant_id: str,
        key: str,
        request_id: str,
        *,
        amount: int,
        limit: int,
        window_seconds: int,
    ) -> dict[str, Any]:
        """Atomically reserve usage without exceeding a hard shared limit."""
        if min(amount, limit, window_seconds) <= 0:
            raise ValueError("amount, limit and window must be positive")
        bucket = (self._now() // window_seconds) * window_seconds
        with self._connection:
            prior = self._connection.execute(
                "SELECT amount FROM counter_reservations WHERE tenant_id=? AND counter_key=? AND bucket=? AND request_id=?",
                (tenant_id, key, bucket, request_id),
            ).fetchone()
            used_row = self._connection.execute(
                "SELECT used FROM shared_counters WHERE tenant_id=? AND counter_key=? AND bucket=?",
                (tenant_id, key, bucket),
            ).fetchone()
            used = int(used_row[0]) if used_row else 0
            if prior:
                return {
                    "allowed": True,
                    "used": used,
                    "limit": limit,
                    "idempotent": True,
                    "bucket": bucket,
                }
            if used + amount > limit:
                return {
                    "allowed": False,
                    "used": used,
                    "limit": limit,
                    "idempotent": False,
                    "bucket": bucket,
                }
            self._connection.execute(
                "INSERT INTO shared_counters VALUES(?,?,?,?) ON CONFLICT(tenant_id,counter_key,bucket) DO UPDATE SET used=used+excluded.used",
                (tenant_id, key, bucket, amount),
            )
            self._connection.execute(
                "INSERT INTO counter_reservations VALUES(?,?,?,?,?)",
                (tenant_id, key, bucket, request_id, amount),
            )
        return {
            "allowed": True,
            "used": used + amount,
            "limit": limit,
            "idempotent": False,
            "bucket": bucket,
        }


class MigrationPlanner:
    """Fail-closed readiness gate for SQLite-to-Postgres production migration."""

    def assess(
        self,
        *,
        source: str,
        target: str,
        backup: bool,
        schema_validated: bool,
        rehearsal: bool,
        rollback_tested: bool,
        full_regression: bool,
    ) -> dict[str, Any]:
        """Return gaps and a deterministic zero-data-loss migration sequence."""
        if source != "sqlite" or target != "postgres":
            raise ValueError("source must be sqlite and target must be postgres")
        checks = {
            "backup": backup,
            "schema validation": schema_validated,
            "rehearsal": rehearsal,
            "rollback test": rollback_tested,
            "full regression": full_regression,
        }
        gaps = [name for name, ok in checks.items() if not ok]
        return {
            "ready": not gaps,
            "gaps": gaps,
            "steps": [
                "freeze writes",
                "create verified backup",
                "migrate schema",
                "copy tenant batches",
                "verify counts and hashes",
                "switch connection",
                "run smoke tests",
                "retain rollback window",
            ],
        }


class IdentityGateway:
    """Verify reverse-proxy SSO claims through HMAC and role policy."""

    def __init__(self, key: bytes, now: Callable[[], int] | None = None) -> None:
        if len(key) < 16:
            raise ValueError("identity signing key must be at least 16 bytes")
        self._key = key
        self._now = now or (lambda: int(time.time()))

    def verify(
        self, raw_claims: str, signature: str, required_role: str | None = None
    ) -> dict[str, Any]:
        """Authenticate canonical claims and enforce expiration, tenant and role."""
        expected = hmac.new(self._key, raw_claims.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise PermissionError("invalid identity signature")
        claims = json.loads(raw_claims)
        if int(claims.get("exp", 0)) < self._now():
            raise PermissionError("identity is expired")
        if (
            not str(claims.get("sub", "")).strip()
            or not str(claims.get("tenant_id", "")).strip()
        ):
            raise PermissionError("identity and tenant are required")
        roles = claims.get("roles", [])
        if required_role and required_role not in roles:
            raise PermissionError("required role is missing")
        return {
            "subject": claims["sub"],
            "tenant_id": claims["tenant_id"],
            "roles": list(roles),
        }


class PolicyRouteSimulator:
    """Explain policy and routing decisions without executing a provider call."""

    def simulate(
        self,
        *,
        request: dict[str, Any],
        policy: dict[str, Any],
        routes: list[dict[str, Any]],
        minimum_quality: float,
    ) -> dict[str, Any]:
        """Evaluate budget, residency, tool policy, health, quality and cost."""
        cost = float(request.get("estimated_cost", 0))
        maximum = float(policy.get("max_cost", math.inf))
        region = str(request.get("region", ""))
        tool = str(request.get("tool", ""))
        path = []
        gates = [
            ("budget", cost <= maximum, f"estimated ${cost:.4f} <= ${maximum:.4f}"),
            (
                "region",
                region in policy.get("allowed_regions", []),
                f"region {region} allowed",
            ),
            ("tool", tool in policy.get("allowed_tools", []), f"tool {tool} allowed"),
        ]
        for gate, passed, detail in gates:
            path.append({"gate": gate, "passed": passed, "detail": detail})
            if not passed:
                return {
                    "allowed": False,
                    "reason": gate,
                    "selected_model": None,
                    "decision_path": path,
                    "rejected_routes": [],
                }
        eligible = []
        rejected = []
        for route in routes:
            reasons = []
            if not route.get("healthy", False):
                reasons.append("unhealthy")
            if float(route.get("quality", 0)) < minimum_quality:
                reasons.append("quality")
            if float(route.get("cost", math.inf)) > maximum:
                reasons.append("cost")
            if reasons:
                rejected.append({"model": route.get("model"), "reasons": reasons})
            else:
                eligible.append(route)
        rejected.sort(key=lambda x: str(x["model"]))
        eligible.sort(key=lambda x: (float(x["cost"]), str(x["model"])))
        if not eligible:
            return {
                "allowed": False,
                "reason": "no_eligible_route",
                "selected_model": None,
                "decision_path": path,
                "rejected_routes": rejected,
            }
        return {
            "allowed": True,
            "reason": "allowed",
            "selected_model": eligible[0]["model"],
            "decision_path": path,
            "rejected_routes": rejected,
        }
