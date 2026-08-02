"""Audit trail store for every MCP tool-call attempt.

Normative per docs/architecture/mcp-governance.md §6.4. The constructor is
functional in the RED phase (mcp_audit_events table + indexes); append/query
raise NotImplementedError until the implementer lands them.
"""

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
        raise NotImplementedError

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
        raise NotImplementedError
