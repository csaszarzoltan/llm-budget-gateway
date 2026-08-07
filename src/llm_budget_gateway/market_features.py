"""Market-driven controls for privacy, caching, alerts, routing, and analytics."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean, pstdev


@dataclass(frozen=True)
class RedactionResult:
    """PII redaction result safe to forward and audit."""

    text: str
    categories: tuple[str, ...]
    count: int


class PIIRedactor:
    """Redact common email, phone, and payment-card patterns locally."""

    _patterns = {
        "email": re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),
        "card": re.compile(r"(?<!\d)(?:\d[ -]*?){13,19}(?!\d)"),
        "phone": re.compile(r"(?<!\d)(?:\+?\d[\d .()-]{7,}\d)(?!\d)"),
    }

    def redact(self, text: str) -> RedactionResult:
        """Return text with detected PII replaced by category tokens."""
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        categories: list[str] = []
        total = 0
        redacted = text
        for category, pattern in self._patterns.items():
            redacted, count = pattern.subn(f"[REDACTED_{category.upper()}]", redacted)
            if count:
                categories.append(category)
                total += count
        return RedactionResult(redacted, tuple(categories), total)


class ExactResponseCache:
    """Tenant-isolated SQLite response cache with TTL and deterministic keys."""

    def __init__(self, path: str, clock: Callable[[], int] | None = None) -> None:
        self.clock = clock or (lambda: int(time.time()))
        parent = Path(path).parent
        if str(parent) and not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS response_cache("
            "tenant TEXT, cache_key TEXT, value TEXT, expires INTEGER, "
            "PRIMARY KEY(tenant, cache_key))"
        )
        self.db.commit()

    @staticmethod
    def key_for(payload: Mapping[str, object]) -> str:
        """Create a stable SHA-256 key for a JSON-compatible request payload."""
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def put(
        self, tenant: str, payload: Mapping[str, object], value: object, ttl: int
    ) -> str:
        """Cache a JSON-compatible value for a positive TTL and return its key."""
        if not tenant or ttl <= 0:
            raise ValueError("tenant and positive ttl are required")
        key = self.key_for(payload)
        self.db.execute(
            "INSERT OR REPLACE INTO response_cache VALUES(?,?,?,?)",
            (tenant, key, json.dumps(value), self.clock() + ttl),
        )
        self.db.commit()
        return key

    def get(self, tenant: str, payload: Mapping[str, object]) -> object | None:
        """Return an unexpired cached value, or None on miss/expiry."""
        key = self.key_for(payload)
        row = self.db.execute(
            "SELECT value,expires FROM response_cache WHERE tenant=? AND cache_key=?",
            (tenant, key),
        ).fetchone()
        if not row or row[1] <= self.clock():
            if row:
                self.db.execute(
                    "DELETE FROM response_cache WHERE tenant=? AND cache_key=?",
                    (tenant, key),
                )
                self.db.commit()
            return None
        return json.loads(row[0])


class SignedWebhook:
    """Build tamper-evident webhook envelopes without performing network I/O."""

    @staticmethod
    def build(
        secret: str, event: str, payload: Mapping[str, object], timestamp: int
    ) -> dict[str, object]:
        """Return an HMAC-SHA256 signed event envelope."""
        if not secret or not event or timestamp < 0:
            raise ValueError("secret, event, and valid timestamp are required")
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        material = f"{timestamp}.{event}.{body}".encode()
        signature = hmac.new(secret.encode(), material, hashlib.sha256).hexdigest()
        return {
            "event": event,
            "timestamp": timestamp,
            "payload": dict(payload),
            "signature": f"sha256={signature}",
        }

    @staticmethod
    def verify(secret: str, envelope: Mapping[str, object]) -> bool:
        """Verify an envelope signature using constant-time comparison."""
        try:
            expected = SignedWebhook.build(
                secret,
                str(envelope["event"]),
                envelope["payload"],  # type: ignore[arg-type]
                int(envelope["timestamp"]),
            )["signature"]
            return hmac.compare_digest(str(expected), str(envelope["signature"]))
        except (KeyError, TypeError, ValueError):
            return False


class UsageAnomalyDetector:
    """Detect cost spikes against a historical z-score and ratio threshold."""

    def detect(
        self, history: Sequence[float], current: float, z_limit: float = 3.0
    ) -> dict[str, object]:
        """Return explainable anomaly metrics for non-negative usage values."""
        if (
            len(history) < 2
            or current < 0
            or any(x < 0 for x in history)
            or z_limit <= 0
        ):
            raise ValueError(
                "two non-negative history values, current, and positive "
                "z_limit required"
            )
        mean = fmean(history)
        deviation = pstdev(history)
        z_score = (
            (current - mean) / deviation
            if deviation
            else (float("inf") if current > mean else 0.0)
        )
        ratio = current / mean if mean else (float("inf") if current else 1.0)
        anomalous = current > mean and (z_score >= z_limit or ratio >= 2.0)
        return {
            "anomaly": anomalous,
            "mean": mean,
            "z_score": z_score,
            "ratio": ratio,
            "explanation": (
                f"Current {current:.4f}; baseline {mean:.4f}; ratio {ratio:.2f}x."
            ),
        }


class CostAwareRouter:
    """Choose the cheapest eligible healthy model while preserving quality."""

    def choose(
        self,
        candidates: Sequence[Mapping[str, object]],
        min_quality: float = 0.0,
        max_latency_ms: int | None = None,
    ) -> dict[str, object]:
        """Return the lowest-cost candidate satisfying health and constraints."""
        if not candidates or not 0 <= min_quality <= 1:
            raise ValueError("candidates and min_quality between 0 and 1 are required")
        if max_latency_ms is not None and max_latency_ms <= 0:
            raise ValueError("max_latency_ms must be positive")
        eligible = []
        for item in candidates:
            try:
                if (
                    bool(item.get("healthy", True))
                    and float(item["quality"]) >= min_quality
                    and float(item["cost"]) >= 0
                    and (
                        max_latency_ms is None
                        or int(item["latency_ms"]) <= max_latency_ms
                    )
                ):
                    eligible.append(item)
            except (KeyError, TypeError, ValueError):
                continue
        if not eligible:
            raise ValueError("no eligible model")
        selected = min(
            eligible,
            key=lambda item: (
                float(item["cost"]),
                -float(item["quality"]),
                int(item["latency_ms"]),
            ),
        )
        return {
            "model": selected["model"],
            "cost": float(selected["cost"]),
            "quality": float(selected["quality"]),
            "latency_ms": int(selected["latency_ms"]),
            "eligible_count": len(eligible),
            "reason": (
                "lowest cost among healthy candidates meeting quality and "
                "latency constraints"
            ),
        }
