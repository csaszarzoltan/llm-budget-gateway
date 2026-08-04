"""Market-validated replay, runtime-governance, and compatibility services."""

from __future__ import annotations

import math
import sqlite3
import time
from collections import Counter
from collections.abc import Callable, Collection, Sequence
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Any


@dataclass(frozen=True)
class ReplayTrace:
    """Privacy-safe baseline evidence captured from a production request."""

    request_id: str
    output: str
    tools: tuple[str, ...]
    cost_usd: float
    tokens: int
    latency_ms: int
    policy: str


@dataclass(frozen=True)
class ReplayCandidate:
    """Candidate replay result produced by a new model or configuration."""

    model: str
    output: str
    tools: tuple[str, ...]
    cost_usd: float
    tokens: int
    latency_ms: int
    policy: str


@dataclass(frozen=True)
class ChangeImpact:
    """Explainable before/after impact result for one replay."""

    request_id: str
    model: str
    similarity: float
    cost_delta_usd: float
    token_delta: int
    latency_delta_ms: int
    tool_changes: dict[str, list[str]]
    safety_changed: bool
    recommendation: str


class ChangeImpactLab:
    """Compare an exact production trace with a candidate replay result."""

    def compare(
        self, baseline: ReplayTrace, candidate: ReplayCandidate
    ) -> ChangeImpact:
        """Validate evidence and return semantic, operational, and safety deltas."""
        if not baseline.request_id.strip():
            raise ValueError("request_id must be non-empty")
        if not candidate.model.strip():
            raise ValueError("candidate model must be non-empty")
        numeric = (
            baseline.cost_usd,
            baseline.tokens,
            baseline.latency_ms,
            candidate.cost_usd,
            candidate.tokens,
            candidate.latency_ms,
        )
        if any(not math.isfinite(float(value)) or value < 0 for value in numeric):
            raise ValueError("replay metrics must be finite and non-negative")
        similarity = round(
            SequenceMatcher(None, baseline.output, candidate.output).ratio(), 4
        )
        added = sorted(set(candidate.tools) - set(baseline.tools))
        removed = sorted(set(baseline.tools) - set(candidate.tools))
        safety_changed = baseline.policy != candidate.policy
        recommendation = (
            "reject"
            if safety_changed and candidate.policy != "allow"
            else "review"
            if similarity < 0.98 or added or removed
            else "accept"
        )
        return ChangeImpact(
            baseline.request_id,
            candidate.model,
            similarity,
            candidate.cost_usd - baseline.cost_usd,
            candidate.tokens - baseline.tokens,
            candidate.latency_ms - baseline.latency_ms,
            {"added": added, "removed": removed},
            safety_changed,
            recommendation,
        )


@dataclass(frozen=True)
class RuntimeStep:
    """One intended or executed agent action."""

    action: str
    intent: str
    irreversible: bool = False


@dataclass(frozen=True)
class GovernorDecision:
    """Fail-closed runtime decision with an actionable explanation."""

    allowed: bool
    code: str
    explanation: str
    next_action: str


class RuntimeGovernor:
    """Detect repeated actions, intent drift, and unapproved irreversible work."""

    def __init__(self, loop_threshold: int = 3) -> None:
        """Create a governor with a minimum repeated-action threshold of two."""
        if loop_threshold < 2:
            raise ValueError("loop_threshold must be at least 2")
        self.loop_threshold = loop_threshold

    def evaluate(
        self,
        *,
        intent: str,
        steps: Sequence[RuntimeStep],
        approved_actions: Collection[str],
    ) -> GovernorDecision:
        """Evaluate the planned intent against measured execution steps."""
        if not intent.strip():
            raise ValueError("intent must be non-empty")
        counts = Counter(step.action for step in steps)
        repeated = next(
            (
                action
                for action, count in counts.items()
                if count >= self.loop_threshold
            ),
            None,
        )
        if repeated:
            return GovernorDecision(
                False,
                "loop_detected",
                f"Action {repeated!r} repeated {counts[repeated]} times.",
                "Pause the run and inspect the repeated branch before resuming.",
            )
        drift = next((step for step in steps if step.intent != intent), None)
        if drift:
            return GovernorDecision(
                False,
                "intent_drift",
                f"Action {drift.action!r} has intent {drift.intent!r}, not {intent!r}.",
                "Return to the approved plan or request a new approval.",
            )
        unapproved = next(
            (
                step
                for step in steps
                if step.irreversible and step.action not in approved_actions
            ),
            None,
        )
        if unapproved:
            return GovernorDecision(
                False,
                "approval_required",
                f"Irreversible action {unapproved.action!r} is not approved.",
                "Request human approval before execution.",
            )
        return GovernorDecision(
            True,
            "allowed",
            "Execution matches the approved intent and safety boundaries.",
            "Continue with the next step.",
        )


