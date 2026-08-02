"""FastAPI REST app for MCP governance.

Normative per docs/architecture/mcp-governance.md §7. The factory signature is
fixed; the body raises NotImplementedError in the RED phase. The implementer
wires the stores, the engine, the routes and the auth middleware.
"""

import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI


def create_mcp_governance_app(
    api_key: str | None = None,
    *,
    conn: sqlite3.Connection | None = None,
) -> "FastAPI":
    """Build the fail-closed MCP governance app (RED stub).

    - api_key None -> os.getenv("GATEWAY_MCP_API_KEY", "")
    - conn None -> open_mcp_db(":memory:") owned by the app
    - Wires registry / policy / budget / audit / approval stores, SSRFGuard,
      PIIRedactor, ToolBudgetService, MCPPolicyEngine and the routes.
    """
    raise NotImplementedError
