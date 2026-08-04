"""Tenant-isolated agent traces and cost-to-outcome analytics."""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TraceSpan:
    """One privacy-safe span in an agent execution trace."""

    span_id: str
    run_id: str
    tenant_id: str
    parent_span_id: str | None
    kind: str
    name: str
    started_ms: int
    ended_ms: int
    cost_usd: float
    status: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class OutcomeRecord:
    """Cost, quality and success evidence for one product outcome."""

    record_id: str
    tenant_id: str
    feature: str
    project: str
    model: str
    tool: str
    cost_usd: float
    quality_score: float
    succeeded: bool


class TraceStore:
    """Persist tenant-isolated trace spans without prompt or response content."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        connection.execute("""CREATE TABLE IF NOT EXISTS agent_trace_spans (
          tenant_id TEXT NOT NULL, run_id TEXT NOT NULL, span_id TEXT NOT NULL,
          parent_span_id TEXT, kind TEXT NOT NULL, name TEXT NOT NULL,
          started_ms INTEGER NOT NULL, ended_ms INTEGER NOT NULL, cost_usd REAL NOT NULL,
          status TEXT NOT NULL, metadata_json TEXT NOT NULL, PRIMARY KEY(tenant_id,run_id,span_id))""")
        connection.commit()

    def append(self, span: TraceSpan) -> TraceSpan:
        """Validate and append one span, requiring an existing in-run parent."""
        if not all(
            (
                span.span_id.strip(),
                span.run_id.strip(),
                span.tenant_id.strip(),
                span.name.strip(),
            )
        ):
            raise ValueError("span, run, tenant and name must be non-empty")
        if span.ended_ms < span.started_ms:
            raise ValueError("span duration cannot be negative")
        if not math.isfinite(span.cost_usd) or span.cost_usd < 0:
            raise ValueError("span cost must be finite and non-negative")
        if (
            span.parent_span_id
            and not self._connection.execute(
                "SELECT 1 FROM agent_trace_spans WHERE tenant_id=? AND run_id=? AND span_id=?",
                (span.tenant_id, span.run_id, span.parent_span_id),
            ).fetchone()
        ):
            raise ValueError("parent span must exist in the same tenant and run")
        safe = {
            k: v
            for k, v in span.metadata.items()
            if k.casefold()
            not in {"prompt", "response", "authorization", "secret", "api_key"}
        }
        try:
            self._connection.execute(
                "INSERT INTO agent_trace_spans VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    span.tenant_id,
                    span.run_id,
                    span.span_id,
                    span.parent_span_id,
                    span.kind,
                    span.name,
                    span.started_ms,
                    span.ended_ms,
                    span.cost_usd,
                    span.status,
                    json.dumps(safe, sort_keys=True, default=str),
                ),
            )
            self._connection.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("span already exists") from exc
        return span

    def trace(self, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
        """Return a nested, privacy-safe tree for one tenant run."""
        rows = self._connection.execute(
            "SELECT span_id,parent_span_id,kind,name,started_ms,ended_ms,cost_usd,status FROM agent_trace_spans WHERE tenant_id=? AND run_id=? ORDER BY started_ms,span_id",
            (tenant_id, run_id),
        ).fetchall()
        if not rows:
            raise KeyError(run_id)
        nodes = {
            str(r[0]): {
                "span_id": str(r[0]),
                "parent_span_id": r[1],
                "kind": str(r[2]),
                "name": str(r[3]),
                "started_ms": int(r[4]),
                "ended_ms": int(r[5]),
                "duration_ms": int(r[5]) - int(r[4]),
                "cost_usd": float(r[6]),
                "status": str(r[7]),
                "children": [],
            }
            for r in rows
        }
        roots = []
        for node in nodes.values():
            parent = node["parent_span_id"]
            if parent is None:
                roots.append(node)
            else:
                nodes[str(parent)]["children"].append(node)
        return roots

    def list_runs(self, tenant_id: str) -> list[dict[str, Any]]:
        """List tenant runs with aggregate duration, cost and span count."""
        rows = self._connection.execute(
            "SELECT run_id,COUNT(*),MIN(started_ms),MAX(ended_ms),SUM(cost_usd) FROM agent_trace_spans WHERE tenant_id=? GROUP BY run_id ORDER BY MAX(ended_ms) DESC",
            (tenant_id,),
        ).fetchall()
        return [
            {
                "run_id": str(r[0]),
                "span_count": int(r[1]),
                "duration_ms": int(r[3]) - int(r[2]),
                "cost_usd": float(r[4]),
            }
            for r in rows
        ]


class OutcomeAnalytics:
    """Produce explainable unit economics without storing user content."""

    def summarize(self, records: list[OutcomeRecord]) -> dict[str, Any]:
        """Aggregate cost, success and quality by feature, project, model and tool."""
        for record in records:
            if not math.isfinite(record.cost_usd) or record.cost_usd < 0:
                raise ValueError("cost must be finite and non-negative")
            if (
                not math.isfinite(record.quality_score)
                or not 0 <= record.quality_score <= 1
            ):
                raise ValueError("quality must be between zero and one")
        total = sum(r.cost_usd for r in records)
        successes = sum(r.succeeded for r in records)
        quality = sum(r.quality_score for r in records)

        def group(field: str) -> list[dict[str, Any]]:
            values: dict[str, list[OutcomeRecord]] = {}
            for record in records:
                values.setdefault(str(getattr(record, field)), []).append(record)
            result = [
                {
                    "name": name,
                    "cost_usd": sum(x.cost_usd for x in items),
                    "outcomes": len(items),
                    "successes": sum(x.succeeded for x in items),
                    "average_quality": sum(x.quality_score for x in items) / len(items),
                }
                for name, items in values.items()
            ]
            return sorted(result, key=lambda x: (-x["cost_usd"], x["name"]))

        return {
            "total_cost_usd": total,
            "outcomes": len(records),
            "successful_outcomes": successes,
            "cost_per_success": total / successes if successes else None,
            "quality_weighted_cost": total / quality if quality else None,
            "by_feature": group("feature"),
            "by_project": group("project"),
            "by_model": group("model"),
            "by_tool": group("tool"),
        }
