"""Research-ranked provider compatibility and incident explanation workflows."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class CompatibilityProbe:
    """One bounded provider capability check."""

    capability: str
    passed: bool
    latency_ms: int
    detail: str = ""


@dataclass(frozen=True)
class RepairAction:
    """A concrete repair for one failed provider capability."""

    capability: str
    action: str
    evidence: str


@dataclass(frozen=True)
class CompatibilityResult:
    """Provider readiness score with ordered repair actions."""

    provider_id: str
    status: str
    score: int
    passed: int
    total: int
    probes: tuple[CompatibilityProbe, ...]
    repairs: tuple[RepairAction, ...]


class ProviderCompatibilityLab:
    """Score provider capabilities and turn failures into repair instructions."""

    def evaluate(
        self, *, provider_id: str, probes: list[CompatibilityProbe]
    ) -> CompatibilityResult:
        """Validate and summarize a complete provider probe run."""
        if not provider_id.strip():
            raise ValueError("provider_id must be non-empty")
        if not probes:
            raise ValueError("at least one compatibility probe is required")
        capabilities = [probe.capability.strip() for probe in probes]
        if any(not capability for capability in capabilities):
            raise ValueError("probe capability must be non-empty")
        if len(set(capabilities)) != len(capabilities):
            raise ValueError("duplicate probe capability")
        if any(probe.latency_ms < 0 for probe in probes):
            raise ValueError("probe latency must be non-negative")
        passed = sum(probe.passed for probe in probes)
        score = round(passed * 100 / len(probes))
        failed = [probe for probe in probes if not probe.passed]
        status = (
            "ready"
            if not failed
            else "blocked"
            if capabilities[0] == "authentication" and not probes[0].passed
            else "degraded"
        )
        repairs = tuple(self._repair(probe) for probe in failed)
        return CompatibilityResult(
            provider_id.strip(),
            status,
            score,
            passed,
            len(probes),
            tuple(probes),
            repairs,
        )

    def _repair(self, probe: CompatibilityProbe) -> RepairAction:
        capability = probe.capability.lower()
        actions = {
            "authentication": "Verify the stored credential, account permissions, and provider authentication header.",
            "model_discovery": "Verify the model-list path and that the credential can list models.",
            "streaming": "Verify the base URL includes the provider's /v1 path and that chat streaming is enabled.",
            "tools": "Select a tool-capable model and verify tool_choice and tool schema support.",
            "structured_output": "Select a model supporting JSON schema and verify response_format compatibility.",
            "embeddings": "Select an embedding model and verify the embeddings endpoint path.",
            "vision": "Select a vision-capable model and verify image input format and size.",
        }
        return RepairAction(
            probe.capability,
            actions.get(
                capability,
                "Review the provider response and update this capability's connection settings.",
            ),
            probe.detail or "Probe did not pass.",
        )


@dataclass(frozen=True)
class IncidentEvidence:
    """One privacy-safe fact in an incident decision timeline."""

    incident_id: str
    timestamp: int
    kind: str
    outcome: str
    summary: str
    severity: str
    details: dict[str, Any]


class IncidentTimelineStore:
    """Persist, redact, and explain chronological incident evidence."""

    _SEVERITIES = {"info", "warning", "critical"}
    _SECRET_KEYS = {"authorization", "api_key", "token", "secret", "password"}

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Initialize the SQLite incident evidence store."""
        self._connection = connection
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS incident_evidence (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT NOT NULL,
            timestamp INTEGER NOT NULL, kind TEXT NOT NULL, outcome TEXT NOT NULL,
            summary TEXT NOT NULL, severity TEXT NOT NULL, details_json TEXT NOT NULL)"""
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_incident_evidence ON incident_evidence(incident_id,timestamp,event_id)"
        )
        self._connection.commit()

    def append(self, evidence: IncidentEvidence) -> IncidentEvidence:
        """Validate, redact, and append one evidence event."""
        if not evidence.incident_id.strip() or not evidence.kind.strip():
            raise ValueError("incident_id and kind must be non-empty")
        if evidence.timestamp < 0:
            raise ValueError("timestamp must be non-negative")
        if evidence.severity not in self._SEVERITIES:
            raise ValueError("severity must be info, warning, or critical")
        safe = IncidentEvidence(
            evidence.incident_id.strip(),
            evidence.timestamp,
            evidence.kind.strip(),
            evidence.outcome.strip(),
            self._redact_text(evidence.summary),
            evidence.severity,
            self._redact_details(evidence.details),
        )
        self._connection.execute(
            "INSERT INTO incident_evidence(incident_id,timestamp,kind,outcome,summary,severity,details_json) VALUES(?,?,?,?,?,?,?)",
            (
                safe.incident_id,
                safe.timestamp,
                safe.kind,
                safe.outcome,
                safe.summary,
                safe.severity,
                json.dumps(safe.details, sort_keys=True),
            ),
        )
        self._connection.commit()
        return safe

    def explain(self, incident_id: str) -> dict[str, Any]:
        """Return ordered evidence with the incident impact and concrete repair."""
        rows = self._connection.execute(
            "SELECT incident_id,timestamp,kind,outcome,summary,severity,details_json FROM incident_evidence WHERE incident_id=? ORDER BY timestamp,event_id",
            (incident_id,),
        ).fetchall()
        if not rows:
            raise KeyError(incident_id)
        events = [
            IncidentEvidence(
                str(r[0]),
                int(r[1]),
                str(r[2]),
                str(r[3]),
                str(r[4]),
                str(r[5]),
                json.loads(r[6]),
            )
            for r in rows
        ]
        critical = next(
            (event for event in events if event.severity == "critical"), events[-1]
        )
        status = (
            "recovered"
            if any(event.outcome == "recovered" for event in events)
            else "active"
        )
        return {
            "incident_id": incident_id,
            "status": status,
            "impact": critical.summary,
            "why": f"{critical.kind.title()} evidence: {critical.summary}",
            "fix": self._fix(events),
            "timeline": [asdict(event) for event in events],
        }

    def _fix(self, events: list[IncidentEvidence]) -> str:
        text = " ".join(f"{event.kind} {event.summary}" for event in events).lower()
        if "429" in text or "rate" in text:
            return "Review provider rate limits, reduce concurrency, and keep the healthy fallback route enabled."
        if "budget" in text or "cost" in text or "412" in text:
            return "Review the application budget, stop runaway work, then raise the budget only with an approved justification."
        if "auth" in text or "401" in text or "403" in text:
            return "Rotate or verify the provider credential and retest the connection before replaying traffic."
        if "timeout" in text or "5" in text:
            return "Check provider health and latency, then route to a healthy fallback or adjust the bounded timeout."
        return "Inspect the linked route, provider, policy, and trace evidence before retrying the request."

    def _redact_details(self, details: dict[str, Any]) -> dict[str, Any]:
        return {
            key: "[REDACTED]"
            if key.lower() in self._SECRET_KEYS
            else self._redact_value(value)
            for key, value in details.items()
        }

    def _redact_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._redact_text(value)
        if isinstance(value, list):
            return [self._redact_value(item) for item in value]
        if isinstance(value, dict):
            return self._redact_details(value)
        return value

    @staticmethod
    def _redact_text(value: str) -> str:
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{16,}\b", "[REDACTED]", value)
        return re.sub(r"\bBearer\s+\S+", "[REDACTED]", value, flags=re.IGNORECASE)
