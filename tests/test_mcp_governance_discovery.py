"""MCPDiscoveryAdapter interface + behavioral tests.

Normative per docs/architecture/mcp-governance.md §6.7. The adapter lazy-
imports the MCP SDK inside discover_tools, so module import never requires it.

S7: the adapter is implemented (was a NotImplementedError stub). Behavioral
tests exercise a live stdio MCP server (FastMCP subprocess) end-to-end plus
the error paths. Tests guard with pytest.importorskip("mcp") per spec §11.4.
"""

import inspect
import sys

import pytest

from llm_budget_gateway.mcp_governance import MCPDiscoveryAdapter
from llm_budget_gateway.mcp_governance.exceptions import MCPDiscoveryError


class TestMCPDiscoveryAdapterInterface:
    def test_instantiable(self):
        adapter = MCPDiscoveryAdapter()
        assert adapter is not None

    def test_discover_tools_is_async(self):
        assert inspect.iscoroutinefunction(MCPDiscoveryAdapter.discover_tools)

    def test_discover_tools_keyword_only(self):
        sig = inspect.signature(MCPDiscoveryAdapter.discover_tools)
        for name in ("transport", "endpoint", "command"):
            assert sig.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert sig.parameters["endpoint"].default is None
        assert sig.parameters["command"].default is None


class TestMCPDiscoveryAdapterBehavior:
    """S7: live inventory via the pinned mcp SDK, not a stub."""

    @pytest.mark.asyncio
    async def test_discover_tools_stdio_live(self, tmp_path):
        pytest.importorskip("mcp")
        server_script = tmp_path / "mcp_server.py"
        server_script.write_text(
            "from mcp.server.fastmcp import FastMCP\n"
            "mcp = FastMCP('demo')\n"
            "@mcp.tool()\n"
            "def greet(name: str) -> str:\n"
            "    return f'hi {name}'\n"
            "@mcp.tool()\n"
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n"
            "mcp.run(transport='stdio')\n"
        )
        adapter = MCPDiscoveryAdapter()
        tools = await adapter.discover_tools(
            transport="stdio", command=[sys.executable, str(server_script)]
        )
        names = {t.name for t in tools}
        assert {"greet", "add"} <= names
        greet = next(t for t in tools if t.name == "greet")
        assert greet.input_schema.get("type") == "object"
        assert "name" in greet.input_schema.get("properties", {})

    @pytest.mark.asyncio
    async def test_unsupported_transport_raises(self):
        pytest.importorskip("mcp")
        adapter = MCPDiscoveryAdapter()
        with pytest.raises(MCPDiscoveryError):
            await adapter.discover_tools(transport="carrier-pigeon")

    @pytest.mark.asyncio
    async def test_http_requires_endpoint(self):
        pytest.importorskip("mcp")
        adapter = MCPDiscoveryAdapter()
        with pytest.raises(MCPDiscoveryError):
            await adapter.discover_tools(transport="http")

    @pytest.mark.asyncio
    async def test_stdio_requires_command(self):
        pytest.importorskip("mcp")
        adapter = MCPDiscoveryAdapter()
        with pytest.raises(MCPDiscoveryError):
            await adapter.discover_tools(transport="stdio")

    @pytest.mark.asyncio
    async def test_bad_stdio_command_raises_discovery_error(self):
        pytest.importorskip("mcp")
        adapter = MCPDiscoveryAdapter()
        with pytest.raises(MCPDiscoveryError):
            await adapter.discover_tools(
                transport="stdio", command=["/nonexistent/binary-xyz"]
            )

    @pytest.mark.asyncio
    async def test_http_connection_failure_raises(self):
        pytest.importorskip("mcp")
        adapter = MCPDiscoveryAdapter()
        with pytest.raises(MCPDiscoveryError):
            await adapter.discover_tools(
                transport="http", endpoint="http://127.0.0.1:1/mcp"
            )
