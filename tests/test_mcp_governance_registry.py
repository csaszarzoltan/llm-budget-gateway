"""MCPRegistry interface + behavioral (RED) tests.

Normative per docs/architecture/mcp-governance.md §6.1. Interface tests pass
immediately; behavioral tests fail with NotImplementedError until the
implementer lands register/get/list/retire/tools.
"""

import inspect

import pytest

from llm_budget_gateway.mcp_governance import (
    DuplicateServerError,
    MCPRegistry,
    MCPRegistryRequest,
    MCPServerNotFoundError,
    MCPToolNotFoundError,
    ToolInfo,
    open_mcp_db,
)


@pytest.fixture
def conn():
    c = open_mcp_db(":memory:")
    yield c
    c.close()


def request(**overrides):
    base = dict(
        name="github-mcp",
        transport="http",
        endpoint="https://mcp.example.com/mcp",
        tools=[ToolInfo(name="create_issue"), ToolInfo(name="get_repo")],
    )
    base.update(overrides)
    return MCPRegistryRequest(**base)


class TestMCPRegistryInterface:
    def test_constructor_accepts_conn_and_clock(self, conn):
        reg = MCPRegistry(conn, clock=lambda: 1000)
        assert reg is not None

    def test_constructor_default_clock(self, conn):
        reg = MCPRegistry(conn)
        assert reg is not None

    def test_constructor_creates_servers_table(self, conn):
        MCPRegistry(conn)
        names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='mcp_servers'"
            )
        }
        assert "mcp_servers" in names

    def test_constructor_creates_tools_table(self, conn):
        MCPRegistry(conn)
        names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='mcp_tools'"
            )
        }
        assert "mcp_tools" in names

    @pytest.mark.parametrize(
        "method",
        [
            "register",
            "get_server",
            "get_server_by_name",
            "list_servers",
            "list_versions",
            "retire_server",
            "list_tools",
            "get_tool",
            "has_tool",
        ],
    )
    def test_has_method(self, method):
        assert hasattr(MCPRegistry, method)

    def test_register_signature(self):
        sig = inspect.signature(MCPRegistry.register)
        assert "request" in sig.parameters

    def test_list_servers_signature(self):
        sig = inspect.signature(MCPRegistry.list_servers)
        p = sig.parameters["include_retired"]
        assert p.default is False

    def test_get_server_by_name_version_default(self):
        sig = inspect.signature(MCPRegistry.get_server_by_name)
        assert sig.parameters["version"].default is None


class TestMCPRegistryBehavior:
    """RED-phase: every behavioral path raises NotImplementedError today."""

    def test_register_creates_server(self, conn):
        reg = MCPRegistry(conn)
        server = reg.register(request())
        assert server.server_id
        assert server.status == "active"

    def test_register_duplicate_name_version_raises(self, conn):
        reg = MCPRegistry(conn)
        reg.register(request())
        with pytest.raises(DuplicateServerError):
            reg.register(request())

    def test_register_same_name_new_version_ok(self, conn):
        reg = MCPRegistry(conn)
        reg.register(request())
        newer = reg.register(request(version="1.1.0"))
        assert newer.version == "1.1.0"

    def test_get_server_unknown_raises(self, conn):
        reg = MCPRegistry(conn)
        with pytest.raises(MCPServerNotFoundError):
            reg.get_server("nope")

    def test_get_server_by_name_returns_latest(self, conn):
        reg = MCPRegistry(conn)
        reg.register(request(version="1.0.0"))
        reg.register(request(version="1.2.0"))
        server = reg.get_server_by_name("github-mcp")
        assert server.version == "1.2.0"

    def test_list_servers_one_row_per_name(self, conn):
        reg = MCPRegistry(conn)
        reg.register(request(version="1.0.0"))
        reg.register(request(version="1.1.0"))
        servers = reg.list_servers()
        assert len(servers) == 1
        assert servers[0].version == "1.1.0"

    def test_list_servers_excludes_retired(self, conn):
        reg = MCPRegistry(conn)
        srv = reg.register(request())
        reg.retire_server(srv.server_id)
        assert reg.list_servers() == []
        assert len(reg.list_servers(include_retired=True)) == 1

    def test_list_versions_newest_first(self, conn):
        reg = MCPRegistry(conn)
        reg.register(request(version="1.0.0"))
        reg.register(request(version="1.1.0"))
        versions = reg.list_versions("github-mcp")
        assert [v.version for v in versions] == ["1.1.0", "1.0.0"]

    def test_retire_server_sets_status(self, conn):
        reg = MCPRegistry(conn)
        srv = reg.register(request())
        retired = reg.retire_server(srv.server_id)
        assert retired.status == "retired"

    def test_retire_unknown_raises(self, conn):
        reg = MCPRegistry(conn)
        with pytest.raises(MCPServerNotFoundError):
            reg.retire_server("nope")

    def test_list_tools_ordered_by_name(self, conn):
        reg = MCPRegistry(conn)
        srv = reg.register(request())
        tools = reg.list_tools(srv.server_id)
        assert [t.name for t in tools] == ["create_issue", "get_repo"]

    def test_list_tools_unknown_server_raises(self, conn):
        reg = MCPRegistry(conn)
        with pytest.raises(MCPServerNotFoundError):
            reg.list_tools("nope")

    def test_get_tool_returns_tool(self, conn):
        reg = MCPRegistry(conn)
        srv = reg.register(request())
        tool = reg.get_tool(srv.server_id, "create_issue")
        assert tool.name == "create_issue"

    def test_get_tool_unknown_tool_raises(self, conn):
        reg = MCPRegistry(conn)
        srv = reg.register(request())
        with pytest.raises(MCPToolNotFoundError):
            reg.get_tool(srv.server_id, "missing_tool")

    def test_has_tool_true_for_registered(self, conn):
        reg = MCPRegistry(conn)
        srv = reg.register(request())
        assert reg.has_tool(srv.server_id, "create_issue") is True

    def test_has_tool_false_for_missing(self, conn):
        reg = MCPRegistry(conn)
        srv = reg.register(request())
        assert reg.has_tool(srv.server_id, "ghost") is False

    def test_has_tool_false_for_unknown_server(self, conn):
        reg = MCPRegistry(conn)
        assert reg.has_tool("nope", "create_issue") is False
