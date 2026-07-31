"""Enterprise platform capabilities for catalog, governance, analytics, and safe releases."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence


def _number(value: object, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


class PromptCatalog:
    """Validate and normalize prompt catalog entries."""

    def register(
        self, name: str, version: str, environments: Sequence[str]
    ) -> dict[str, object]:
        """Return a normalized immutable prompt catalog record."""
        if (
            not name.strip()
            or not re.fullmatch(r"\d+\.\d+\.\d+", version)
            or not environments
        ):
            raise ValueError("name, semantic version, and environments are required")
        return {
            "name": name.strip(),
            "version": version,
            "environments": sorted(set(environments)),
        }


class ModelCatalog:
    """Validate model metadata used for routing decisions."""

    def register(
        self,
        name: str,
        context_window: int,
        capabilities: Sequence[str],
        external: bool,
    ) -> dict[str, object]:
        """Return a normalized model catalog record."""
        if (
            not name
            or isinstance(context_window, bool)
            or context_window < 1
            or not capabilities
        ):
            raise ValueError("valid model metadata is required")
        return {
            "name": name,
            "context_window": context_window,
            "capabilities": sorted(set(capabilities)),
            "classification": "external" if external else "internal",
        }


class UsageTagger:
    """Normalize bounded cost-allocation tags."""

    def normalize(self, tags: Mapping[str, object]) -> dict[str, str]:
        """Return lower-case safe tags with deterministic ordering."""
        if len(tags) > 20:
            raise ValueError("at most 20 tags are allowed")
        result = {}
        for key, value in tags.items():
            if (
                not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", key)
                or not isinstance(value, str)
                or len(value) > 100
            ):
                raise ValueError("tag keys or values are invalid")
            result[key.lower()] = value.strip()
        return dict(sorted(result.items()))


class CostAllocator:
    """Allocate cost by weighted business dimensions."""

    def allocate(self, total: float, weights: Mapping[str, float]) -> dict[str, float]:
        """Allocate the exact total without negative or non-finite values."""
        total = _number(total, "total")
        values = {k: _number(v, k) for k, v in weights.items()}
        if (
            total < 0
            or not values
            or any(v < 0 for v in values.values())
            or sum(values.values()) <= 0
        ):
            raise ValueError("positive weights and non-negative total are required")
        denominator = sum(values.values())
        return {k: total * v / denominator for k, v in sorted(values.items())}


class QuotaPlanner:
    """Plan request and token headroom from provider limits."""

    def plan(
        self,
        request_limit: int,
        token_limit: int,
        expected_requests: int,
        expected_tokens: int,
    ) -> dict[str, object]:
        """Return remaining capacity and the first exhausted quota dimension."""
        values = (request_limit, token_limit, expected_requests, expected_tokens)
        if any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in values):
            raise ValueError("quota values must be non-negative integers")
        return {
            "request_headroom": max(0, request_limit - expected_requests),
            "token_headroom": max(0, token_limit - expected_tokens),
            "allowed": expected_requests <= request_limit
            and expected_tokens <= token_limit,
        }


class AlertRuleEvaluator:
    """Evaluate threshold alerts with explicit comparison operators."""

    def evaluate(
        self, metric: float, operator: str, threshold: float
    ) -> dict[str, object]:
        """Return a deterministic alert decision."""
        metric, threshold = _number(metric, "metric"), _number(threshold, "threshold")
        operations = {
            ">": metric > threshold,
            ">=": metric >= threshold,
            "<": metric < threshold,
            "<=": metric <= threshold,
        }
        if operator not in operations:
            raise ValueError("unsupported alert operator")
        return {
            "triggered": operations[operator],
            "metric": metric,
            "threshold": threshold,
        }


class SLOCalculator:
    """Calculate availability and error-budget health."""

    def calculate(self, total: int, failures: int, target: float) -> dict[str, object]:
        """Return availability, target compliance, and remaining error budget."""
        if (
            isinstance(total, bool)
            or isinstance(failures, bool)
            or total < 1
            or not 0 <= failures <= total
            or not 0 < target <= 1
        ):
            raise ValueError("invalid SLO inputs")
        availability = (total - failures) / total
        return {
            "availability": availability,
            "met": availability >= target,
            "remaining_error_budget": max(0.0, total * (1 - target) - failures),
        }


class IncidentDigest:
    """Summarize normalized incident events."""

    def summarize(self, events: Sequence[Mapping[str, object]]) -> dict[str, object]:
        """Return counts, duration, and critical-event presence."""
        if not events:
            raise ValueError("events are required")
        times = [int(e["timestamp"]) for e in events]
        kinds = [str(e["kind"]) for e in events]
        if any(x < 0 for x in times) or any(not x for x in kinds):
            raise ValueError("invalid incident event")
        return {
            "duration_seconds": max(times) - min(times),
            "counts": dict(Counter(kinds)),
            "critical": any(x in {"security", "data_loss"} for x in kinds),
        }


class RetentionPolicy:
    """Calculate expiry for classified records."""

    def expiry(self, created_at: int, days: int, legal_hold: bool) -> dict[str, object]:
        """Return expiry timestamp or legal-hold state."""
        if (
            isinstance(created_at, bool)
            or isinstance(days, bool)
            or created_at < 0
            or days < 1
        ):
            raise ValueError("invalid retention data")
        return {
            "expires_at": None if legal_hold else created_at + days * 86400,
            "legal_hold": legal_hold,
        }


class DLPClassifier:
    """Classify common sensitive-data patterns without returning values."""

    _patterns = {
        "email": re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b"),
        "secret": re.compile(r"\b(?:sk|gw)_[A-Za-z0-9_-]{8,}\b"),
        "card": re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
    }

    def classify(self, text: str) -> dict[str, object]:
        """Return finding categories and a block decision."""
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        findings = sorted(
            name for name, pattern in self._patterns.items() if pattern.search(text)
        )
        return {"findings": findings, "blocked": bool(findings)}


class RegionRouter:
    """Choose a healthy provider in an allowed region."""

    def choose(
        self, providers: Sequence[Mapping[str, object]], allowed_regions: Sequence[str]
    ) -> dict[str, object]:
        """Select lowest-latency healthy compliant provider."""
        allowed = set(allowed_regions)
        eligible = [
            p
            for p in providers
            if p.get("healthy") is True and p.get("region") in allowed
        ]
        if not eligible:
            raise ValueError("no compliant healthy provider")
        selected = min(
            eligible,
            key=lambda p: (
                _number(p.get("latency_ms"), "latency_ms"),
                str(p.get("name")),
            ),
        )
        return {"provider": selected["name"], "region": selected["region"]}


class ProviderScorecard:
    """Score provider cost, latency, quality, and reliability."""

    def score(
        self, cost: float, latency: float, quality: float, reliability: float
    ) -> dict[str, object]:
        """Return a normalized zero-to-one provider score."""
        cost, latency, quality, reliability = map(
            float,
            (
                _number(cost, "cost"),
                _number(latency, "latency"),
                _number(quality, "quality"),
                _number(reliability, "reliability"),
            ),
        )
        if (
            cost < 0
            or latency < 0
            or not 0 <= quality <= 1
            or not 0 <= reliability <= 1
        ):
            raise ValueError("invalid scorecard inputs")
        score = (
            0.35 * quality
            + 0.35 * reliability
            + 0.15 / (1 + cost)
            + 0.15 / (1 + latency / 1000)
        )
        return {
            "score": score,
            "grade": "A" if score >= 0.85 else "B" if score >= 0.7 else "C",
        }


class CanaryPlanner:
    """Create bounded canary rollout stages."""

    def plan(self, percentages: Sequence[int]) -> dict[str, object]:
        """Validate strictly increasing rollout percentages ending at 100."""
        if (
            not percentages
            or percentages[-1] != 100
            or any(isinstance(x, bool) or not 1 <= x <= 100 for x in percentages)
            or list(percentages) != sorted(set(percentages))
        ):
            raise ValueError("canary stages must increase and end at 100")
        return {"stages": list(percentages), "count": len(percentages)}


class RollbackDecision:
    """Decide whether release metrics require rollback."""

    def decide(
        self, quality_delta: float, error_rate: float, latency_delta: float
    ) -> dict[str, object]:
        """Rollback on material quality, error, or latency regression."""
        quality_delta, error_rate, latency_delta = map(
            float,
            (
                _number(quality_delta, "quality_delta"),
                _number(error_rate, "error_rate"),
                _number(latency_delta, "latency_delta"),
            ),
        )
        rollback = quality_delta < -0.02 or error_rate > 0.05 or latency_delta > 0.25
        return {
            "rollback": rollback,
            "reason": "guardrail_breached" if rollback else "within_guardrails",
        }


class FeedbackAggregator:
    """Aggregate explicit user ratings."""

    def aggregate(self, ratings: Sequence[int]) -> dict[str, object]:
        """Return average, sample count, and positive share."""
        if not ratings or any(isinstance(x, bool) or not 1 <= x <= 5 for x in ratings):
            raise ValueError("ratings must be integers from one to five")
        return {
            "average": sum(ratings) / len(ratings),
            "samples": len(ratings),
            "positive_share": sum(x >= 4 for x in ratings) / len(ratings),
        }


class QualityDriftDetector:
    """Detect quality drift between baseline and current observations."""

    def detect(
        self, baseline: Sequence[float], current: Sequence[float], tolerance: float
    ) -> dict[str, object]:
        """Return mean delta and drift state."""
        if not baseline or not current or tolerance < 0:
            raise ValueError("baseline, current, and tolerance are required")
        b = sum(_number(x, "baseline") for x in baseline) / len(baseline)
        c = sum(_number(x, "current") for x in current) / len(current)
        return {
            "baseline_mean": b,
            "current_mean": c,
            "delta": c - b,
            "drifted": c < b - tolerance,
        }


class DatasetCurator:
    """Deduplicate evaluation examples by canonical JSON content."""

    def curate(self, examples: Sequence[Mapping[str, object]]) -> dict[str, object]:
        """Return unique examples and duplicate count."""
        seen, unique = set(), []
        for example in examples:
            digest = hashlib.sha256(
                json.dumps(example, sort_keys=True).encode()
            ).hexdigest()
            if digest not in seen:
                seen.add(digest)
                unique.append(dict(example))
        return {"examples": unique, "duplicates_removed": len(examples) - len(unique)}


class ExportManifest:
    """Create integrity metadata for exported files."""

    def build(self, files: Mapping[str, bytes]) -> dict[str, object]:
        """Return sorted file hashes and aggregate digest."""
        if any(
            not name or name.startswith("/") or ".." in name.split("/")
            for name in files
        ):
            raise ValueError("unsafe export path")
        entries = {
            name: hashlib.sha256(data).hexdigest()
            for name, data in sorted(files.items())
        }
        return {
            "files": entries,
            "sha256": hashlib.sha256(
                json.dumps(entries, sort_keys=True).encode()
            ).hexdigest(),
        }


class ContractCompatibility:
    """Compare required API fields for backward compatibility."""

    def compare(
        self, previous: Sequence[str], proposed: Sequence[str]
    ) -> dict[str, object]:
        """Return removed required fields and compatibility state."""
        removed = sorted(set(previous) - set(proposed))
        return {
            "compatible": not removed,
            "removed": removed,
            "added": sorted(set(proposed) - set(previous)),
        }


class AdoptionFunnel:
    """Calculate conversion through product adoption stages."""

    def calculate(self, stages: Mapping[str, int]) -> dict[str, object]:
        """Return stage conversion rates while rejecting impossible funnels."""
        if not stages or any(
            isinstance(v, bool) or not isinstance(v, int) or v < 0
            for v in stages.values()
        ):
            raise ValueError("stage counts must be non-negative integers")
        values = list(stages.items())
        if any(values[i][1] > values[i - 1][1] for i in range(1, len(values))):
            raise ValueError("funnel stages cannot grow")
        conversions = {
            values[i][0]: (values[i][1] / values[i - 1][1] if values[i - 1][1] else 0.0)
            for i in range(1, len(values))
        }
        return {
            "stages": dict(values),
            "conversions": conversions,
            "overall": values[-1][1] / values[0][1] if values[0][1] else 0.0,
        }
