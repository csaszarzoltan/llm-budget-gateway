"""Safe release recovery and evidence-constrained optimization controls."""

from __future__ import annotations

import hashlib
import math
import re
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class BackupArtifact:
    """Verified local SQLite backup bound to a release identifier."""

    release_id: str
    path: Path
    sha256: str
    size_bytes: int


class ReleaseRecoveryService:
    """Create verified SQLite backups and fail-closed rollout decisions."""

    def __init__(self, backup_root: Path) -> None:
        """Initialize a dedicated backup root without creating it eagerly."""
        self.backup_root = backup_root

    def create_backup(self, source: Path, *, release_id: str) -> BackupArtifact:
        """Create a consistent SQLite backup and return its integrity evidence."""
        if not _SAFE_ID.fullmatch(release_id):
            raise ValueError("release_id must be a safe identifier")
        if not source.is_file():
            raise FileNotFoundError(source)
        self.backup_root.mkdir(parents=True, exist_ok=True)
        target = self.backup_root / f"{release_id}-{source.name}.backup"
        src = sqlite3.connect(source)
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        return BackupArtifact(
            release_id, target, _sha256(target), target.stat().st_size
        )

    def verify_backup(self, artifact: BackupArtifact) -> bool:
        """Verify file size, SHA-256, and SQLite integrity check."""
        if (
            not artifact.path.is_file()
            or artifact.path.stat().st_size != artifact.size_bytes
            or _sha256(artifact.path) != artifact.sha256
        ):
            return False
        try:
            connection = sqlite3.connect(artifact.path)
            result = connection.execute("PRAGMA integrity_check").fetchone()
            connection.close()
            return bool(result and result[0] == "ok")
        except sqlite3.DatabaseError:
            return False

    def restore(self, artifact: BackupArtifact, target: Path) -> None:
        """Atomically restore a verified backup to the target path."""
        if not self.verify_backup(artifact):
            raise ValueError("backup verification failed")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".restore")
        shutil.copy2(artifact.path, temporary)
        temporary.replace(target)

    def plan_rollout(
        self,
        *,
        provenance_verified: bool,
        backup_verified: bool,
        migration_ready: bool,
        regression_passed: bool,
        canary_percent: int,
    ) -> dict[str, Any]:
        """Require every safety gate before a bounded canary rollout."""
        if not 1 <= canary_percent <= 50:
            raise ValueError("canary_percent must be between 1 and 50")
        checks = {
            "provenance": provenance_verified,
            "backup": backup_verified,
            "migration": migration_ready,
            "regression": regression_passed,
        }
        gaps = [name for name, ok in checks.items() if not ok]
        return {
            "allowed": not gaps,
            "gaps": gaps,
            "canary_percent": canary_percent,
            "steps": [
                "verify artifact",
                "freeze schema",
                "create backup",
                f"route {canary_percent}% canary traffic",
                "monitor guardrails",
                "promote or rollback",
            ],
        }

    def canary_decision(
        self,
        *,
        error_rate: float,
        max_error_rate: float,
        p95_latency_ms: float,
        max_p95_latency_ms: float,
        quality: float,
        minimum_quality: float,
    ) -> dict[str, Any]:
        """Promote only while error, latency, and quality guardrails all pass."""
        values = (
            error_rate,
            max_error_rate,
            p95_latency_ms,
            max_p95_latency_ms,
            quality,
            minimum_quality,
        )
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("canary metrics must be finite and non-negative")
        failures = []
        if error_rate > max_error_rate:
            failures.append("error_rate")
        if p95_latency_ms > max_p95_latency_ms:
            failures.append("p95_latency")
        if quality < minimum_quality:
            failures.append("quality")
        return {
            "action": "rollback" if failures else "promote",
            "failed_guardrails": failures,
        }


@dataclass(frozen=True)
class AutopilotCandidate:
    """Measured route candidate used by the optimization recommender."""

    name: str
    cost_usd: float
    latency_ms: float
    quality: float
    success_rate: float


class OutcomeAutopilot:
    """Recommend bounded cost improvements without applying changes automatically."""

    def recommend(
        self,
        *,
        baseline: AutopilotCandidate,
        candidates: list[AutopilotCandidate],
        minimum_quality: float,
        minimum_success_rate: float,
        maximum_latency_ms: float,
    ) -> dict[str, Any]:
        """Choose the cheapest eligible improvement and require human approval."""
        if not candidates:
            raise ValueError("at least one candidate is required")
        all_candidates = [baseline, *candidates]
        for item in all_candidates:
            values = (item.cost_usd, item.latency_ms, item.quality, item.success_rate)
            if (
                not item.name.strip()
                or any(not math.isfinite(v) or v < 0 for v in values)
                or not 0 <= item.quality <= 1
                or not 0 <= item.success_rate <= 1
            ):
                raise ValueError("candidate evidence is invalid")
        eligible = [
            item
            for item in candidates
            if item.quality >= minimum_quality
            and item.success_rate >= minimum_success_rate
            and item.latency_ms <= maximum_latency_ms
            and item.cost_usd < baseline.cost_usd
        ]
        if not eligible:
            return {
                "action": "hold",
                "candidate": None,
                "requires_approval": False,
                "reason": "No measured candidate improves cost within quality, success, and latency guardrails.",
            }
        selected = min(
            eligible,
            key=lambda item: (item.cost_usd, -item.quality, item.latency_ms, item.name),
        )
        savings = (
            round((baseline.cost_usd - selected.cost_usd) * 100 / baseline.cost_usd, 2)
            if baseline.cost_usd
            else 0.0
        )
        return {
            "action": "recommend",
            "candidate": selected.name,
            "estimated_savings_percent": savings,
            "requires_approval": True,
            "guardrails": {
                "quality": selected.quality,
                "success_rate": selected.success_rate,
                "latency_ms": selected.latency_ms,
            },
            "rollback": "Restore the prior route version if any monitored guardrail regresses.",
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
