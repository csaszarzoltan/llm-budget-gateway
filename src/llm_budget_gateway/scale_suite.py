"""Deterministic scale and multi-instance deployment controls."""

from __future__ import annotations

import math
import re
from hashlib import sha256
from typing import Any


def _num(value: Any, name: str, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ValueError(f"{name} must be finite and >= {minimum}")
    return result


class StorageTopology:
    """Determine whether a persistence topology supports the requested node count."""

    def assess(
        self, nodes: int, backend: str, transactional: bool
    ) -> dict[str, object]:
        count = int(_num(nodes, "nodes", 1))
        kind = backend.strip().lower()
        if kind not in {"sqlite", "postgres", "redis", "distributed"}:
            raise ValueError("unsupported backend")
        ready = count == 1 or (kind != "sqlite" and transactional)
        return {
            "ready": ready,
            "nodes": count,
            "backend": kind,
            "action": "proceed" if ready else "use a transactional shared backend",
        }


class ReplicationQuorum:
    """Calculate majority quorum and tolerated replica failures."""

    def calculate(self, replicas: int, available: int) -> dict[str, object]:
        total = int(_num(replicas, "replicas", 1))
        up = int(_num(available, "available"))
        if up > total:
            raise ValueError("available cannot exceed replicas")
        quorum = total // 2 + 1
        return {
            "quorum": quorum,
            "available": up,
            "writable": up >= quorum,
            "failure_tolerance": total - quorum,
        }


class PartitionPlanner:
    """Recommend deterministic tenant partitions from workload volume."""

    def plan(
        self, tenants: int, requests_per_second: float, max_rps_per_partition: float
    ) -> dict[str, object]:
        tenant_count = int(_num(tenants, "tenants", 1))
        rps = _num(requests_per_second, "requests_per_second")
        capacity = _num(max_rps_per_partition, "max_rps_per_partition", 1)
        partitions = max(1, math.ceil(rps / capacity))
        return {
            "partitions": partitions,
            "tenants_per_partition": math.ceil(tenant_count / partitions),
            "estimated_rps_per_partition": rps / partitions,
        }


class ConsistencyPolicy:
    """Select a supported consistency mode for a workload requirement."""

    def decide(self, workload: str, mode: str) -> dict[str, object]:
        allowed = {
            "budget": {"strong"},
            "key-lifecycle": {"strong"},
            "cache": {"eventual", "strong"},
            "analytics": {"eventual", "strong"},
        }
        if workload not in allowed or mode not in {"strong", "eventual"}:
            raise ValueError("unsupported policy")
        permitted = mode in allowed[workload]
        return {
            "permitted": permitted,
            "workload": workload,
            "mode": mode,
            "reason": "accepted"
            if permitted
            else "financial and identity controls require strong consistency",
        }


class FailoverPlanner:
    """Validate ordered failover regions and recovery targets."""

    def build(
        self, primary: str, candidates: list[str], health: dict[str, bool]
    ) -> dict[str, object]:
        if not primary or not candidates:
            raise ValueError("primary and candidates are required")
        ordered = [r for r in candidates if r != primary]
        if len(ordered) != len(set(ordered)):
            raise ValueError("candidate regions must be unique")
        selected = next((r for r in ordered if health.get(r) is True), None)
        return {
            "ready": selected is not None,
            "primary": primary,
            "selected": selected,
            "candidates": ordered,
        }


class MigrationReadiness:
    """Gate shared-store migration on backups, rehearsal, tests, and rollback."""

    REQUIRED = (
        "backup",
        "schema_validation",
        "rehearsal",
        "targeted_tests",
        "full_regression",
        "rollback",
    )

    def decide(self, checks: dict[str, bool]) -> dict[str, object]:
        if any(not isinstance(v, bool) for v in checks.values()):
            raise ValueError("checks must be booleans")
        missing = [x for x in self.REQUIRED if checks.get(x) is not True]
        return {
            "ready": not missing,
            "missing": missing,
            "required": list(self.REQUIRED),
        }


class ConnectionPoolPlanner:
    """Bound per-node database connections below provider capacity."""

    def plan(
        self,
        nodes: int,
        database_max_connections: int,
        reserved_connections: int,
        safety_ratio: float = 0.8,
    ) -> dict[str, object]:
        count = int(_num(nodes, "nodes", 1))
        maximum = int(_num(database_max_connections, "database_max_connections", 1))
        reserved = int(_num(reserved_connections, "reserved_connections"))
        ratio = _num(safety_ratio, "safety_ratio")
        if reserved >= maximum or ratio <= 0 or ratio > 1:
            raise ValueError("invalid connection capacity")
        usable = math.floor((maximum - reserved) * ratio)
        per_node = usable // count
        return {
            "ready": per_node >= 1,
            "per_node": per_node,
            "usable": usable,
            "reserved": reserved,
        }


class TenantShardAssignment:
    """Assign tenants to stable shards without retaining tenant plaintext in the key."""

    def assign(self, tenant_id: str, shard_count: int) -> dict[str, object]:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", tenant_id):
            raise ValueError("invalid tenant id")
        count = int(_num(shard_count, "shard_count", 1))
        digest = sha256(tenant_id.encode()).hexdigest()
        return {
            "shard": int(digest[:16], 16) % count,
            "tenant_fingerprint": digest,
            "shard_count": count,
        }


class ResidencyTopology:
    """Ensure tenant data stores stay inside explicitly allowed regions."""

    def evaluate(
        self,
        tenant_region: str,
        stores: list[dict[str, str]],
        allowed_pairs: list[list[str]] | None = None,
    ) -> dict[str, object]:
        pairs = {tuple(x) for x in (allowed_pairs or []) if len(x) == 2}
        violations = sorted(
            s.get("name", "unknown")
            for s in stores
            if s.get("region") != tenant_region
            and (tenant_region, s.get("region")) not in pairs
        )
        return {
            "compliant": not violations,
            "tenant_region": tenant_region,
            "violations": violations,
        }


class DisasterRecoveryObjective:
    """Evaluate backup and recovery performance against RPO and RTO targets."""

    def evaluate(
        self,
        target_rpo_minutes: float,
        target_rto_minutes: float,
        backup_age_minutes: float,
        restore_duration_minutes: float,
    ) -> dict[str, object]:
        trpo = _num(target_rpo_minutes, "target_rpo_minutes")
        trto = _num(target_rto_minutes, "target_rto_minutes")
        age = _num(backup_age_minutes, "backup_age_minutes")
        restore = _num(restore_duration_minutes, "restore_duration_minutes")
        failures = []
        if age > trpo:
            failures.append("rpo")
        if restore > trto:
            failures.append("rto")
        return {
            "ready": not failures,
            "failures": failures,
            "rpo_margin_minutes": trpo - age,
            "rto_margin_minutes": trto - restore,
        }
