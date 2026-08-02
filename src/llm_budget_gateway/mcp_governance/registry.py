"""MCP server registry + tool inventory (SQLite).

Normative per docs/architecture/mcp-governance.md §6.1. The constructor is
functional in the RED phase (creates the mcp_servers + mcp_tools tables on the
shared connection); the behavioral methods are implemented here.
"""

import json
import secrets
import sqlite3
import time
from collections.abc import Callable

from .exceptions import (
    DuplicateServerError,
    MCPServerNotFoundError,
    MCPToolNotFoundError,
)
from .schemas import MCPRegistryRequest, MCPServer, ToolInfo

_CREATE_SERVERS = """
CREATE TABLE IF NOT EXISTS mcp_servers (
    server_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    transport   TEXT NOT NULL,
    endpoint    TEXT,
    version     TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active',
    config_json TEXT NOT NULL DEFAULT '{}',
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL,
    UNIQUE (name, version)
)
"""

_CREATE_TOOLS = """
CREATE TABLE IF NOT EXISTS mcp_tools (
    server_id         TEXT NOT NULL REFERENCES mcp_servers(server_id) ON DELETE CASCADE,
    tool_name         TEXT NOT NULL,
    description       TEXT NOT NULL DEFAULT '',
    input_schema_json TEXT NOT NULL DEFAULT '{}',
    enabled           INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (server_id, tool_name)
)
"""


def _version_key(version: str) -> tuple[int, int, int]:
    """Return the (major, minor, patch) int tuple used for version ordering."""
    return tuple(int(part) for part in version.split("."))  # type: ignore[return-value]


