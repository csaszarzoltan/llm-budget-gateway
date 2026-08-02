"""MCPDiscoveryAdapter interface + behavioral (RED) tests.

Normative per docs/architecture/mcp-governance.md §6.7. The adapter lazy-
imports the MCP SDK inside discover_tools, so module import never requires it.
The behavioral test guards with pytest.importorskip("mcp") per spec §11.4.
"""

import inspect

import pytest

from llm_budget_gateway.mcp_governance import MCPDiscoveryAdapter


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
    """RED-phase: live discovery is not implemented yet."""

    @pytest.mark.asyncio
    async def test_discover_tools_raises_not_implemented(self):
        pytest.importorskip("mcp")
        adapter = MCPDiscoveryAdapter()
        with pytest.raises(NotImplementedError):
            await adapter.discover_tools(
                transport="http", endpoint="https://mcp.example.com/mcp"
            )
