"""Governed agent-fleet capabilities for identity, lifecycle, policy, cost, and evidence."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence


def _num(value: object, name: str, minimum: float = 0.0) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < minimum
    ):
        raise ValueError(f"{name} must be finite and >= {minimum}")
    return float(value)


class AgentIdentityCard:
    """Create a normalized identity card for a non-human worker."""

    def issue(
        self, agent_id: str, owner: str, purpose: str, expires_at: int
    ) -> dict[str, object]:
        """Return an accountable identity card with a stable fingerprint."""
        if (
            not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", agent_id)
            or not owner
            or not purpose
            or isinstance(expires_at, bool)
            or expires_at < 1
        ):
            raise ValueError("valid agent identity fields are required")
        raw = f"{agent_id}:{owner}:{purpose}:{expires_at}"
        return {
            "agent_id": agent_id,
            "owner": owner,
            "purpose": purpose,
            "expires_at": expires_at,
            "fingerprint": hashlib.sha256(raw.encode()).hexdigest(),
        }


class AgentInventory:
    """Summarize sanctioned and shadow agents."""

    def summarize(self, agents: Sequence[Mapping[str, object]]) -> dict[str, object]:
        """Return ownership, status, and shadow-agent counts."""
        if not agents:
            raise ValueError("agents are required")
        shadow = [
            str(a.get("id"))
            for a in agents
            if not a.get("owner") or a.get("sanctioned") is not True
        ]
        return {
            "total": len(agents),
            "shadow": sorted(shadow),
            "by_status": dict(Counter(str(a.get("status", "unknown")) for a in agents)),
        }


class LifecyclePolicy:
    """Evaluate activation and offboarding state transitions."""

    _allowed = {
        "draft": {"active", "retired"},
        "active": {"suspended", "retired"},
        "suspended": {"active", "retired"},
        "retired": set(),
    }

    def transition(self, current: str, proposed: str) -> dict[str, object]:
        """Permit only explicit lifecycle transitions."""
        if current not in self._allowed or proposed not in self._allowed[current]:
            raise ValueError("invalid lifecycle transition")
        return {"from": current, "to": proposed, "allowed": True}


class CredentialExpiry:
    """Determine credential renewal or revocation actions."""

    def evaluate(
        self, expires_at: int, now: int, renewal_window: int
    ) -> dict[str, object]:
        """Return valid, renew, or revoke action."""
        if any(
            isinstance(x, bool) or not isinstance(x, int) or x < 0
            for x in (expires_at, now, renewal_window)
        ):
            raise ValueError("invalid expiry inputs")
        action = (
            "revoke"
            if now >= expires_at
            else "renew"
            if expires_at - now <= renewal_window
            else "valid"
        )
        return {"action": action, "seconds_remaining": max(0, expires_at - now)}


class CapabilityGrant:
    """Authorize a scope- and time-bounded agent capability."""

    def decide(
        self,
        capability: str,
        granted: Sequence[str],
        resource: str,
        resources: Sequence[str],
        expires_at: int,
        now: int,
    ) -> dict[str, object]:
        """Require capability, resource scope, and unexpired grant."""
        allowed = (
            capability in granted
            and ("*" in resources or resource in resources)
            and now < expires_at
        )
        return {
            "allowed": allowed,
            "reason": "granted" if allowed else "capability_denied",
        }


class PlatformAuthorization:
    """Verify that an agent is permitted by an external platform."""

    def decide(
        self,
        platform: str,
        approved_platforms: Sequence[str],
        terms_version: str,
        accepted_versions: Mapping[str, str],
    ) -> dict[str, object]:
        """Require platform approval and exact accepted terms version."""
        allowed = (
            platform in approved_platforms
            and accepted_versions.get(platform) == terms_version
        )
        return {
            "allowed": allowed,
            "reason": "authorized" if allowed else "platform_not_authorized",
        }


class KillSwitch:
    """Evaluate emergency stop state across agent, team, and organization scopes."""

    def decide(
        self, agent: str, team: str, stopped: Sequence[str]
    ) -> dict[str, object]:
        """Stop when any relevant scope is present in the stop set."""
        scopes = {"organization", f"team:{team}", f"agent:{agent}"}
        hit = sorted(scopes.intersection(stopped))
        return {"allowed": not hit, "matched_stops": hit}


class PolicySimulation:
    """Compare current and proposed policy decisions without enforcement."""

    def compare(
        self, current: Sequence[bool], proposed: Sequence[bool]
    ) -> dict[str, object]:
        """Return newly blocked and newly allowed decision indexes."""
        if len(current) != len(proposed):
            raise ValueError("policy samples must align")
        return {
            "newly_blocked": [
                i
                for i, (a, b) in enumerate(zip(current, proposed, strict=True))
                if a and not b
            ],
            "newly_allowed": [
                i
                for i, (a, b) in enumerate(zip(current, proposed, strict=True))
                if not a and b
            ],
        }


class BlastRadiusEstimator:
    """Estimate worst-case impact from permissions and workload scale."""

    def estimate(
        self, users: int, write_systems: int, autonomy: int
    ) -> dict[str, object]:
        """Return bounded blast-radius score and tier."""
        if (
            any(
                isinstance(x, bool) or not isinstance(x, int) or x < 0
                for x in (users, write_systems, autonomy)
            )
            or autonomy > 5
        ):
            raise ValueError("invalid blast-radius input")
        score = min(
            1.0, users / 10000 * 0.4 + write_systems / 20 * 0.35 + autonomy / 5 * 0.25
        )
        return {
            "score": score,
            "tier": "critical"
            if score >= 0.8
            else "high"
            if score >= 0.55
            else "medium"
            if score >= 0.3
            else "low",
        }


class HumanResponsibility:
    """Resolve the accountable human for an agent action."""

    def resolve(
        self, agent_owner: str, workflow_owner: str, approver: str | None
    ) -> dict[str, str]:
        """Prefer explicit approver, then workflow owner, then agent owner."""
        owner = approver or workflow_owner or agent_owner
        if not owner:
            raise ValueError("an accountable human is required")
        return {"accountable": owner}


class EvidenceBundle:
    """Build a canonical evidence manifest for an agent decision."""

    def build(self, artifacts: Mapping[str, str]) -> dict[str, object]:
        """Return sorted artifact hashes and aggregate digest."""
        if not artifacts or any(not k or ".." in k.split("/") for k in artifacts):
            raise ValueError("safe evidence artifacts are required")
        hashes = {
            k: hashlib.sha256(v.encode()).hexdigest()
            for k, v in sorted(artifacts.items())
        }
        return {
            "artifacts": hashes,
            "digest": hashlib.sha256(
                json.dumps(hashes, sort_keys=True).encode()
            ).hexdigest(),
        }


class PolicyCoverage:
    """Calculate how much of an agent fleet is covered by policy."""

    def calculate(self, total: int, governed: int, observed: int) -> dict[str, float]:
        """Return governance and observability coverage ratios."""
        if (
            isinstance(total, bool)
            or total < 1
            or not 0 <= governed <= total
            or not 0 <= observed <= total
        ):
            raise ValueError("invalid coverage counts")
        return {
            "governance_coverage": governed / total,
            "observability_coverage": observed / total,
        }


class ShadowAgentDetector:
    """Detect unknown agents by comparing observed and registered identities."""

    def detect(
        self, observed: Sequence[str], registered: Sequence[str]
    ) -> dict[str, object]:
        """Return unknown and inactive registered identities."""
        return {
            "unknown": sorted(set(observed) - set(registered)),
            "inactive": sorted(set(registered) - set(observed)),
        }


class CostCeiling:
    """Enforce workflow cost ceilings before additional work."""

    def decide(
        self, spent: float, estimated_next: float, ceiling: float
    ) -> dict[str, object]:
        """Return projected cost and whether work may continue."""
        values = [
            _num(spent, "spent"),
            _num(estimated_next, "estimated_next"),
            _num(ceiling, "ceiling"),
        ]
        projected = values[0] + values[1]
        return {
            "allowed": projected <= values[2],
            "projected": projected,
            "remaining": max(0.0, values[2] - values[0]),
        }


class RunawayDetector:
    """Detect fan-out, retry, and tool-thrashing behavior."""

    def detect(
        self,
        steps: int,
        retries: int,
        repeated_tool_calls: int,
        limits: Mapping[str, int],
    ) -> dict[str, object]:
        """Return triggered runaway indicators."""
        metrics = {
            "steps": steps,
            "retries": retries,
            "repeated_tool_calls": repeated_tool_calls,
        }
        if any(
            isinstance(v, bool) or not isinstance(v, int) or v < 0
            for v in metrics.values()
        ) or any(k not in limits for k in metrics):
            raise ValueError("valid metrics and limits are required")
        hits = sorted(k for k, v in metrics.items() if v > limits[k])
        return {"runaway": bool(hits), "indicators": hits}


class OutcomeEconomics:
    """Calculate agent cost relative to completed business value."""

    def calculate(
        self, total_cost: float, completed_outcomes: int, value_per_outcome: float
    ) -> dict[str, object]:
        """Return cost per outcome, value, and ROI."""
        cost = _num(total_cost, "total_cost")
        value_rate = _num(value_per_outcome, "value_per_outcome")
        if (
            isinstance(completed_outcomes, bool)
            or not isinstance(completed_outcomes, int)
            or completed_outcomes < 1
        ):
            raise ValueError("completed_outcomes must be positive")
        value = completed_outcomes * value_rate
        return {
            "cost_per_outcome": cost / completed_outcomes,
            "value": value,
            "roi": (value - cost) / cost if cost else math.inf,
        }


class ModelTierPolicy:
    """Choose the cheapest permitted model tier for task complexity."""

    def choose(
        self, complexity: float, tiers: Sequence[Mapping[str, object]]
    ) -> dict[str, object]:
        """Select the lowest-cost tier meeting the measured complexity."""
        complexity = _num(complexity, "complexity")
        eligible = [
            t
            for t in tiers
            if _num(t.get("max_complexity"), "max_complexity") >= complexity
        ]
        if not eligible:
            raise ValueError("no model tier can satisfy complexity")
        selected = min(
            eligible, key=lambda t: (_num(t.get("cost"), "cost"), str(t.get("name")))
        )
        return {"tier": selected["name"], "cost": selected["cost"]}


class ToolCostLedger:
    """Aggregate priced tool calls into an audit-friendly ledger."""

    def aggregate(self, calls: Sequence[Mapping[str, object]]) -> dict[str, object]:
        """Return total and by-tool cost."""
        by_tool: dict[str, float] = {}
        for call in calls:
            tool = str(call.get("tool", ""))
            cost = _num(call.get("cost"), "cost")
            if not tool:
                raise ValueError("tool name is required")
            by_tool[tool] = by_tool.get(tool, 0.0) + cost
        return {
            "total": sum(by_tool.values()),
            "by_tool": dict(sorted(by_tool.items())),
        }


class DataReadiness:
    """Score whether trusted enterprise content is ready for agent use."""

    def assess(
        self, fresh: float, permissioned: float, classified: float
    ) -> dict[str, object]:
        """Return readiness score and blocking dimensions."""
        values = {
            "fresh": fresh,
            "permissioned": permissioned,
            "classified": classified,
        }
        if any(not 0 <= _num(v, k) <= 1 for k, v in values.items()):
            raise ValueError("readiness dimensions must be between zero and one")
        blockers = sorted(k for k, v in values.items() if v < 0.8)
        return {
            "score": sum(values.values()) / 3,
            "ready": not blockers,
            "blockers": blockers,
        }


class ReproducibilityRecord:
    """Create a stable fingerprint of an agent run configuration."""

    def build(
        self, prompt_version: str, model: str, tools: Sequence[str], policy_version: str
    ) -> dict[str, object]:
        """Return normalized configuration and fingerprint."""
        if not all((prompt_version, model, policy_version)):
            raise ValueError("run versions are required")
        record = {
            "prompt_version": prompt_version,
            "model": model,
            "tools": sorted(set(tools)),
            "policy_version": policy_version,
        }
        return {
            **record,
            "fingerprint": hashlib.sha256(
                json.dumps(record, sort_keys=True).encode()
            ).hexdigest(),
        }


class ComplianceCrosswalk:
    """Map implemented controls to framework requirements."""

    def evaluate(
        self, requirements: Mapping[str, Sequence[str]], controls: Sequence[str]
    ) -> dict[str, object]:
        """Return satisfied and missing requirements."""
        available = set(controls)
        missing = {
            name: sorted(set(required) - available)
            for name, required in requirements.items()
            if set(required) - available
        }
        return {"compliant": not missing, "missing": missing}
