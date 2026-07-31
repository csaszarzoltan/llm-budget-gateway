"""AgentOps domain services for secure, economical, observable agent workflows."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from collections.abc import Mapping, Sequence


def _finite(value: object, name: str, minimum: float = 0.0) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < minimum
    ):
        raise ValueError(f"{name} must be a finite number >= {minimum}")
    return float(value)


class MCPServerRegistry:
    """Normalize registered MCP server identity and tool metadata."""

    def register(self, name: str, url: str, tools: Sequence[str]) -> dict[str, object]:
        """Return a normalized server card for HTTPS endpoints."""
        if not name.strip() or not url.startswith("https://") or not tools:
            raise ValueError("name, HTTPS URL, and tools are required")
        return {
            "name": name.strip(),
            "url": url.rstrip("/"),
            "tools": sorted(set(tools)),
        }


class ToolAccessPolicy:
    """Enforce explicit allow and deny lists for agent tools."""

    def decide(
        self, tool: str, allowed: Sequence[str], denied: Sequence[str]
    ) -> dict[str, object]:
        """Deny explicitly denied, unknown, or empty tool names."""
        if not tool:
            raise ValueError("tool is required")
        granted = tool in set(allowed) and tool not in set(denied)
        return {
            "allowed": granted,
            "reason": "allowed" if granted else "tool_not_permitted",
        }


class DelegationDepthPolicy:
    """Bound recursive agent delegation."""

    def evaluate(self, current_depth: int, maximum_depth: int) -> dict[str, object]:
        """Return whether another delegation step is allowed."""
        if any(
            isinstance(x, bool) or not isinstance(x, int) or x < 0
            for x in (current_depth, maximum_depth)
        ):
            raise ValueError("depths must be non-negative integers")
        return {
            "allowed": current_depth < maximum_depth,
            "remaining": max(0, maximum_depth - current_depth),
        }


class TaskLease:
    """Evaluate ownership of expiring asynchronous task leases."""

    def evaluate(
        self, owner: str, claimant: str, expires_at: int, now: int
    ) -> dict[str, object]:
        """Allow the owner before expiry and any claimant after expiry."""
        if not owner or not claimant or min(expires_at, now) < 0:
            raise ValueError("valid lease data is required")
        expired = now >= expires_at
        return {"claimable": expired or owner == claimant, "expired": expired}


class ReplayProtector:
    """Validate timestamped HMAC request signatures."""

    def verify(
        self,
        body: bytes,
        timestamp: int,
        now: int,
        signature: str,
        secret: str,
        tolerance: int = 300,
    ) -> dict[str, object]:
        """Reject stale or incorrectly signed requests."""
        if not secret or tolerance < 0 or abs(now - timestamp) > tolerance:
            return {"valid": False, "reason": "stale_or_unconfigured"}
        expected = hmac.new(
            secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256
        ).hexdigest()
        valid = hmac.compare_digest(expected, signature)
        return {"valid": valid, "reason": "valid" if valid else "signature_mismatch"}


class SessionAffinity:
    """Map sessions deterministically across healthy backends."""

    def choose(self, session_id: str, backends: Sequence[str]) -> dict[str, object]:
        """Return a stable backend using rendezvous-style hashing."""
        if not session_id or not backends:
            raise ValueError("session and backends are required")
        backend = max(
            sorted(set(backends)),
            key=lambda item: hashlib.sha256(f"{session_id}:{item}".encode()).digest(),
        )
        return {"backend": backend, "session_id": session_id}


class CircuitBreakerPolicy:
    """Determine circuit state from failures and cooldown."""

    def evaluate(
        self,
        failures: int,
        threshold: int,
        opened_at: int | None,
        now: int,
        cooldown: int,
    ) -> dict[str, object]:
        """Return closed, open, or half-open state."""
        if (
            any(
                isinstance(x, bool) or not isinstance(x, int) or x < 0
                for x in (failures, threshold, now, cooldown)
            )
            or threshold < 1
        ):
            raise ValueError("invalid circuit metrics")
        if failures < threshold:
            state = "closed"
        elif opened_at is not None and now - opened_at >= cooldown:
            state = "half_open"
        else:
            state = "open"
        return {"state": state, "probe_allowed": state == "half_open"}


class SemanticCacheKey:
    """Build privacy-preserving deterministic cache keys."""

    def build(self, text: str, model: str, namespace: str) -> dict[str, str]:
        """Hash normalized text with model and namespace, never returning text."""
        if not text.strip() or not model or not namespace:
            raise ValueError("text, model, and namespace are required")
        normalized = " ".join(text.lower().split())
        return {
            "key": hashlib.sha256(
                f"{namespace}:{model}:{normalized}".encode()
            ).hexdigest()
        }


class SensitiveDataRedactor:
    """Redact common secret and email patterns."""

    def redact(self, text: str) -> dict[str, object]:
        """Return redacted text and finding count."""
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        patterns = [
            re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b"),
            re.compile(r"\b(?:sk|gw)_[A-Za-z0-9_-]{8,}\b"),
        ]
        count = 0
        for pattern in patterns:
            text, found = pattern.subn("[REDACTED]", text)
            count += found
        return {"text": text, "findings": count}


class InjectionRiskScorer:
    """Score untrusted content for prompt-injection indicators."""

    def score(self, text: str) -> dict[str, object]:
        """Return bounded score and review decision."""
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        phrases = (
            "ignore previous",
            "system prompt",
            "developer message",
            "reveal secret",
            "disable safety",
        )
        hits = sum(phrase in text.lower() for phrase in phrases)
        score = min(1.0, hits / 3)
        return {"score": score, "review_required": score >= 1 / 3}


class HumanApprovalGate:
    """Require human approval for high-impact actions."""

    def decide(
        self, action: str, impact: str, approved_by: str | None
    ) -> dict[str, object]:
        """Allow low impact or explicitly approved high-impact actions."""
        if not action or impact not in {"low", "medium", "high"}:
            raise ValueError("valid action and impact are required")
        required = impact in {"medium", "high"}
        return {
            "allowed": not required or bool(approved_by),
            "approval_required": required,
        }


class AuditChain:
    """Create tamper-evident audit event hashes."""

    def append(self, previous_hash: str, event: Mapping[str, object]) -> dict[str, str]:
        """Hash canonical event content with the previous hash."""
        canonical = json.dumps(event, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(f"{previous_hash}:{canonical}".encode()).hexdigest()
        return {"previous_hash": previous_hash, "hash": digest}


class TraceSampler:
    """Make deterministic trace-sampling decisions."""

    def decide(
        self, trace_id: str, rate: float, force: bool = False
    ) -> dict[str, object]:
        """Sample by stable hash while supporting forced incident traces."""
        if not trace_id or not 0 <= rate <= 1:
            raise ValueError("trace_id and rate are invalid")
        bucket = int(hashlib.sha256(trace_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        return {"sampled": force or bucket < rate, "bucket": bucket}


class TaskCostMeter:
    """Calculate cost and unit economics for agent tasks."""

    def calculate(
        self,
        input_tokens: int,
        output_tokens: int,
        input_rate: float,
        output_rate: float,
        steps: int,
    ) -> dict[str, object]:
        """Return task cost and cost per step."""
        if (
            any(
                isinstance(x, bool) or not isinstance(x, int) or x < 0
                for x in (input_tokens, output_tokens, steps)
            )
            or steps < 1
        ):
            raise ValueError("token counts and steps are invalid")
        cost = input_tokens / 1_000_000 * _finite(
            input_rate, "input_rate"
        ) + output_tokens / 1_000_000 * _finite(output_rate, "output_rate")
        return {"cost": cost, "cost_per_step": cost / steps}


class TokenDensityMetric:
    """Measure useful output relative to consumed tokens."""

    def calculate(self, useful_units: float, total_tokens: int) -> dict[str, float]:
        """Return useful units per thousand tokens."""
        useful = _finite(useful_units, "useful_units")
        if (
            isinstance(total_tokens, bool)
            or not isinstance(total_tokens, int)
            or total_tokens < 1
        ):
            raise ValueError("total_tokens must be positive")
        return {"per_1k_tokens": useful * 1000 / total_tokens}


class CarbonEstimator:
    """Estimate operational emissions from energy and grid intensity."""

    def estimate(self, energy_kwh: float, grams_per_kwh: float) -> dict[str, float]:
        """Return estimated grams and kilograms CO2e."""
        grams = _finite(energy_kwh, "energy_kwh") * _finite(
            grams_per_kwh, "grams_per_kwh"
        )
        return {"grams_co2e": grams, "kilograms_co2e": grams / 1000}


class ChangeRiskAssessor:
    """Score release risk from scope, criticality, and test coverage."""

    def assess(
        self, changed_files: int, criticality: int, coverage: float
    ) -> dict[str, object]:
        """Return bounded risk and review tier."""
        if (
            any(
                isinstance(x, bool) or not isinstance(x, int) or x < 0
                for x in (changed_files, criticality)
            )
            or not 0 <= coverage <= 1
            or not 1 <= criticality <= 5
        ):
            raise ValueError("invalid change risk inputs")
        score = min(
            1.0, changed_files / 50 * 0.4 + criticality / 5 * 0.4 + (1 - coverage) * 0.2
        )
        return {
            "score": score,
            "tier": "high" if score >= 0.7 else "medium" if score >= 0.4 else "low",
        }


class SupportTriage:
    """Prioritize support incidents by severity and customer impact."""

    def prioritize(
        self, severity: int, affected_users: int, workaround: bool
    ) -> dict[str, object]:
        """Return P1-P4 priority."""
        if not 1 <= severity <= 4 or affected_users < 0:
            raise ValueError("invalid support incident")
        score = severity * 2 + min(4, affected_users // 100) + (0 if workaround else 2)
        return {
            "priority": "P1"
            if score >= 11
            else "P2"
            if score >= 8
            else "P3"
            if score >= 5
            else "P4"
        }


class LocaleNegotiator:
    """Negotiate supported locales from user preference order."""

    def choose(
        self, requested: Sequence[str], supported: Sequence[str], default: str
    ) -> dict[str, str]:
        """Return first exact or language-prefix match, otherwise default."""
        if default not in supported:
            raise ValueError("default locale must be supported")
        for item in requested:
            if item in supported:
                return {"locale": item}
            prefix = item.split("-")[0]
            match = next((x for x in supported if x.split("-")[0] == prefix), None)
            if match:
                return {"locale": match}
        return {"locale": default}


class ResidencyPolicy:
    """Authorize data movement between classified regions."""

    def decide(
        self, source: str, destination: str, allowed_pairs: Sequence[Sequence[str]]
    ) -> dict[str, object]:
        """Allow same-region flows or explicitly approved region pairs."""
        if not source or not destination:
            raise ValueError("source and destination are required")
        pairs = {(pair[0], pair[1]) for pair in allowed_pairs if len(pair) == 2}
        allowed = source == destination or (source, destination) in pairs
        return {
            "allowed": allowed,
            "reason": "compliant" if allowed else "residency_block",
        }
