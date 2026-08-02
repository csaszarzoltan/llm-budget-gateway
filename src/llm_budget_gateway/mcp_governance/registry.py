"""MCP server registry + tool inventory (SQLite).

Normative per docs/architecture/mcp-governance.md §6.1. The constructor is
functional in the RED phase (creates the mcp_servers + mcp_tools tables on the
shared connection); every behavioral method raises NotImplementedError until
the implementer lands it.
"""

import sqlite3
import time
from collections.abc import Callable

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
        """Insert a server row + its tool inventory (RED stub)."""
        raise NotImplementedError

    def get_server(self, server_id: str) -> MCPServer:
        """Return by id (any status) (RED stub)."""
        raise NotImplementedError

    def get_server_by_name(
        self, name: str, version: str | None = None
    ) -> MCPServer:
        """Return the (name, version) row, or the highest version (RED stub)."""
        raise NotImplementedError

    def list_servers(self, include_retired: bool = False) -> list[MCPServer]:
        """One row per name — the highest version, ordered by name (RED stub)."""
        raise NotImplementedError

    def list_versions(self, name: str) -> list[MCPServer]:
        """All rows for name, highest version first (RED stub)."""
        raise NotImplementedError

    def retire_server(self, server_id: str) -> MCPServer:
        """Set status='retired' (idempotent) (RED stub)."""
        raise NotImplementedError

    def list_tools(self, server_id: str) -> list[ToolInfo]:
        """All tools for the server, ordered by name asc (RED stub)."""
        raise NotImplementedError

    def get_tool(self, server_id: str, tool_name: str) -> ToolInfo:
        """Tool lookup; unknown server/tool raises (RED stub)."""
        raise NotImplementedError

    def has_tool(self, server_id: str, tool_name: str) -> bool:
        """True iff the server exists and the tool is registered (RED stub)."""
        raise NotImplementedError
