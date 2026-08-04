"""Tenant-isolated OpenTelemetry/OpenInference evidence export."""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from typing import Any

_ID = re.compile(r"^[0-9a-f]+$")
_SENSITIVE_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "input.value",
    "output.value",
    "llm.input_messages",
    "llm.output_messages",
}
_KIND = {
    "model": "LLM",
    "tool": "TOOL",
    "agent": "AGENT",
    "gateway": "CHAIN",
    "policy": "GUARDRAIL",
    "budget": "CHAIN",
}


@dataclass(frozen=True)
class EvidenceEvent:
    """One normalized span carrying operational evidence without raw content."""

    tenant_id: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    kind: str
    name: str
    started_at_ns: int
    ended_at_ns: int
    status: str
    attributes: dict[str, Any]
    metrics: dict[str, float | int]


class EvidencePlane:
    """Persist tenant-scoped evidence and export OTLP-shaped trace documents."""

    def __init__(
        self, connection: sqlite3.Connection, now_fn: Callable[[], int] | None = None
    ) -> None:
        """Initialize storage with an injectable clock for deterministic tests."""
        self.connection = connection
        self.now_fn = now_fn or (lambda: int(time.time()))
        connection.execute("""CREATE TABLE IF NOT EXISTS evidence_spans(
            tenant_id TEXT NOT NULL, trace_id TEXT NOT NULL, span_id TEXT NOT NULL,
            parent_span_id TEXT, kind TEXT NOT NULL, name TEXT NOT NULL,
            started_at_ns INTEGER NOT NULL, ended_at_ns INTEGER NOT NULL,
            status TEXT NOT NULL, attributes_json TEXT NOT NULL, metrics_json TEXT NOT NULL,
            recorded_at INTEGER NOT NULL, PRIMARY KEY(tenant_id,trace_id,span_id))""")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_evidence_trace ON evidence_spans(tenant_id,trace_id,started_at_ns,span_id)"
        )
        connection.commit()

    def record(self, event: EvidenceEvent) -> EvidenceEvent:
        """Validate, redact, and idempotently persist one evidence span."""
        self._validate(event)
        safe = replace(event, attributes=self._redact(event.attributes))
        self.connection.execute(
            "INSERT INTO evidence_spans VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(tenant_id,trace_id,span_id) DO UPDATE SET parent_span_id=excluded.parent_span_id,kind=excluded.kind,name=excluded.name,started_at_ns=excluded.started_at_ns,ended_at_ns=excluded.ended_at_ns,status=excluded.status,attributes_json=excluded.attributes_json,metrics_json=excluded.metrics_json,recorded_at=excluded.recorded_at",
            (
                safe.tenant_id,
                safe.trace_id,
                safe.span_id,
                safe.parent_span_id,
                safe.kind,
                safe.name,
                safe.started_at_ns,
                safe.ended_at_ns,
                safe.status,
                json.dumps(safe.attributes, sort_keys=True, separators=(",", ":")),
                json.dumps(safe.metrics, sort_keys=True, separators=(",", ":")),
                self.now_fn(),
            ),
        )
        self.connection.commit()
        return safe

    def list_trace(self, *, tenant_id: str, trace_id: str) -> list[EvidenceEvent]:
        """Return a tenant's trace in deterministic start-time order."""
        rows = self.connection.execute(
            "SELECT tenant_id,trace_id,span_id,parent_span_id,kind,name,started_at_ns,ended_at_ns,status,attributes_json,metrics_json FROM evidence_spans WHERE tenant_id=? AND trace_id=? ORDER BY started_at_ns,span_id",
            (tenant_id, trace_id),
        ).fetchall()
        return [
            EvidenceEvent(
                str(r[0]),
                str(r[1]),
                str(r[2]),
                None if r[3] is None else str(r[3]),
                str(r[4]),
                str(r[5]),
                int(r[6]),
                int(r[7]),
                str(r[8]),
                json.loads(r[9]),
                json.loads(r[10]),
            )
            for r in rows
        ]

    def export_trace(self, *, tenant_id: str, trace_id: str) -> dict[str, Any]:
        """Export one trace as an OTLP-shaped document with OpenInference attributes."""
        events = self.list_trace(tenant_id=tenant_id, trace_id=trace_id)
        if not events:
            raise KeyError(trace_id)
        spans = []
        for event in events:
            attrs = {
                **event.attributes,
                **event.metrics,
                "openinference.span.kind": _KIND.get(event.kind, event.kind.upper()),
            }
            spans.append(
                {
                    "traceId": event.trace_id,
                    "spanId": event.span_id,
                    "parentSpanId": event.parent_span_id or "",
                    "name": event.name,
                    "kind": event.kind,
                    "startTimeUnixNano": str(event.started_at_ns),
                    "endTimeUnixNano": str(event.ended_at_ns),
                    "status": {
                        "code": "STATUS_CODE_OK"
                        if event.status == "ok"
                        else "STATUS_CODE_ERROR"
                    },
                    "attributes": attrs,
                }
            )
        return {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": {
                            "service.name": "llm-budget-gateway",
                            "tenant.id": tenant_id,
                        }
                    },
                    "scopeSpans": [
                        {
                            "scope": {
                                "name": "llm_budget_gateway.evidence",
                                "version": "13.6.0",
                            },
                            "spans": spans,
                        }
                    ],
                }
            ]
        }

    def export_jsonl(self, *, tenant_id: str, trace_id: str) -> str:
        """Export canonical JSON Lines for portable offline ingestion."""
        return "\n".join(
            json.dumps(asdict(event), sort_keys=True, separators=(",", ":"))
            for event in self.list_trace(tenant_id=tenant_id, trace_id=trace_id)
        )

    @staticmethod
    def _validate(event: EvidenceEvent) -> None:
        if not event.tenant_id.strip() or not event.name.strip():
            raise ValueError("tenant_id and name must be non-empty")
        if len(event.trace_id) != 32 or not _ID.fullmatch(event.trace_id):
            raise ValueError("trace_id must be 32 lowercase hexadecimal characters")
        if len(event.span_id) != 16 or not _ID.fullmatch(event.span_id):
            raise ValueError("span_id must be 16 lowercase hexadecimal characters")
        if event.parent_span_id is not None and (
            len(event.parent_span_id) != 16 or not _ID.fullmatch(event.parent_span_id)
        ):
            raise ValueError(
                "parent_span_id must be 16 lowercase hexadecimal characters"
            )
        if event.started_at_ns < 0 or event.ended_at_ns < event.started_at_ns:
            raise ValueError(
                "ended_at_ns must be greater than or equal to started_at_ns"
            )
        if event.status not in {"ok", "error"}:
            raise ValueError("status must be ok or error")
        if any(not math.isfinite(float(value)) for value in event.metrics.values()):
            raise ValueError("metrics must contain finite numeric values")

    @classmethod
    def _redact(cls, value: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in value.items():
            lowered = key.lower()
            if lowered in _SENSITIVE_KEYS or any(
                word in lowered for word in ("secret", "password", "authorization")
            ):
                result[key] = "[REDACTED]"
            elif isinstance(item, Mapping):
                result[key] = cls._redact(item)
            elif isinstance(item, list):
                result[key] = [
                    cls._redact(v) if isinstance(v, Mapping) else v for v in item
                ]
            else:
                result[key] = item
        return result
