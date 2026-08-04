"""Production operations features for prompts, retries, SLOs, quotas, and models."""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryDecision:
    """A bounded retry decision with an optional delay in milliseconds."""

    retry: bool
    delay_ms: int
    reason: str


class RetryPolicy:
    """Prevent retry storms with attempt, elapsed-time, and delay ceilings."""

    def __init__(
        self,
        max_attempts: int = 3,
        max_elapsed_ms: int = 30_000,
        max_delay_ms: int = 5_000,
    ) -> None:
        if max_attempts < 1 or max_elapsed_ms < 1 or max_delay_ms < 1:
            raise ValueError("retry limits must be positive")
        self.max_attempts = max_attempts
        self.max_elapsed_ms = max_elapsed_ms
        self.max_delay_ms = max_delay_ms

    def decide(
        self,
        attempt: int,
        elapsed_ms: int,
        status_code: int | None,
        retry_after_ms: int | None = None,
        seed: int = 0,
    ) -> RetryDecision:
        """Return a deterministic full-jitter retry decision for transient errors."""
        if (
            attempt < 1
            or elapsed_ms < 0
            or retry_after_ms is not None
            and retry_after_ms < 0
        ):
            raise ValueError("attempt, elapsed time, and retry-after must be valid")
        if attempt >= self.max_attempts:
            return RetryDecision(False, 0, "attempt_limit")
        if elapsed_ms >= self.max_elapsed_ms:
            return RetryDecision(False, 0, "elapsed_limit")
        if status_code not in {408, 409, 425, 429, 500, 502, 503, 504}:
            return RetryDecision(False, 0, "non_retryable")
        ceiling = min(
            self.max_delay_ms,
            retry_after_ms or 250 * 2 ** (attempt - 1),
            self.max_elapsed_ms - elapsed_ms,
        )
        delay = random.Random(seed).randint(0, max(0, ceiling))
        return RetryDecision(True, delay, "transient")


class QuotaDiagnostic:
    """Classify provider failures into actionable quota and throttling categories."""

    def classify(
        self, status_code: int, code: str | None, message: str | None
    ) -> dict[str, str]:
        """Return a stable category, action, and operator-facing explanation."""
        if not isinstance(status_code, int) or isinstance(status_code, bool):
            raise TypeError("status_code must be an integer")
        marker = f"{code or ''} {message or ''}".lower()
        if status_code == 429 and any(
            x in marker for x in ("insufficient_quota", "billing", "credit")
        ):
            return {
                "category": "financial_quota",
                "action": "check_billing",
                "explanation": "The account lacks usable provider credit or quota.",
            }
        if status_code == 429 and any(x in marker for x in ("token", "tpm")):
            return {
                "category": "token_rate_limit",
                "action": "reduce_tokens_or_backoff",
                "explanation": "The token throughput limit was reached.",
            }
        if status_code == 429:
            return {
                "category": "request_rate_limit",
                "action": "backoff",
                "explanation": "The request-rate limit was reached.",
            }
        if status_code in {500, 502, 503, 504}:
            return {
                "category": "provider_availability",
                "action": "retry_or_fallback",
                "explanation": "The provider is temporarily unavailable.",
            }
        return {
            "category": "request_error",
            "action": "do_not_retry",
            "explanation": "The request should be corrected before retrying.",
        }


class ModelCatalog:
    """Expose validated pricing, context, capability, and residency metadata."""

    def normalize(
        self, models: Sequence[Mapping[str, object]]
    ) -> list[dict[str, object]]:
        """Validate, normalize, and sort model catalog entries by identifier."""
        output: list[dict[str, object]] = []
        seen: set[str] = set()
        for raw in models:
            model_id = raw.get("id")
            if (
                not isinstance(model_id, str)
                or not model_id.strip()
                or model_id in seen
            ):
                raise ValueError("model ids must be unique non-empty strings")
            input_price = self._non_negative(
                raw.get("input_cost_per_million", 0), "input price"
            )
            output_price = self._non_negative(
                raw.get("output_cost_per_million", 0), "output price"
            )
            context = raw.get("context_window", 0)
            if isinstance(context, bool) or not isinstance(context, int) or context < 1:
                raise ValueError("context_window must be a positive integer")
            capabilities = sorted(
                {str(x) for x in raw.get("capabilities", []) if str(x)}
            )
            regions = sorted({str(x) for x in raw.get("regions", []) if str(x)})
            output.append(
                {
                    "id": model_id,
                    "input_cost_per_million": input_price,
                    "output_cost_per_million": output_price,
                    "context_window": context,
                    "capabilities": capabilities,
                    "regions": regions,
                }
            )
            seen.add(model_id)
        return sorted(output, key=lambda x: str(x["id"]))

    @staticmethod
    def _non_negative(value: object, label: str) -> float:
        if isinstance(value, bool):
            raise ValueError(f"{label} must be non-negative")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} must be non-negative") from exc
        if number < 0:
            raise ValueError(f"{label} must be non-negative")
        return number


