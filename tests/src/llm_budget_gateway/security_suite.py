"""Security posture, secret scanning, replay protection, compliance, and change risk."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class SecretScanResult:
    """Redacted text and safely categorized secret findings."""

    text: str
    categories: tuple[str, ...]
    count: int


class SecretScanner:
    """Detect and redact common provider keys, bearer tokens, and private keys."""

    _patterns = {
        "private_key": re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
        ),
        "bearer": re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{12,}=*"),
        "provider_key": re.compile(r"\b(?:sk|gw|gsk|pk)_[A-Za-z0-9_-]{8,}\b"),
        "assignment": re.compile(
            r"(?i)\b(?:api[_-]?key|token|password|secret)\s*[:=]\s*['\"]?[^\s'\"]{8,}['\"]?"
        ),
    }

    def scan(self, text: str) -> SecretScanResult:
        """Return locally redacted text without exposing detected values."""
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        redacted, categories, count = text, [], 0
        for category, pattern in self._patterns.items():
            redacted, found = pattern.subn(f"[REDACTED_{category.upper()}]", redacted)
            if found:
                categories.append(category)
                count += found
        return SecretScanResult(redacted, tuple(categories), count)


class ReplayProtector:
    """Durably reserve webhook delivery IDs within a tenant-scoped TTL window."""

    def __init__(self, path: str, clock: Callable[[], int] | None = None) -> None:
        self.clock = clock or (lambda: int(time.time()))
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS replay(tenant TEXT,event_id TEXT,expires INTEGER,PRIMARY KEY(tenant,event_id))"
        )

    def reserve(self, tenant: str, event_id: str, ttl: int) -> dict[str, object]:
        """Atomically reserve a delivery ID and report duplicates without side effects."""
        if not tenant or not event_id or ttl <= 0:
            raise ValueError("tenant, event_id, and positive ttl are required")
        now = self.clock()
        self.db.execute("DELETE FROM replay WHERE expires<=?", (now,))
        try:
            self.db.execute(
                "INSERT INTO replay VALUES(?,?,?)", (tenant, event_id, now + ttl)
            )
            return {"accepted": True, "duplicate": False, "expires": now + ttl}
        except sqlite3.IntegrityError:
            row = self.db.execute(
                "SELECT expires FROM replay WHERE tenant=? AND event_id=?",
                (tenant, event_id),
            ).fetchone()
            return {"accepted": False, "duplicate": True, "expires": int(row[0])}


class ProviderCompliancePolicy:
    """Fail closed when provider certifications or data policies are incomplete."""

    def evaluate(
        self, provider: Mapping[str, object], requirements: Mapping[str, object]
    ) -> dict[str, object]:
        """Return an explainable allow or deny decision for provider routing."""
        name = provider.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("provider name is required")
        certifications = {str(x).lower() for x in provider.get("certifications", [])}
        missing = []
        for cert in requirements.get("certifications", []):
            if str(cert).lower() not in certifications:
                missing.append(f"certification:{cert}")
        for field in ("no_training", "no_logging", "gdpr"):
            if requirements.get(field) is True and provider.get(field) is not True:
                missing.append(field)
        regions = {str(x).lower() for x in provider.get("regions", [])}
        allowed_regions = {
            str(x).lower() for x in requirements.get("allowed_regions", [])
        }
        if allowed_regions and not regions.intersection(allowed_regions):
            missing.append("allowed_region")
        return {
            "provider": name,
            "allowed": not missing,
            "missing": missing,
            "reason": "requirements_met"
            if not missing
            else "provider_compliance_denied",
        }


class ChangeRiskAssessor:
    """Score sensitive gateway changes and determine required approval depth."""

    _weights = {
        "auth": 35,
        "routing": 20,
        "pricing": 15,
        "retention": 25,
        "provider": 20,
        "ui": 5,
        "docs": 1,
    }

    def assess(
        self, changes: Sequence[str], production: bool = True
    ) -> dict[str, object]:
        """Return bounded risk, severity, and approval requirements."""
        if not changes or any(not isinstance(x, str) or not x for x in changes):
            raise ValueError("at least one valid change category is required")
        unknown = sorted(set(changes) - self._weights.keys())
        if unknown:
            raise ValueError(f"unknown change categories: {', '.join(unknown)}")
        score = min(
            100, sum(self._weights[x] for x in set(changes)) + (10 if production else 0)
        )
        severity = (
            "critical"
            if score >= 70
            else "high"
            if score >= 45
            else "medium"
            if score >= 20
            else "low"
        )
        return {
            "score": score,
            "severity": severity,
            "approvals_required": 2 if score >= 45 else 1,
            "can_auto_apply": score < 20 and not production,
        }


class SecurityPosture:
    """Evaluate a gateway configuration against high-value security controls."""

    _controls = (
        "auth_configured",
        "secret_scanning",
        "replay_protection",
        "fail_closed_compliance",
        "audit_integrity",
        "dependency_pins",
    )

    def evaluate(self, config: Mapping[str, object]) -> dict[str, object]:
        """Return score, grade, missing controls, and remediation messages."""
        if not isinstance(config, Mapping):
            raise TypeError("config must be an object")
        passed = [name for name in self._controls if config.get(name) is True]
        missing = [name for name in self._controls if name not in passed]
        score = round(100 * len(passed) / len(self._controls))
        grade = (
            "A"
            if score >= 90
            else "B"
            if score >= 75
            else "C"
            if score >= 60
            else "D"
            if score >= 40
            else "F"
        )
        return {
            "score": score,
            "grade": grade,
            "passed": passed,
            "missing": missing,
            "remediation": [f"Enable {item.replace('_', ' ')}." for item in missing],
        }


def integrity_digest(value: Mapping[str, object]) -> str:
    """Return a stable SHA-256 digest for a JSON-compatible security record."""
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
