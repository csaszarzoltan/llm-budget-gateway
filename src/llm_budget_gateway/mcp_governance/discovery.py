"""Live tool inventory via the official MCP SDK (lazy-imported).

Normative per docs/architecture/mcp-governance.md §6.7. The ``import mcp``
happens inside ``discover_tools`` so module import never requires the MCP SDK.
The behavioral method raises NotImplementedError in the RED phase.
"""

from .schemas import ToolInfo


class MCPDiscoveryAdapter:
    """Connect to an MCP server and return its tool inventory."""

    async def discover_tools(
        self,
        *,
        transport: str,
        endpoint: str | None = None,
        command: list[str] | None = None,
    ) -> list[ToolInfo]:
        """ClientSession.list_tools() over http/sse/stdio/websocket (RED stub)."""
        raise NotImplementedError