def _row_to_server(row: sqlite3.Row) -> MCPServer:
    """Map a mcp_servers row to an MCPServer model."""
    return MCPServer(
        server_id=row["server_id"],
        name=row["name"],
        transport=row["transport"],
        endpoint=row["endpoint"],
        version=row["version"],
        description=row["description"],
        status=row["status"],
        config=json.loads(row["config_json"] or "{}"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_tool(row: sqlite3.Row) -> ToolInfo:
    """Map a mcp_tools row to a ToolInfo model."""
    return ToolInfo(
        name=row["tool_name"],
        description=row["description"],
        input_schema=json.loads(row["input_schema_json"] or "{}"),
        enabled=bool(row["enabled"]),
    )


class MCPRegistry:
    """Servers + tool inventory, keyed by generated server_id."""

    def __init__(
        self, conn: sqlite3.Connection, clock: Callable[[], int] | None = None
    ) -> None:
        """Creates mcp_servers + mcp_tools tables (IF NOT EXISTS)."""
        self._conn = conn
        self._clock = clock if clock is not None else (lambda: int(time.time()))
        conn.execute(_CREATE_SERVERS)
        conn.execute(_CREATE_TOOLS)
        conn.commit()

    def register(self, request: MCPRegistryRequest) -> MCPServer:
        """Insert a server row + its tool inventory.

        - server_id = secrets.token_hex(8); created_at = updated_at = clock()
        - (name, version) already present  -> raises DuplicateServerError
        - Tools are upserted into mcp_tools for the new server_id.
        """
        now = int(self._clock())
        server_id = secrets.token_hex(8)
        try:
            self._conn.execute(
                """
                INSERT INTO mcp_servers (
                    server_id, name, transport, endpoint, version, description,
                    status, config_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    server_id,
                    request.name,
                    request.transport,
                    request.endpoint,
                    request.version,
                    request.description,
                    json.dumps(request.config),
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateServerError(
                f"server {request.name} version {request.version} already registered"
            ) from exc
        for tool in request.tools:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO mcp_tools (
                    server_id, tool_name, description, input_schema_json, enabled
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    server_id,
                    tool.name,
                    tool.description,
                    json.dumps(tool.input_schema),
                    1 if tool.enabled else 0,
                ),
            )
        self._conn.commit()
        return MCPServer(
            server_id=server_id,
            name=request.name,
            transport=request.transport,
            endpoint=request.endpoint,
            version=request.version,
            description=request.description,
            status="active",
            config=request.config,
            created_at=now,
            updated_at=now,
        )

    def get_server(self, server_id: str) -> MCPServer:
        """Return by id (any status). Unknown id -> MCPServerNotFoundError."""
        row = self._conn.execute(
            "SELECT * FROM mcp_servers WHERE server_id = ?", (server_id,)
        ).fetchone()
        if row is None:
            raise MCPServerNotFoundError(f"server {server_id!r} not found")
        return _row_to_server(row)

    def get_server_by_name(
        self, name: str, version: str | None = None
    ) -> MCPServer:
        """Return the (name, version) row, or the highest version (RED stub)."""
        if version is not None:
            row = self._conn.execute(
                "SELECT * FROM mcp_servers WHERE name = ? AND version = ?",
                (name, version),
            ).fetchone()
            if row is None:
                raise MCPServerNotFoundError(
                    f"server {name!r} version {version} not found"
                )
            return _row_to_server(row)
        rows = self._conn.execute(
            "SELECT * FROM mcp_servers WHERE name = ?", (name,)
        ).fetchall()
        if not rows:
            raise MCPServerNotFoundError(f"server {name!r} not found")
        best = max(rows, key=lambda r: _version_key(r["version"]))
        return _row_to_server(best)

    def list_servers(self, include_retired: bool = False) -> list[MCPServer]:
        """One row per name — the highest version, ordered by name (RED stub)."""
        if include_retired:
            rows = self._conn.execute(
                "SELECT * FROM mcp_servers ORDER BY name, created_at"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM mcp_servers WHERE status = 'active' "
                "ORDER BY name, created_at"
            ).fetchall()
        by_name: dict[str, sqlite3.Row] = {}
        for row in rows:
            current = by_name.get(row["name"])
            if current is None or _version_key(row["version"]) > _version_key(
                current["version"]
            ):
                by_name[row["name"]] = row
        return [_row_to_server(r) for _, r in sorted(by_name.items())]

    def list_versions(self, name: str) -> list[MCPServer]:
        """All rows for name, highest version first (RED stub)."""
        rows = self._conn.execute(
            "SELECT * FROM mcp_servers WHERE name = ?", (name,)
        ).fetchall()
        rows = sorted(rows, key=lambda r: _version_key(r["version"]), reverse=True)
        return [_row_to_server(r) for r in rows]

    def retire_server(self, server_id: str) -> MCPServer:
        """Set status='retired' (idempotent) (RED stub)."""
        self.get_server(server_id)  # raises MCPServerNotFoundError when unknown
        self._conn.execute(
            "UPDATE mcp_servers SET status = 'retired', updated_at = ? "
            "WHERE server_id = ?",
            (int(self._clock()), server_id),
        )
        self._conn.commit()
        return self.get_server(server_id)

    def list_tools(self, server_id: str) -> list[ToolInfo]:
        """All tools for the server, ordered by name asc (RED stub)."""
        self.get_server(server_id)  # raises MCPServerNotFoundError when unknown
        rows = self._conn.execute(
            "SELECT * FROM mcp_tools WHERE server_id = ? ORDER BY tool_name",
            (server_id,),
        ).fetchall()
        return [_row_to_tool(r) for r in rows]

    def get_tool(self, server_id: str, tool_name: str) -> ToolInfo:
        """Tool lookup; unknown server/tool raises (RED stub)."""
        self.get_server(server_id)  # raises MCPServerNotFoundError when unknown
        row = self._conn.execute(
            "SELECT * FROM mcp_tools WHERE server_id = ? AND tool_name = ?",
            (server_id, tool_name),
        ).fetchone()
        if row is None:
            raise MCPToolNotFoundError(
                f"tool {tool_name!r} not found on server {server_id!r}"
            )
        return _row_to_tool(row)

    def has_tool(self, server_id: str, tool_name: str) -> bool:
        """True iff the server exists and the tool is registered (RED stub)."""
        row = self._conn.execute(
            "SELECT 1 FROM mcp_tools WHERE server_id = ? AND tool_name = ?",
            (server_id, tool_name),
        ).fetchone()
        return row is not None
