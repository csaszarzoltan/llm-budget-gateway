"""Live tool inventory via the official MCP SDK (lazy-imported).

Normative per docs/architecture/mcp-governance.md §6.7. The ``mcp`` imports
happen inside the transport methods so module import never requires the MCP
SDK. Connection/negotiation failures surface as MCPDiscoveryError (502).

S7: this adapter is fully implemented (previously a NotImplementedError
stub) using the pinned mcp SDK (``mcp>=1.2,<2`` in pyproject.toml) — no
supply-chain risk from an untested hand-rolled client.
"""

from typing import Any

from .exceptions import MCPDiscoveryError
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
        """ClientSession.list_tools() over http/sse/stdio/websocket (S7).

        - transport "stdio": command is the launch argv (required).
        - transport "http" | "sse": endpoint is the server URL (required).
        - transport "websocket": endpoint ws(s):// URL (requires the
          optional ``websockets`` package).
        - Connection/negotiation failure -> MCPDiscoveryError.
        """
        if transport == "stdio":
            if not command:
                raise MCPDiscoveryError("stdio transport requires a command")
            return await self._discover_stdio(command)
        if transport in ("http", "sse"):
            if not endpoint:
                raise MCPDiscoveryError(f"{transport} transport requires an endpoint")
            return await self._discover_http(endpoint, transport)
        if transport == "websocket":
            if not endpoint:
                raise MCPDiscoveryError("websocket transport requires an endpoint")
            return await self._discover_websocket(endpoint)
        raise MCPDiscoveryError(f"unsupported transport: {transport!r}")

    async def _discover_stdio(self, command: list[str]) -> list[ToolInfo]:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        params = StdioServerParameters(command=command[0], args=list(command[1:]))
        try:
            async with (
                stdio_client(params) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                result = await session.list_tools()
        except Exception as exc:
            raise MCPDiscoveryError(f"failed to discover tools via stdio: {exc}") from exc
        return [self._to_tool_info(tool) for tool in result.tools]

    async def _discover_http(self, endpoint: str, transport: str) -> list[ToolInfo]:
        from mcp import ClientSession

        if transport == "sse":
            from mcp.client.sse import sse_client

            connect = sse_client(endpoint)
        else:
            from mcp.client.streamable_http import streamable_http_client

            connect = streamable_http_client(endpoint)
        try:
            # streamable_http yields (read, write[, get_session_id]) depending
            # on the SDK version — unpack defensively.
            async with connect as streams:
                if len(streams) == 3:
                    read, write, _ = streams
                else:
                    read, write = streams
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.list_tools()
        except Exception as exc:
            raise MCPDiscoveryError(f"failed to discover tools via {transport}: {exc}") from exc
        return [self._to_tool_info(tool) for tool in result.tools]

    async def _discover_websocket(self, endpoint: str) -> list[ToolInfo]:
        try:
            from mcp.client.websocket import websocket_client
        except ModuleNotFoundError as exc:
            raise MCPDiscoveryError(
                "websocket transport requires the optional 'websockets' package"
            ) from exc
        from mcp import ClientSession

        try:
            async with (
                websocket_client(endpoint) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                result = await session.list_tools()
        except Exception as exc:
            raise MCPDiscoveryError(
                f"failed to discover tools via websocket: {exc}"
            ) from exc
        return [self._to_tool_info(tool) for tool in result.tools]

    @staticmethod
    def _to_tool_info(tool: Any) -> ToolInfo:
        """Map an mcp.types.Tool to the governance ToolInfo model."""
        schema = getattr(tool, "inputSchema", None)
        return ToolInfo(
            name=tool.name,
            description=getattr(tool, "description", "") or "",
            input_schema=schema if isinstance(schema, dict) else {},
            enabled=True,
        )
