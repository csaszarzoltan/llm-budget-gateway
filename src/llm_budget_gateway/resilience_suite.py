"""Operational resilience controls for concurrency, incidents, queues, maintenance, and configuration."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass


class AdaptiveConcurrency:
    """Adjust a concurrency limit using latency and error-rate guardrails."""

    def tune(
        self,
        current: int,
        p95_ms: float,
        error_rate: float,
        target_ms: float,
        min_limit: int = 1,
        max_limit: int = 100,
    ) -> dict[str, object]:
        """Return a bounded additive-increase, multiplicative-decrease decision."""
        if (
            any(isinstance(x, bool) for x in (current, min_limit, max_limit))
            or not min_limit <= current <= max_limit
            or target_ms <= 0
            or p95_ms < 0
            or not 0 <= error_rate <= 1
        ):
            raise ValueError("invalid concurrency telemetry or bounds")
        if error_rate >= 0.05 or p95_ms > target_ms * 1.25:
            new = max(min_limit, current // 2)
            reason = "decrease"
        elif error_rate < 0.01 and p95_ms <= target_ms:
            new = min(max_limit, current + 1)
            reason = "increase"
        else:
            new = current
            reason = "hold"
        return {"previous": current, "limit": new, "reason": reason}


class DeadLetterStore:
    """Tenant-isolated durable dead-letter queue with idempotent replay."""

    def __init__(self, path: str, clock: Callable[[], int] | None = None) -> None:
        self.clock = clock or (lambda: int(time.time()))
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS dead_letter(id TEXT PRIMARY KEY,tenant TEXT,payload TEXT,error TEXT,state TEXT,created INTEGER,replayed INTEGER)"
        )
        self.db.commit()

    def add(
        self, tenant: str, payload: Mapping[str, object], error: str
    ) -> dict[str, object]:
        """Persist a failed operation after removing sensitive fields."""
        if not tenant or not error:
            raise ValueError("tenant and error required")
        safe = {
            k: v
            for k, v in payload.items()
            if k.lower() not in {"prompt", "secret", "authorization"}
        }
        raw = json.dumps(safe, sort_keys=True)
        rid = hashlib.sha256(f"{tenant}:{raw}:{error}".encode()).hexdigest()[:20]
        self.db.execute(
            "INSERT OR IGNORE INTO dead_letter VALUES(?,?,?,?,'pending',?,NULL)",
            (rid, tenant, raw, error, self.clock()),
        )
        self.db.commit()
        return {"id": rid, "state": "pending"}

    def replay(self, tenant: str, rid: str) -> dict[str, object]:
        """Mark one pending item replayed exactly once and return its payload."""
        row = self.db.execute(
            "SELECT payload,state FROM dead_letter WHERE tenant=? AND id=?",
            (tenant, rid),
        ).fetchone()
        if not row:
            raise KeyError(rid)
        if row["state"] == "replayed":
            return {"id": rid, "state": "replayed", "duplicate": True}
        self.db.execute(
            "UPDATE dead_letter SET state='replayed',replayed=? WHERE tenant=? AND id=?",
            (self.clock(), tenant, rid),
        )
        self.db.commit()
        return {
            "id": rid,
            "state": "replayed",
            "duplicate": False,
            "payload": json.loads(row["payload"]),
        }


class MaintenanceWindow:
    """Determine whether a UTC weekly maintenance window is active."""

    def evaluate(
        self, weekday: int, start_minute: int, duration_minutes: int, now_epoch: int
    ) -> dict[str, object]:
        """Return active state and seconds until the next window."""
        if (
            not 0 <= weekday <= 6
            or not 0 <= start_minute < 1440
            or not 1 <= duration_minutes <= 1440
            or now_epoch < 0
        ):
            raise ValueError("invalid maintenance window")
        tm = time.gmtime(now_epoch)
        week_start = now_epoch - (
            (tm.tm_wday * 86400) + (tm.tm_hour * 3600) + (tm.tm_min * 60) + tm.tm_sec
        )
        start = week_start + weekday * 86400 + start_minute * 60
        if start + duration_minutes * 60 <= now_epoch:
            start += 7 * 86400
        active = start <= now_epoch < start + duration_minutes * 60
        return {
            "active": active,
            "seconds_until_start": 0 if active else max(0, start - now_epoch),
            "start_epoch": start,
        }


class ConfigDoctor:
    """Validate gateway deployment configuration and return actionable findings."""

    def diagnose(self, config: Mapping[str, object]) -> dict[str, object]:
        """Check authentication, storage, timeouts, keys, and production safety."""
        if not isinstance(config, Mapping):
            raise TypeError("config must be an object")
        findings = []
        if not config.get("api_key"):
            findings.append("api_key_missing")
        if config.get("environment") == "production" and str(
            config.get("database_url", "")
        ).startswith("sqlite"):
            findings.append("shared_database_required")
        timeout = config.get("provider_timeout", 0)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout <= 0
        ):
            findings.append("provider_timeout_invalid")
        if config.get("webhook_secret") in {None, "", "development-only"}:
            findings.append("webhook_secret_unsafe")
        return {
            "valid": not findings,
            "findings": findings,
            "remediation": [x.replace("_", " ") for x in findings],
        }


@dataclass(frozen=True)
class IncidentEvent:
    """One normalized incident timeline event."""

    timestamp: int
    kind: str
    detail: str


class IncidentTimeline:
    """Normalize and summarize incident events without prompt content."""

    def build(self, events: Sequence[Mapping[str, object]]) -> dict[str, object]:
        """Return ordered events, duration, severity, and kind counts."""
        if not events:
            raise ValueError("events required")
        normalized = []
        for item in events:
            ts = item.get("timestamp")
            kind = item.get("kind")
            detail = item.get("detail", "")
            if (
                isinstance(ts, bool)
                or not isinstance(ts, int)
                or ts < 0
                or not isinstance(kind, str)
                or not kind
            ):
                raise ValueError("invalid incident event")
            normalized.append(IncidentEvent(ts, kind, str(detail)[:500]))
        normalized.sort(key=lambda x: x.timestamp)
        counts = {}
        for event in normalized:
            counts[event.kind] = counts.get(event.kind, 0) + 1
        severity = (
            "critical"
            if any(x.kind in {"data_loss", "security"} for x in normalized)
            else "high"
            if any(x.kind in {"outage", "budget"} for x in normalized)
            else "medium"
        )
        return {
            "events": [x.__dict__ for x in normalized],
            "duration_seconds": normalized[-1].timestamp - normalized[0].timestamp,
            "counts": counts,
            "severity": severity,
        }
