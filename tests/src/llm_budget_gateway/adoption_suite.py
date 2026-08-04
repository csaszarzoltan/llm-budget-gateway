"""Deterministic product-adoption and rollout decision services."""

from __future__ import annotations

import math
from hashlib import sha256
from typing import Any


def _num(v: Any, name: str, minimum: float = 0) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{name} must be numeric")
    value = float(v)
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name} must be finite and >= {minimum}")
    return value


class ActivationFunnel:
    """Calculate sequential onboarding conversions and the largest drop-off."""

    def calculate(self, stages: dict[str, int]) -> dict[str, object]:
        if not stages:
            raise ValueError("stages must not be empty")
        items = list(stages.items())
        previous = None
        conversions = {}
        dropoffs = {}
        for name, count in items:
            value = int(_num(count, name))
            if previous is not None and value > previous:
                raise ValueError("funnel counts must not increase")
            conversions[name] = (
                1.0 if previous is None else (value / previous if previous else 0.0)
            )
            dropoffs[name] = 0 if previous is None else previous - value
            previous = value
        largest = max(dropoffs, key=dropoffs.get)
        return {
            "conversions": conversions,
            "dropoffs": dropoffs,
            "largest_dropoff": largest,
            "completion": items[-1][1] / items[0][1] if items[0][1] else 0.0,
        }


class CohortRetention:
    """Measure retained users and rate for a cohort."""

    def calculate(self, cohort_size: int, active_users: int) -> dict[str, object]:
        cohort = int(_num(cohort_size, "cohort_size"))
        active = int(_num(active_users, "active_users"))
        if cohort < 1 or active > cohort:
            raise ValueError("invalid cohort counts")
        return {
            "retained": active,
            "churned": cohort - active,
            "retention": active / cohort,
        }


class FeatureAdoption:
    """Rank feature use and report zero-adoption capabilities."""

    def summarize(
        self, eligible_users: int, usage: dict[str, int]
    ) -> dict[str, object]:
        eligible = int(_num(eligible_users, "eligible_users"))
        if eligible < 1:
            raise ValueError("eligible_users must be positive")
        rates = {
            name: int(_num(count, name)) / eligible for name, count in usage.items()
        }
        if any(rate > 1 for rate in rates.values()):
            raise ValueError("usage cannot exceed eligible users")
        return {
            "rates": dict(sorted(rates.items(), key=lambda x: (-x[1], x[0]))),
            "unused": sorted(name for name, rate in rates.items() if rate == 0),
        }


class ExperimentAssignment:
    """Assign subjects deterministically to weighted experiment variants."""

    def assign(
        self, experiment: str, subject: str, weights: dict[str, int]
    ) -> dict[str, object]:
        if not experiment or not subject or not weights:
            raise ValueError("experiment, subject and weights are required")
        normalized = {k: int(_num(v, k, 1)) for k, v in weights.items()}
        total = sum(normalized.values())
        point = (
            int(sha256(f"{experiment}:{subject}".encode()).hexdigest()[:16], 16) % total
        )
        running = 0
        for name, weight in sorted(normalized.items()):
            running += weight
            if point < running:
                return {"variant": name, "bucket": point, "total_weight": total}
        raise RuntimeError("unreachable")


class ExperimentOutcome:
    """Compare control and treatment conversion with guardrails."""

    def evaluate(
        self,
        control_users: int,
        control_success: int,
        treatment_users: int,
        treatment_success: int,
        max_regression: float = 0,
    ) -> dict[str, object]:
        cu = int(_num(control_users, "control_users", 1))
        cs = int(_num(control_success, "control_success"))
        tu = int(_num(treatment_users, "treatment_users", 1))
        ts = int(_num(treatment_success, "treatment_success"))
        guard = _num(max_regression, "max_regression")
        if cs > cu or ts > tu:
            raise ValueError("successes cannot exceed users")
        cr, tr = cs / cu, ts / tu
        lift = tr - cr
        return {
            "control_rate": cr,
            "treatment_rate": tr,
            "absolute_lift": lift,
            "relative_lift": lift / cr if cr else None,
            "ship": lift >= -guard,
        }


class FeedbackTheme:
    """Aggregate bounded feedback categories without storing comments."""

    ALLOWED = (
        "onboarding",
        "documentation",
        "reliability",
        "cost",
        "security",
        "ui",
        "other",
    )

    def aggregate(self, categories: list[str]) -> dict[str, object]:
        invalid = sorted(set(categories) - set(self.ALLOWED))
        if invalid:
            raise ValueError(f"unsupported categories: {', '.join(invalid)}")
        counts = {
            name: categories.count(name) for name in self.ALLOWED if name in categories
        }
        return {
            "counts": counts,
            "top": max(counts, key=counts.get) if counts else None,
            "total": len(categories),
        }


class PricingSignal:
    """Summarize Van Westendorp-style price inputs."""

    def summarize(self, responses: list[dict[str, float]]) -> dict[str, object]:
        if not responses:
            raise ValueError("responses must not be empty")
        keys = ("too_cheap", "cheap", "expensive", "too_expensive")
        values = {k: [] for k in keys}
        for response in responses:
            row = [_num(response[k], k) for k in keys]
            if row != sorted(row):
                raise ValueError("price points must be ordered")
            for key, value in zip(keys, row, strict=True):
                values[key].append(value)
        medians = {k: sorted(v)[len(v) // 2] for k, v in values.items()}
        return {
            "medians": medians,
            "acceptable_range": [medians["cheap"], medians["expensive"]],
            "responses": len(responses),
        }


class RolloutCohort:
    """Select a stable percentage cohort for staged rollout."""

    def decide(self, tenant_id: str, percentage: float) -> dict[str, object]:
        pct = _num(percentage, "percentage")
        if pct > 100 or not tenant_id:
            raise ValueError("invalid rollout")
        bucket = int(sha256(tenant_id.encode()).hexdigest()[:8], 16) % 10000 / 100
        return {"included": bucket < pct, "bucket": bucket, "percentage": pct}


class SuccessThreshold:
    """Evaluate product metrics against explicit minimums and maximums."""

    def evaluate(
        self,
        metrics: dict[str, float],
        minimums: dict[str, float] | None = None,
        maximums: dict[str, float] | None = None,
    ) -> dict[str, object]:
        failures = []
        for name, limit in (minimums or {}).items():
            if _num(metrics.get(name), name) < _num(limit, name):
                failures.append(f"{name}:below-minimum")
        for name, limit in (maximums or {}).items():
            if _num(metrics.get(name), name) > _num(limit, name):
                failures.append(f"{name}:above-maximum")
        return {"passed": not failures, "failures": sorted(failures)}


class AdoptionReport:
    """Create a canonical privacy-safe adoption report fingerprint."""

    def build(
        self, period: str, metrics: dict[str, float], decisions: list[str]
    ) -> dict[str, object]:
        if not period:
            raise ValueError("period is required")
        safe_metrics = {k: _num(v, k) for k, v in sorted(metrics.items())}
        safe_decisions = sorted(str(x) for x in decisions)
        payload = repr((period, safe_metrics, safe_decisions))
        return {
            "period": period,
            "metrics": safe_metrics,
            "decisions": safe_decisions,
            "sha256": sha256(payload.encode()).hexdigest(),
        }