class SLOMonitor:
    """Calculate availability and error-budget burn for operational windows."""

    def evaluate(
        self, total: int, failed: int, target: float = 0.99
    ) -> dict[str, object]:
        """Return availability, remaining error budget, and burn state."""
        if (
            isinstance(total, bool)
            or isinstance(failed, bool)
            or total <= 0
            or failed < 0
            or failed > total
            or not 0 < target < 1
        ):
            raise ValueError(
                "valid request counts and target between zero and one required"
            )
        availability = (total - failed) / total
        allowed_failures = total * (1 - target)
        burn_rate = failed / allowed_failures if allowed_failures else float("inf")
        return {
            "availability": availability,
            "target": target,
            "burn_rate": burn_rate,
            "state": "critical"
            if burn_rate >= 2
            else "warning"
            if burn_rate >= 1
            else "healthy",
            "remaining_failures": max(0, allowed_failures - failed),
        }


class PromptRegistry:
    """Tenant-isolated immutable prompt versions with deterministic A/B assignment."""

    def __init__(self, path: str, clock: Callable[[], int] | None = None) -> None:
        self.clock = clock or (lambda: int(time.time()))
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS prompt_version(tenant TEXT,name TEXT,version INTEGER,template TEXT,metadata TEXT,created INTEGER,PRIMARY KEY(tenant,name,version))"
        )
        self.db.commit()

    def create(
        self,
        tenant: str,
        name: str,
        template: str,
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Create the next immutable prompt version and return its public record."""
        if not tenant or not name.strip() or not template.strip():
            raise ValueError("tenant, name, and template are required")
        row = self.db.execute(
            "SELECT COALESCE(MAX(version),0) FROM prompt_version WHERE tenant=? AND name=?",
            (tenant, name),
        ).fetchone()
        version = int(row[0]) + 1
        safe_metadata = {
            k: v
            for k, v in (metadata or {}).items()
            if k.lower() not in {"secret", "authorization"}
        }
        self.db.execute(
            "INSERT INTO prompt_version VALUES(?,?,?,?,?,?)",
            (
                tenant,
                name,
                version,
                template,
                json.dumps(safe_metadata, sort_keys=True),
                self.clock(),
            ),
        )
        self.db.commit()
        return {
            "tenant": tenant,
            "name": name,
            "version": version,
            "template": template,
            "metadata": safe_metadata,
        }

    def list(self, tenant: str, name: str) -> list[dict[str, object]]:
        """List prompt versions newest first for one tenant and prompt name."""
        return [
            {**dict(row), "metadata": json.loads(row["metadata"])}
            for row in self.db.execute(
                "SELECT name,version,template,metadata,created FROM prompt_version WHERE tenant=? AND name=? ORDER BY version DESC",
                (tenant, name),
            )
        ]

    def assign(
        self, tenant: str, name: str, subject: str, versions: Sequence[int]
    ) -> dict[str, object]:
        """Assign a subject deterministically to one existing prompt version."""
        if not subject or not versions:
            raise ValueError("subject and versions are required")
        clean = sorted(set(versions))
        available = {
            int(row[0])
            for row in self.db.execute(
                "SELECT version FROM prompt_version WHERE tenant=? AND name=?",
                (tenant, name),
            )
        }
        if not set(clean) <= available:
            raise ValueError("all experiment versions must exist")
        digest = hashlib.sha256(f"{tenant}:{name}:{subject}".encode()).digest()
        version = clean[int.from_bytes(digest[:8], "big") % len(clean)]
        return {
            "name": name,
            "subject": subject,
            "version": version,
            "experiment": clean,
        }
