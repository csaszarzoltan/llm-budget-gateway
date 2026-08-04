"""Audit trail store for every MCP tool-call attempt.

Normative per docs/architecture/mcp-governance.md §6.4. The constructor is
functional in the RED phase (mcp_audit_events table + indexes); append/query
are implemented here.
"""

import json
import secrets
import sqlite3
import time
from collections.abc import Callable

from .schemas import AuditEvent, AuditPage

_CREATE_AUDIT = """
CREATE TABLE IF NOT EXISTS mcp_audit_events (
    event_id    TEXT PRIMARY KEY,
    server_id   TEXT NOT NULL,
    tool_name   TEXT NOT NULL,
    caller      TEXT NOT NULL,
    scope_kind  TEXT NOT NULL,
    scope_key   TEXT NOT NULL,
    args_json   TEXT NOT NULL,
    decision    TEXT NOT NULL,
    status      TEXT NOT NULL,
    reason      TEXT,
    cost        REAL NOT NULL DEFAULT 0,
    latency_ms  INTEGER NOT NULL DEFAULT 0,
    timestamp   INTEGER NOT NULL,
    redacted    INTEGER NOT NULL DEFAULT 1,
    approval_id TEXT,
    request_id  TEXT
)
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_mcp_audit_caller ON mcp_audit_events (caller, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_mcp_audit_tool ON mcp_audit_events (server_id, tool_name, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_mcp_audit_ts ON mcp_audit_events (timestamp)",
]

_VALID_DECISIONS = {"allowed", "denied", "approval_required", "approved", "error"}
_VALID_STATUSES = {"started", "completed", "blocked", "failed"}

_COLUMNS = (
    "event_id, server_id, tool_name, caller, scope_kind, scope_key, args_json, "
    "decision, status, reason, cost, latency_ms, timestamp, redacted, "
    "approval_id, request_id"
)


def _row_to_event(row: sqlite3.Row) -> AuditEvent:
    """Map a mcp_audit_events row to an AuditEvent model."""
    return AuditEvent(
        event_id=row["event_id"],
        server_id=row["server_id"],
        tool_name=row["tool_name"],
        caller=row["caller"],
        scope_kind=row["scope_kind"],
        scope_key=row["scope_key"],
        args=json.loads(row["args_json"] or "{}"),
        decision=row["decision"],
        status=row["status"],
        reason=row["reason"],
        cost=row["cost"],
        latency_ms=row["latency_ms"],
        timestamp=row["timestamp"],
        redacted=bool(row["redacted"]),
        approval_id=row["approval_id"],
        request_id=row["request_id"],
    )


class AuditStore:
    """Append-only (replace-on-same-id) audit events with filtered queries."""

    def __init__(
        self, conn: sqlite3.Connection, clock: Callable[[], int] | None = None
    ) -> None:
        """Creates the mcp_audit_events table and its indexes."""
        self._conn = conn
        self._clock = clock if clock is not None else (lambda: int(time.time()))
        conn.execute(_CREATE_AUDIT)
        for stmt in _CREATE_INDEXES:
            conn.execute(stmt)
        conn.commit()

    def append(self, event: AuditEvent) -> AuditEvent:
        """Insert or replace the event (auto id when event_id == \"\") (RED stub)."""
        event_id = event.event_id if event.event_id else secrets.token_hex(8)
        self._conn.execute(
            f"INSERT OR REPLACE INTO mcp_audit_events ({_COLUMNS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                event.server_id,
                event.tool_name,
                event.caller,
                event.scope_kind,
                event.scope_key,
                json.dumps(event.args),
                event.decision,
                event.status,
                event.reason,
                event.cost,
                event.latency_ms,
                event.timestamp,
                1 if event.redacted else 0,
                event.approval_id,
                event.request_id,
            ),
        )
        self._conn.commit()
        return event.model_copy(update={"event_id": event_id})

    def query(
        self,
        *,
        caller: str | None = None,
        server_id: str | None = None,
        tool_name: str | None = None,
        decision: str | None = None,
        status: str | None = None,
        since: int | None = None,
        until: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> AuditPage:
        """Filtered query; timestamp DESC; invalid decision/status -> ValueError (RED stub)."""
        if decision is not None and decision not in _VALID_DECISIONS:
            raise ValueError(f"invalid decision: {decision!r}")
        if status is not None and status not in _VALID_STATUSES:
            raise ValueError(f"invalid status: {status!r}")
        limit = max(1, min(500, limit))
        offset = max(0, offset)
        clauses: list[str] = []
        params: list[object] = []
        for column, value in (
            ("caller", caller),
            ("server_id", server_id),
            ("tool_name", tool_name),
            ("decision", decision),
            ("status", status),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since)
        if until is not None:
            clauses.append("timestamp <= ?")
            params.append(until)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        total = self._conn.execute(
            f"SELECT COUNT(*) FROM mcp_audit_events{where}", params
        ).fetchone()[0]
        rows = self._conn.execute(
            f"SELECT * FROM mcp_audit_events{where} "
            "ORDER BY timestamp DESC, event_id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return AuditPage(
            object="list",
            data=[_row_to_event(r) for r in rows],
            limit=limit,
            offset=offset,
            total=total,
        )
