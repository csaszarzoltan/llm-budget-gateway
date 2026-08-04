"""Research-ranked P0 product services for the Unified Console."""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RunLimits:
    """Hard, pre-step ceilings for one agent run."""

    max_cost_usd: float
    max_tokens: int
    max_tool_calls: int
    max_depth: int
    max_elapsed_seconds: int
    max_retries: int


@dataclass(frozen=True)
class RunState:
    """Measured consumption for one agent run."""

    run_id: str
    cost_usd: float
    tokens: int
    tool_calls: int
    depth: int
    elapsed_seconds: int
    retries: int
    emergency_stop: bool = False


@dataclass(frozen=True)
class FirewallDecision:
    """Explainable allow/block result from the runaway firewall."""

    allowed: bool
    code: str
    explanation: str
    next_action: str


class RunawayFirewall:
    """Fail closed before a run exceeds any configured resource ceiling."""

    def evaluate(self, state: RunState, limits: RunLimits) -> FirewallDecision:
        """Evaluate current usage against all limits in deterministic order."""
        measured = (
            state.cost_usd,
            state.tokens,
            state.tool_calls,
            state.depth,
            state.elapsed_seconds,
            state.retries,
        )
        configured = (
            limits.max_cost_usd,
            limits.max_tokens,
            limits.max_tool_calls,
            limits.max_depth,
            limits.max_elapsed_seconds,
            limits.max_retries,
        )
        if not state.run_id.strip():
            raise ValueError("run_id must be non-empty")
        if any(
            not math.isfinite(float(value)) or value < 0
            for value in (*measured, *configured)
        ):
            raise ValueError("run state and limits must be finite and non-negative")
        if state.emergency_stop:
            return FirewallDecision(
                False,
                "emergency_stop",
                "The organization emergency stop is active.",
                "Review the incident and explicitly clear the stop before resuming.",
            )
        checks = (
            (
                state.cost_usd >= limits.max_cost_usd,
                "cost_limit",
                f"Run cost ${state.cost_usd:.4f} reached the ${limits.max_cost_usd:.4f} ceiling.",
            ),
            (
                state.tokens >= limits.max_tokens,
                "token_limit",
                f"Run tokens {state.tokens} reached the {limits.max_tokens} ceiling.",
            ),
            (
                state.tool_calls >= limits.max_tool_calls,
                "tool_call_limit",
                f"Tool calls {state.tool_calls} reached the {limits.max_tool_calls} ceiling.",
            ),
            (
                state.depth >= limits.max_depth,
                "depth_limit",
                f"Delegation depth {state.depth} reached the {limits.max_depth} ceiling.",
            ),
            (
                state.elapsed_seconds >= limits.max_elapsed_seconds,
                "elapsed_limit",
                f"Elapsed time {state.elapsed_seconds}s reached the {limits.max_elapsed_seconds}s ceiling.",
            ),
            (
                state.retries >= limits.max_retries,
                "retry_limit",
                f"Retries {state.retries} reached the {limits.max_retries} ceiling.",
            ),
        )
        for blocked, code, explanation in checks:
            if blocked:
                return FirewallDecision(
                    False,
                    code,
                    explanation,
                    "Inspect the run trace, then raise the limit or end the run.",
                )
        return FirewallDecision(
            True,
            "allowed",
            "All run resources remain below their configured ceilings.",
            "Continue with the next step.",
        )


