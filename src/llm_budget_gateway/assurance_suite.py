"""Continuous AI assurance services for quality, safety, control, and audit evidence."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence


def _f(v: object, n: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"{n} must be finite")
    return float(v)


class RiskTier:
    """Classify AI use-case risk."""

    def classify(
        self, impact: int, autonomy: int, sensitive: bool
    ) -> dict[str, object]:
        """Return low through critical risk tier."""
        if impact not in range(1, 6) or autonomy not in range(0, 6):
            raise ValueError("invalid risk inputs")
        score = impact + autonomy + (2 if sensitive else 0)
        return {
            "score": score,
            "tier": "critical"
            if score >= 10
            else "high"
            if score >= 7
            else "medium"
            if score >= 4
            else "low",
        }


class ControlTest:
    """Evaluate control operating effectiveness."""

    def evaluate(self, passed: int, total: int) -> dict[str, object]:
        """Return effectiveness ratio and status."""
        if total < 1 or not 0 <= passed <= total:
            raise ValueError("invalid control counts")
        rate = passed / total
        return {"rate": rate, "effective": rate >= 0.95}


class EvaluationGate:
    """Gate releases on multiple evaluation metrics."""

    def decide(
        self, metrics: Mapping[str, float], thresholds: Mapping[str, float]
    ) -> dict[str, object]:
        """Return failed metrics and release decision."""
        if set(metrics) != set(thresholds):
            raise ValueError("metric sets must match")
        failed = sorted(
            k for k, v in metrics.items() if _f(v, k) < _f(thresholds[k], k)
        )
        return {"allowed": not failed, "failed": failed}


class CalibrationMetric:
    """Calculate mean confidence error."""

    def calculate(
        self, confidence: Sequence[float], correct: Sequence[bool]
    ) -> dict[str, float]:
        """Return mean absolute calibration error."""
        if not confidence or len(confidence) != len(correct):
            raise ValueError("aligned samples required")
        return {
            "error": sum(
                abs(_f(c, "confidence") - int(y))
                for c, y in zip(confidence, correct, strict=True)
            )
            / len(confidence)
        }


class RefusalQuality:
    """Measure safe refusal correctness."""

    def calculate(
        self, expected: Sequence[bool], actual: Sequence[bool]
    ) -> dict[str, float]:
        """Return refusal accuracy."""
        if not expected or len(expected) != len(actual):
            raise ValueError("aligned samples required")
        return {
            "accuracy": sum(a == b for a, b in zip(expected, actual, strict=True))
            / len(expected)
        }


class FairnessGap:
    """Measure disparity between group success rates."""

    def calculate(self, rates: Mapping[str, float]) -> dict[str, object]:
        """Return max group gap."""
        if len(rates) < 2:
            raise ValueError("two groups required")
        vals = [_f(v, k) for k, v in rates.items()]
        if any(not 0 <= v <= 1 for v in vals):
            raise ValueError("rates out of range")
        gap = max(vals) - min(vals)
        return {"gap": gap, "acceptable": gap <= 0.1}


class RobustnessScore:
    """Measure performance retained under perturbation."""

    def calculate(self, baseline: float, perturbed: float) -> dict[str, float]:
        """Return retained-performance ratio."""
        b = _f(baseline, "baseline")
        p = _f(perturbed, "perturbed")
        if b <= 0 or p < 0:
            raise ValueError("invalid scores")
        return {"retention": min(1.0, p / b)}


class HallucinationRate:
    """Calculate unsupported claim rate."""

    def calculate(self, unsupported: int, claims: int) -> dict[str, float]:
        """Return hallucination rate."""
        if claims < 1 or not 0 <= unsupported <= claims:
            raise ValueError("invalid claim counts")
        return {"rate": unsupported / claims}


class ProvenanceRecord:
    """Fingerprint model, data, prompt, and policy lineage."""

    def build(
        self, model: str, prompt: str, dataset: str, policy: str
    ) -> dict[str, str]:
        """Return canonical provenance digest."""
        if not all((model, prompt, dataset, policy)):
            raise ValueError("lineage fields required")
        raw = json.dumps(
            {"model": model, "prompt": prompt, "dataset": dataset, "policy": policy},
            sort_keys=True,
        )
        return {"digest": hashlib.sha256(raw.encode()).hexdigest()}


class ChangeApproval:
    """Require approvals appropriate to release risk."""

    def decide(self, risk: str, approvers: Sequence[str]) -> dict[str, object]:
        """Return whether approval quorum is met."""
        need = {"low": 0, "medium": 1, "high": 2, "critical": 3}.get(risk)
        if need is None:
            raise ValueError("invalid risk")
        count = len(set(a for a in approvers if a))
        return {"allowed": count >= need, "required": need, "received": count}


class IncidentSeverity:
    """Classify AI incident severity."""

    def classify(
        self, users: int, data_exposure: bool, financial_loss: float
    ) -> dict[str, str]:
        """Return P1 through P4 severity."""
        if users < 0 or _f(financial_loss, "financial_loss") < 0:
            raise ValueError("invalid incident")
        score = (
            (4 if data_exposure else 0)
            + min(4, users // 100)
            + min(4, int(financial_loss // 10000))
        )
        return {
            "severity": "P1"
            if score >= 8
            else "P2"
            if score >= 5
            else "P3"
            if score >= 2
            else "P4"
        }


class CorrectiveAction:
    """Track corrective-action completion."""

    def status(self, completed: int, total: int, overdue: int) -> dict[str, object]:
        """Return closure and escalation status."""
        if total < 1 or not 0 <= completed <= total or overdue < 0:
            raise ValueError("invalid actions")
        return {"closure_rate": completed / total, "escalate": overdue > 0}


class VendorRisk:
    """Score third-party AI supplier risk."""

    def assess(
        self, security: int, transparency: int, resilience: int
    ) -> dict[str, object]:
        """Return weighted vendor risk."""
        vals = (security, transparency, resilience)
        if any(v not in range(1, 6) for v in vals):
            raise ValueError("scores must be 1-5")
        score = 1 - sum(vals) / 15
        return {"risk": score, "approved": score <= 0.25}


class DataQuality:
    """Score completeness, freshness, and validity."""

    def calculate(
        self, completeness: float, freshness: float, validity: float
    ) -> dict[str, object]:
        """Return aggregate data quality."""
        vals = [_f(x, "quality") for x in (completeness, freshness, validity)]
        if any(not 0 <= x <= 1 for x in vals):
            raise ValueError("quality out of range")
        score = sum(vals) / 3
        return {"score": score, "ready": score >= 0.8}


class DriftAlert:
    """Detect material metric drift."""

    def detect(
        self, baseline: float, current: float, tolerance: float
    ) -> dict[str, object]:
        """Return absolute drift and alert state."""
        b = _f(baseline, "baseline")
        c = _f(current, "current")
        t = _f(tolerance, "tolerance")
        if t < 0:
            raise ValueError("negative tolerance")
        d = abs(c - b)
        return {"drift": d, "alert": d > t}


class RedTeamCoverage:
    """Measure adversarial category coverage."""

    def calculate(
        self, tested: Sequence[str], required: Sequence[str]
    ) -> dict[str, object]:
        """Return missing adversarial categories."""
        missing = sorted(set(required) - set(tested))
        return {
            "coverage": 1 - len(missing) / len(set(required)) if required else 1.0,
            "missing": missing,
        }


class EvidenceFreshness:
    """Check audit evidence age."""

    def evaluate(self, collected_at: int, now: int, max_age: int) -> dict[str, object]:
        """Return evidence freshness state."""
        if min(collected_at, now, max_age) < 0:
            raise ValueError("invalid time")
        age = max(0, now - collected_at)
        return {"age": age, "fresh": age <= max_age}


class MaturityScore:
    """Calculate assurance maturity across domains."""

    def calculate(self, domains: Mapping[str, int]) -> dict[str, object]:
        """Return average and maturity level."""
        if not domains or any(v not in range(1, 6) for v in domains.values()):
            raise ValueError("domain levels must be 1-5")
        score = sum(domains.values()) / len(domains)
        return {
            "score": score,
            "level": "optimized"
            if score >= 4.5
            else "managed"
            if score >= 3.5
            else "defined"
            if score >= 2.5
            else "initial",
        }


class AssuranceReport:
    """Create an integrity-protected assurance summary."""

    def build(self, findings: Sequence[Mapping[str, object]]) -> dict[str, object]:
        """Return counts and canonical digest."""
        canonical = json.dumps(list(findings), sort_keys=True, separators=(",", ":"))
        return {
            "findings": len(findings),
            "digest": hashlib.sha256(canonical.encode()).hexdigest(),
        }


class BenefitRealization:
    """Compare delivered value with planned value."""

    def calculate(
        self, planned: float, realized: float, cost: float
    ) -> dict[str, object]:
        """Return realization and net value."""
        p = _f(planned, "planned")
        r = _f(realized, "realized")
        c = _f(cost, "cost")
        if p <= 0 or min(r, c) < 0:
            raise ValueError("invalid benefits")
        return {"realization": r / p, "net_value": r - c}