@dataclass(frozen=True)
class CompatibilityContract:
    """Measured capability and pricing contract for one provider model."""

    provider_id: str
    model_id: str
    capability: str
    supported: bool
    checked_at: int
    price_per_million: float | None
    region: str


class CompatibilityContractCatalog:
    """Persist fresh provider contracts and answer route-eligibility queries."""

    def __init__(
        self, connection: sqlite3.Connection, now_fn: Callable[[], int] | None = None
    ) -> None:
        """Initialize the SQLite catalog using an injectable clock."""
        self.connection = connection
        self.now_fn = now_fn or (lambda: int(time.time()))
        connection.execute("""CREATE TABLE IF NOT EXISTS compatibility_contracts(
            provider_id TEXT NOT NULL, model_id TEXT NOT NULL, capability TEXT NOT NULL,
            supported INTEGER NOT NULL, checked_at INTEGER NOT NULL, price_per_million REAL,
            region TEXT NOT NULL, PRIMARY KEY(provider_id,model_id,capability,region))""")
        connection.commit()

    def record(self, contract: CompatibilityContract) -> CompatibilityContract:
        """Validate and upsert one measured contract."""
        if not all(
            value.strip()
            for value in (
                contract.provider_id,
                contract.model_id,
                contract.capability,
                contract.region,
            )
        ):
            raise ValueError(
                "provider_id, model_id, capability, and region must be non-empty"
            )
        if contract.checked_at < 0 or (
            contract.price_per_million is not None
            and (
                not math.isfinite(contract.price_per_million)
                or contract.price_per_million < 0
            )
        ):
            raise ValueError("timestamps and pricing must be finite and non-negative")
        self.connection.execute(
            "INSERT INTO compatibility_contracts VALUES(?,?,?,?,?,?,?) ON CONFLICT(provider_id,model_id,capability,region) DO UPDATE SET supported=excluded.supported,checked_at=excluded.checked_at,price_per_million=excluded.price_per_million",
            (
                contract.provider_id,
                contract.model_id,
                contract.capability,
                int(contract.supported),
                contract.checked_at,
                contract.price_per_million,
                contract.region,
            ),
        )
        self.connection.commit()
        return contract

    def record_result(
        self,
        result: Any,
        *,
        model_id: str,
        checked_at: int,
        region: str,
        price_per_million: float | None = None,
    ) -> int:
        """Persist every measured probe from a compatibility result."""
        for probe in result.probes:
            self.record(
                CompatibilityContract(
                    result.provider_id,
                    model_id,
                    probe.capability,
                    probe.passed,
                    checked_at,
                    price_per_million,
                    region,
                )
            )
        return len(result.probes)

    def matrix(self, provider_id: str) -> list[dict[str, Any]]:
        """Return newest-first provider contracts as JSON-ready dictionaries."""
        rows = self.connection.execute(
            "SELECT provider_id,model_id,capability,supported,checked_at,price_per_million,region FROM compatibility_contracts WHERE provider_id=? ORDER BY checked_at DESC,model_id,capability",
            (provider_id,),
        ).fetchall()
        return [
            asdict(
                CompatibilityContract(
                    str(r[0]),
                    str(r[1]),
                    str(r[2]),
                    bool(r[3]),
                    int(r[4]),
                    None if r[5] is None else float(r[5]),
                    str(r[6]),
                )
            )
            for r in rows
        ]

    def eligible(
        self,
        *,
        provider_id: str,
        model_id: str,
        required: Sequence[str],
        max_age_seconds: int,
        region: str,
        require_price: bool = False,
    ) -> bool:
        """Return true only when every required contract is supported and fresh."""
        if max_age_seconds < 0:
            raise ValueError("max_age_seconds must be non-negative")
        for capability in required:
            row = self.connection.execute(
                "SELECT supported,checked_at,price_per_million FROM compatibility_contracts WHERE provider_id=? AND model_id=? AND capability=? AND region=?",
                (provider_id, model_id, capability, region),
            ).fetchone()
            if (
                row is None
                or not bool(row[0])
                or self.now_fn() - int(row[1]) > max_age_seconds
                or (require_price and row[2] is None)
            ):
                return False
        return True