class RunawayLedger:
    """SQLite reservation and reconciliation store for agent run consumption."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._connection.execute("""CREATE TABLE IF NOT EXISTS agent_run_reservations (
            run_id TEXT PRIMARY KEY, cost_usd REAL NOT NULL, tokens INTEGER NOT NULL,
            tool_calls INTEGER NOT NULL, depth INTEGER NOT NULL, elapsed_seconds INTEGER NOT NULL,
            retries INTEGER NOT NULL, emergency_stop INTEGER NOT NULL DEFAULT 0, limits_json TEXT NOT NULL)""")
        self._connection.commit()

    def reserve(self, run_id: str, limits: RunLimits) -> RunState:
        """Create a zero-consumption reservation, rejecting duplicate run IDs."""
        import json

        if not run_id.strip():
            raise ValueError("run_id must be non-empty")
        try:
            self._connection.execute(
                "INSERT INTO agent_run_reservations VALUES (?,0,0,0,0,0,0,0,?)",
                (run_id, json.dumps(asdict(limits), sort_keys=True)),
            )
            self._connection.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"run {run_id} already exists") from exc
        return self.get(run_id)

    def reconcile(
        self,
        run_id: str,
        *,
        cost_usd: float,
        tokens: int,
        tool_calls: int,
        depth: int,
        elapsed_seconds: int,
        retries: int,
    ) -> RunState:
        """Atomically replace measured consumption for an existing reservation."""
        values = (cost_usd, tokens, tool_calls, depth, elapsed_seconds, retries)
        if any(not math.isfinite(float(value)) or value < 0 for value in values):
            raise ValueError("reconciliation values must be finite and non-negative")
        cursor = self._connection.execute(
            "UPDATE agent_run_reservations SET cost_usd=?,tokens=?,tool_calls=?,depth=?,elapsed_seconds=?,retries=? WHERE run_id=?",
            (*values, run_id),
        )
        if cursor.rowcount == 0:
            raise KeyError(run_id)
        self._connection.commit()
        return self.get(run_id)

    def get(self, run_id: str) -> RunState:
        """Return one recorded run or raise ``KeyError``."""
        row = self._connection.execute(
            "SELECT run_id,cost_usd,tokens,tool_calls,depth,elapsed_seconds,retries,emergency_stop FROM agent_run_reservations WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return RunState(
            str(row[0]),
            float(row[1]),
            int(row[2]),
            int(row[3]),
            int(row[4]),
            int(row[5]),
            int(row[6]),
            bool(row[7]),
        )


class CockpitService:
    """Normalize cross-workspace health into one role-ready cockpit summary."""

    def summarize(
        self,
        *,
        spend: Mapping[str, Any],
        quality: Mapping[str, Any],
        operations: Mapping[str, Any],
        governance: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Return normalized metrics and severity-ranked recommended actions."""
        current = _number(spend.get("current", 0), "current spend")
        budget = _number(spend.get("budget", 1), "budget")
        if budget <= 0:
            raise ValueError("budget must be greater than zero")
        score = _number(quality.get("score", 1), "quality score")
        minimum = _number(quality.get("minimum", 0), "minimum quality")
        incidents = int(_number(operations.get("incidents", 0), "incidents"))
        failing = int(_number(operations.get("failing_models", 0), "failing models"))
        approvals = int(
            _number(governance.get("pending_approvals", 0), "pending approvals")
        )
        coverage = _number(governance.get("policy_coverage", 1), "policy coverage")
        if (
            not 0 <= score <= 1
            or not 0 <= minimum <= 1
            or not 0 <= coverage <= 1
            or min(current, incidents, failing, approvals) < 0
        ):
            raise ValueError("metrics are outside their allowed range")
        actions: list[dict[str, str]] = []
        if current >= budget:
            actions.append(
                _action(
                    "budget",
                    "critical",
                    "Spend is over budget",
                    "Open FinOps and apply a safe ceiling.",
                )
            )
        if score < minimum:
            actions.append(
                _action(
                    "quality",
                    "critical",
                    "Quality is below the release floor",
                    "Inspect evaluations and pause unsafe rollout.",
                )
            )
        if incidents or failing:
            actions.append(
                _action(
                    "incident",
                    "critical" if incidents else "warning",
                    "Active operational degradation",
                    "Open the incident trace and review fallback health.",
                )
            )
        if coverage < 1:
            actions.append(
                _action(
                    "policy",
                    "warning",
                    "Policy coverage is incomplete",
                    "Review uncovered tools and models.",
                )
            )
        if approvals:
            actions.append(
                _action(
                    "approval",
                    "warning",
                    f"{approvals} approval(s) need attention",
                    "Open the approval inbox.",
                )
            )
        rank = {"critical": 0, "warning": 1, "info": 2}
        actions.sort(key=lambda item: (rank[item["severity"]], item["kind"]))
        status = (
            "critical"
            if any(x["severity"] == "critical" for x in actions)
            else "attention"
            if actions
            else "healthy"
        )
        return {
            "status": status,
            "metrics": [
                {
                    "id": "spend",
                    "label": "Spend",
                    "value": current,
                    "target": budget,
                    "unit": "USD",
                },
                {
                    "id": "quality",
                    "label": "Quality",
                    "value": score,
                    "target": minimum,
                    "unit": "score",
                },
                {
                    "id": "incidents",
                    "label": "Incidents",
                    "value": incidents,
                    "target": 0,
                    "unit": "count",
                },
                {
                    "id": "coverage",
                    "label": "Policy coverage",
                    "value": coverage,
                    "target": 1,
                    "unit": "ratio",
                },
            ],
            "actions": actions,
        }


class SchemaFormService:
    """Convert a bounded JSON Schema object into safe UI control metadata."""

    def generate(self, form_id: str, schema: Mapping[str, Any]) -> dict[str, Any]:
        """Generate ordered controls for common scalar and array schema fields."""
        if schema.get("type", "object") != "object" or not isinstance(
            schema.get("properties", {}), Mapping
        ):
            raise ValueError("form schema root must be an object")
        required = set(schema.get("required", []))
        controls = []
        for name, raw in schema.get("properties", {}).items():
            if not isinstance(raw, Mapping):
                raise ValueError(f"property {name} must be a schema object")
            field_type = str(raw.get("type", "string"))
            widget = (
                "select"
                if raw.get("enum")
                else {
                    "boolean": "checkbox",
                    "integer": "number",
                    "number": "number",
                    "array": "list",
                    "object": "json",
                }.get(field_type, "text")
            )
            sensitive = any(
                token in str(name).casefold()
                for token in (
                    "secret",
                    "token",
                    "password",
                    "api_key",
                    "apikey",
                    "credential",
                )
            )
            help_text = str(raw.get("description", ""))
            if sensitive:
                help_text = (
                    help_text + " Sensitive values are never persisted by this form."
                ).strip()
            controls.append(
                {
                    "name": str(name),
                    "label": str(raw.get("title", str(name).replace("_", " ").title())),
                    "type": field_type,
                    "widget": widget,
                    "required": name in required,
                    "default": raw.get("default"),
                    "options": list(raw.get("enum", [])),
                    "minimum": raw.get("minimum"),
                    "maximum": raw.get("maximum"),
                    "help": help_text,
                    "sensitive": sensitive,
                }
            )
        return {
            "id": form_id,
            "title": str(schema.get("title", form_id.replace("-", " ").title())),
            "description": str(schema.get("description", "")),
            "controls": controls,
        }


def _number(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _action(
    kind: str, severity: str, title: str, recommendation: str
) -> dict[str, str]:
    return {
        "kind": kind,
        "severity": severity,
        "title": title,
        "recommendation": recommendation,
    }
