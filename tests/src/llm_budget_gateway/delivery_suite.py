"""Deterministic delivery and deployment controls for the LLM gateway."""

from __future__ import annotations

import math
import re
from hashlib import sha256
from typing import Any


def _number(value: Any, name: str, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ValueError(f"{name} must be finite and >= {minimum}")
    return result


class EnvironmentReadiness:
    """Check required environment names without exposing values."""

    def evaluate(self, required: list[str], configured: list[str]) -> dict[str, object]:
        req = sorted({x.strip() for x in required if isinstance(x, str) and x.strip()})
        have = {x.strip() for x in configured if isinstance(x, str) and x.strip()}
        if not req:
            raise ValueError("required must not be empty")
        missing = [x for x in req if x not in have]
        return {"ready": not missing, "missing": missing, "configured_count": len(have)}


class ConfigurationDrift:
    """Compare redacted configuration fingerprints across environments."""

    def compare(
        self,
        expected: dict[str, Any],
        actual: dict[str, Any],
        ignored: list[str] | None = None,
    ) -> dict[str, object]:
        ignored_set = set(ignored or [])
        keys = sorted((set(expected) | set(actual)) - ignored_set)
        changed = [k for k in keys if expected.get(k) != actual.get(k)]
        digest = sha256("\n".join(changed).encode()).hexdigest()
        return {"drifted": bool(changed), "changed_fields": changed, "sha256": digest}


class CapacityPlanner:
    """Estimate request and token headroom from peak demand."""

    def plan(
        self,
        rpm_limit: int,
        tpm_limit: int,
        peak_rpm: int,
        peak_tpm: int,
        reserve_ratio: float = 0.2,
    ) -> dict[str, object]:
        values = [
            _number(v, n)
            for v, n in (
                (rpm_limit, "rpm_limit"),
                (tpm_limit, "tpm_limit"),
                (peak_rpm, "peak_rpm"),
                (peak_tpm, "peak_tpm"),
            )
        ]
        reserve = _number(reserve_ratio, "reserve_ratio")
        if reserve > 1:
            raise ValueError("reserve_ratio must be <= 1")
        rpm, tpm, pr, pt = values
        required_rpm, required_tpm = pr * (1 + reserve), pt * (1 + reserve)
        return {
            "ready": rpm >= required_rpm and tpm >= required_tpm,
            "rpm_headroom": rpm - pr,
            "tpm_headroom": tpm - pt,
            "required_rpm": required_rpm,
            "required_tpm": required_tpm,
        }


class HealthDependencyPolicy:
    """Aggregate required and optional dependency health."""

    def evaluate(self, dependencies: list[dict[str, Any]]) -> dict[str, object]:
        if not dependencies:
            raise ValueError("dependencies must not be empty")
        blocking, degraded = [], []
        for dep in dependencies:
            name, status = dep.get("name"), dep.get("status")
            if not isinstance(name, str) or status not in {
                "healthy",
                "degraded",
                "down",
            }:
                raise ValueError("invalid dependency")
            if dep.get("required", True) and status == "down":
                blocking.append(name)
            elif status != "healthy":
                degraded.append(name)
        state = "down" if blocking else "degraded" if degraded else "healthy"
        return {
            "state": state,
            "blocking": sorted(blocking),
            "degraded": sorted(degraded),
        }


class RolloutPlanner:
    """Validate monotonic canary stages and calculate observation windows."""

    def build(self, stages: list[int], observation_minutes: int) -> dict[str, object]:
        if (
            not stages
            or stages[-1] != 100
            or any(isinstance(x, bool) or not isinstance(x, int) for x in stages)
        ):
            raise ValueError("stages must be integers ending at 100")
        if any(a >= b or a <= 0 for a, b in zip(stages, stages[1:], strict=False)):
            raise ValueError("stages must be strictly increasing")
        minutes = int(_number(observation_minutes, "observation_minutes", 1))
        return {
            "stages": stages,
            "total_observation_minutes": minutes * len(stages),
            "rollback_between_stages": True,
        }


class RollbackDecision:
    """Trigger rollback when quality, errors, or latency exceed guardrails."""

    def decide(
        self,
        quality_drop: float,
        error_rate: float,
        latency_increase: float,
        max_quality_drop: float,
        max_error_rate: float,
        max_latency_increase: float,
    ) -> dict[str, object]:
        vals = {k: _number(v, k) for k, v in locals().items() if k != "self"}
        reasons = [
            name
            for name, limit in (
                ("quality_drop", "max_quality_drop"),
                ("error_rate", "max_error_rate"),
                ("latency_increase", "max_latency_increase"),
            )
            if vals[name] > vals[limit]
        ]
        return {"rollback": bool(reasons), "reasons": reasons}


class ObservabilityCoverage:
    """Measure required logs, metrics and traces before production release."""

    def assess(self, required: list[str], instrumented: list[str]) -> dict[str, object]:
        req = sorted(set(required))
        have = set(instrumented)
        if not req:
            raise ValueError("required signals must not be empty")
        missing = [x for x in req if x not in have]
        return {
            "coverage": (len(req) - len(missing)) / len(req),
            "missing": missing,
            "ready": not missing,
        }


class AlertRouteValidator:
    """Validate severity routes and signed-delivery requirements."""

    def validate(self, routes: list[dict[str, Any]]) -> dict[str, object]:
        findings = []
        for i, route in enumerate(routes):
            if route.get("severity") not in {"critical", "high", "medium", "low"}:
                findings.append(f"route[{i}]: invalid severity")
            if route.get("channel") not in {"webhook", "email", "pager"}:
                findings.append(f"route[{i}]: invalid channel")
            if route.get("channel") == "webhook" and route.get("signed") is not True:
                findings.append(f"route[{i}]: webhook must be signed")
        if not routes:
            findings.append("at least one route is required")
        return {"valid": not findings, "findings": findings}


class RunbookCoverage:
    """Ensure every critical failure mode has an owned recovery runbook."""

    def assess(
        self, failure_modes: list[str], runbooks: list[dict[str, Any]]
    ) -> dict[str, object]:
        modes = sorted(set(failure_modes))
        covered = {
            r.get("failure_mode") for r in runbooks if r.get("owner") and r.get("steps")
        }
        missing = [m for m in modes if m not in covered]
        return {
            "ready": bool(modes) and not missing,
            "missing": missing,
            "coverage": 0 if not modes else (len(modes) - len(missing)) / len(modes),
        }


class ReleaseManifest:
    """Create a deterministic integrity manifest for release artifacts."""

    def build(self, version: str, artifacts: dict[str, str]) -> dict[str, object]:
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise ValueError("version must be SemVer")
        if not artifacts:
            raise ValueError("artifacts must not be empty")
        normalized = {k: artifacts[k] for k in sorted(artifacts)}
        payload = version + "\n" + "\n".join(f"{k}:{v}" for k, v in normalized.items())
        return {
            "version": version,
            "artifacts": normalized,
            "sha256": sha256(payload.encode()).hexdigest(),
        }
