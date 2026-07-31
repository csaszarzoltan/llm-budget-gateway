"""Evaluation, release-gating, tracing, batch, and audit capabilities."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class EvaluationResult:
    """Deterministic evaluation outcome for one candidate output."""

    passed: bool
    score: float
    checks: dict[str, bool]


class RuleEvaluator:
    """Evaluate exact, containment, forbidden-pattern, and length rules."""

    def evaluate(self, output: str, rules: Mapping[str, object]) -> EvaluationResult:
        """Evaluate output without an external model or network call."""
        if not isinstance(output, str) or not isinstance(rules, Mapping):
            raise TypeError("output and rules have invalid types")
        checks: dict[str, bool] = {}
        if "equals" in rules:
            checks["equals"] = output == str(rules["equals"])
        if "contains" in rules:
            checks["contains"] = all(str(x) in output for x in rules["contains"])  # type: ignore[union-attr]
        if "forbidden" in rules:
            checks["forbidden"] = not any(
                re.search(str(x), output, re.I) for x in rules["forbidden"]
            )  # type: ignore[union-attr]
        if "max_length" in rules:
            limit = rules["max_length"]
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
                raise ValueError("max_length must be non-negative")
            checks["max_length"] = len(output) <= limit
        if not checks:
            raise ValueError("at least one evaluation rule is required")
        score = sum(checks.values()) / len(checks)
        return EvaluationResult(all(checks.values()), score, checks)


class ReleaseGate:
    """Apply explicit quality, safety, and regression thresholds."""

    def decide(
        self,
        scores: Sequence[float],
        minimum: float,
        max_regression: float,
        baseline: float | None = None,
    ) -> dict[str, object]:
        """Return a deterministic release pass/fail decision."""
        if (
            not scores
            or not 0 <= minimum <= 1
            or max_regression < 0
            or any(not 0 <= x <= 1 for x in scores)
        ):
            raise ValueError("valid scores and thresholds required")
        mean = sum(scores) / len(scores)
        regression = max(0, (baseline - mean) if baseline is not None else 0)
        passed = mean >= minimum and regression <= max_regression
        return {
            "passed": passed,
            "mean": mean,
            "minimum": minimum,
            "regression": regression,
            "reason": "thresholds_met" if passed else "quality_gate_failed",
        }


class TraceContext:
    """Validate and propagate privacy-safe trace and session identifiers."""

    _id = re.compile(r"^[A-Za-z0-9_-]{8,128}$")

    def resolve(self, headers: Mapping[str, str]) -> dict[str, str]:
        """Resolve trace headers in documented priority order."""
        lower = {k.lower(): v for k, v in headers.items()}
        value = lower.get("x-gateway-trace-id") or lower.get("x-gateway-session-id")
        if value is None:
            value = next(
                (
                    v
                    for k, v in lower.items()
                    if k.startswith("x-") and k.endswith("-session-id")
                ),
                "",
            )
        if not self._id.fullmatch(value):
            raise ValueError("valid trace or session id is required")
        return {"trace_id": value, "session_id": value}


class BatchManifest:
    """Validate offline batch manifests and estimate discounted cost."""

    def build(
        self, requests: Sequence[Mapping[str, object]], discount: float = 0.5
    ) -> dict[str, object]:
        """Return a validated single-model batch manifest."""
        if not requests or not 0 <= discount <= 1:
            raise ValueError("requests and discount are invalid")
        ids = []
        models = set()
        total = 0.0
        for item in requests:
            cid = item.get("custom_id")
            model = item.get("model")
            cost = item.get("estimated_cost")
            if (
                not isinstance(cid, str)
                or not cid
                or cid in ids
                or not isinstance(model, str)
                or not model
            ):
                raise ValueError("unique custom_id and model required")
            if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
                raise ValueError("estimated_cost must be non-negative")
            ids.append(cid)
            models.add(model)
            total += float(cost)
        if len(models) != 1:
            raise ValueError("a batch must use exactly one model")
        return {
            "model": models.pop(),
            "count": len(ids),
            "custom_ids": ids,
            "estimated_cost": total * (1 - discount),
            "discount": discount,
        }


class AuditReport:
    """Create schema-versioned, redacted, integrity-protected audit reports."""

    _secret = re.compile(r"(?:sk|gw)_[A-Za-z0-9_-]{6,}")

    def create(
        self, findings: Sequence[Mapping[str, object]], generated_at: int
    ) -> dict[str, object]:
        """Create a stable audit-report.v1 document with SHA-256 integrity."""
        if generated_at < 0:
            raise ValueError("generated_at must be non-negative")
        safe = []
        for finding in findings:
            row = {
                k: v
                for k, v in finding.items()
                if k.lower() not in {"prompt", "authorization", "secret"}
            }
            text = json.dumps(row, sort_keys=True)
            row = json.loads(self._secret.sub("[redacted-secret]", text))
            safe.append(row)
        payload = {
            "schema": "audit-report.v1",
            "generated_at": generated_at,
            "findings": safe,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        payload["sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
        return payload

    def verify(self, report: Mapping[str, object]) -> bool:
        """Verify schema and report integrity."""
        try:
            body = {k: v for k, v in report.items() if k != "sha256"}
            canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
            return report.get("schema") == "audit-report.v1" and hashlib.sha256(
                canonical.encode()
            ).hexdigest() == report.get("sha256")
        except (TypeError, ValueError):
            return False


class EvaluationStore:
    """Tenant-isolated SQLite store for immutable evaluation runs."""

    def __init__(self, path: str, clock: Callable[[], int] | None = None) -> None:
        self.clock = clock or (lambda: int(time.time()))
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS eval_run(id TEXT PRIMARY KEY,tenant TEXT,name TEXT,score REAL,passed INTEGER,created INTEGER)"
        )
        self.db.commit()

    def record(
        self, tenant: str, name: str, result: EvaluationResult
    ) -> dict[str, object]:
        """Persist an evaluation result and return its record."""
        if not tenant or not name:
            raise ValueError("tenant and name required")
        rid = hashlib.sha256(
            f"{tenant}:{name}:{self.clock()}:{result.score}".encode()
        ).hexdigest()[:16]
        self.db.execute(
            "INSERT INTO eval_run VALUES(?,?,?,?,?,?)",
            (rid, tenant, name, result.score, int(result.passed), self.clock()),
        )
        self.db.commit()
        return {"id": rid, "score": result.score, "passed": result.passed}

    def list(self, tenant: str) -> list[dict[str, object]]:
        """List evaluation runs for one tenant only."""
        return [
            dict(x)
            for x in self.db.execute(
                "SELECT * FROM eval_run WHERE tenant=? ORDER BY created DESC", (tenant,)
            )
        ]
