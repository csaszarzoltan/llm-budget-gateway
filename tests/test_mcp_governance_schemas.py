"""Schema interface + validation tests for mcp_governance models.

Normative per docs/architecture/mcp-governance.md §4. Schemas are fully
functional in the RED phase, so every test here must pass immediately: valid
models construct, invalid models raise pydantic ValidationError.
"""

import pytest
from pydantic import ValidationError

from llm_budget_gateway.mcp_governance import (
    ApprovalRequest,
    AuditEvent,
    AuditPage,
    MCPRegistryRequest,
    MCPServer,
    PolicyDecision,
    RuleVerdict,
    ToolBudget,
    ToolBudgetRequest,
    ToolInfo,
    ToolPolicy,
    ToolPolicyRequest,
)


def server(**overrides):
    base = dict(
        server_id="srv1",
        name="github-mcp",
        transport="http",
        endpoint="https://mcp.example.com/mcp",
        created_at=100,
        updated_at=100,
    )
    base.update(overrides)
    return MCPServer(**base)


class TestMCPServer:
    def test_valid_http_server(self):
        s = server()
        assert s.server_id == "srv1"
        assert s.status == "active"
        assert s.version == "1.0.0"

    def test_stdio_server_without_endpoint(self):
        s = server(transport="stdio", endpoint=None)
        assert s.transport == "stdio"

    def test_default_version_and_description(self):
        s = server()
        assert s.version == "1.0.0"
        assert s.description == ""
        assert s.config == {}

    @pytest.mark.parametrize("bad", ["has space", "", "x" * 65, "a/b"])
    def test_invalid_name_rejected(self, bad):
        with pytest.raises(ValidationError):
            server(name=bad)

    @pytest.mark.parametrize("bad", ["1.0", "1.0.0.1", "v1.0.0", "abc"])
    def test_invalid_version_rejected(self, bad):
        with pytest.raises(ValidationError):
            server(version=bad)

    @pytest.mark.parametrize("bad", ["grpc", "tcp", 42])
    def test_invalid_transport_rejected(self, bad):
        with pytest.raises(ValidationError):
            server(transport=bad)

    def test_endpoint_required_for_http(self):
        with pytest.raises(ValidationError):
            server(transport="http", endpoint="")

    def test_endpoint_required_for_sse(self):
        with pytest.raises(ValidationError):
            server(transport="sse", endpoint=None)

    @pytest.mark.parametrize("bad", ["retired_now", "archived"])
    def test_invalid_status_rejected(self, bad):
        with pytest.raises(ValidationError):
            server(status=bad)


class TestToolInfo:
    def test_valid(self):
        t = ToolInfo(name="create_issue", description="d", enabled=True)
        assert t.name == "create_issue"
        assert t.input_schema == {}

    @pytest.mark.parametrize("bad", ["has space", "", "x" * 129])
    def test_invalid_name_rejected(self, bad):
        with pytest.raises(ValidationError):
            ToolInfo(name=bad)


class TestMCPRegistryRequest:
    def test_valid(self):
        req = MCPRegistryRequest(
            name="github-mcp",
            transport="http",
            endpoint="https://mcp.example.com/mcp",
            tools=[ToolInfo(name="create_issue")],
        )
        assert req.version == "1.0.0"
        assert len(req.tools) == 1

    def test_extra_field_forbidden(self):
        with pytest.raises(ValidationError):
            MCPRegistryRequest(name="x", transport="stdio", bogus=1)

    def test_duplicate_tool_names_rejected(self):
        with pytest.raises(ValidationError):
            MCPRegistryRequest(
                name="x",
                transport="stdio",
                tools=[ToolInfo(name="t1"), ToolInfo(name="t1")],
            )

    def test_endpoint_required_for_non_stdio(self):
        with pytest.raises(ValidationError):
            MCPRegistryRequest(name="x", transport="websocket")

    def test_stdio_ok_without_endpoint(self):
        assert MCPRegistryRequest(name="x", transport="stdio") is not None

    def test_invalid_name_rejected(self):
        with pytest.raises(ValidationError):
            MCPRegistryRequest(name="bad name", transport="stdio")


class TestToolPolicy:
    def test_valid(self):
        p = ToolPolicy(
            policy_id="p1",
            scope_kind="user",
            scope_key="alice",
            server_id="srv1",
            tool_name="create_issue",
            effect="allow",
            created_at=100,
        )
        assert p.effect == "allow"
        assert p.created_by == "admin"

    def test_wildcard_server_only(self):
        p = ToolPolicy(
            policy_id="p2", scope_kind="global", scope_key="default", effect="deny", created_at=1
        )
        assert p.server_id is None

    def test_tool_name_requires_server_id(self):
        with pytest.raises(ValidationError):
            ToolPolicy(
                policy_id="p3",
                scope_kind="user",
                scope_key="alice",
                tool_name="x",
                effect="allow",
                created_at=1,
            )

    def test_empty_scope_key_rejected(self):
        with pytest.raises(ValidationError):
            ToolPolicy(
                policy_id="p4", scope_kind="user", scope_key="", effect="allow", created_at=1
            )


class TestToolPolicyRequest:
    def test_valid(self):
        req = ToolPolicyRequest(
            scope_kind="user", scope_key="alice", effect="allow"
        )
        assert req.effect == "allow"

    def test_extra_field_forbidden(self):
        with pytest.raises(ValidationError):
            ToolPolicyRequest(scope_kind="user", scope_key="a", effect="allow", extra=1)

    def test_tool_name_requires_server_id(self):
        with pytest.raises(ValidationError):
            ToolPolicyRequest(
                scope_kind="user", scope_key="a", tool_name="t", effect="allow"
            )

    def test_invalid_effect_rejected(self):
        with pytest.raises(ValidationError):
            ToolPolicyRequest(scope_kind="user", scope_key="a", effect="maybe")


class TestToolBudget:
    def test_valid_hard_limit(self):
        b = ToolBudget(
            budget_id="b1", scope_kind="user", scope_key="alice", hard_limit=5.0, created_at=1
        )
        assert b.window == "30d"

    def test_soft_limit_only(self):
        b = ToolBudget(
            budget_id="b2", scope_kind="team", scope_key="eng", soft_limit=1.0, created_at=1
        )
        assert b.soft_limit == 1.0

    def test_no_limits_rejected(self):
        with pytest.raises(ValidationError):
            ToolBudget(budget_id="b3", scope_kind="user", scope_key="a", created_at=1)

    def test_negative_limit_rejected(self):
        with pytest.raises(ValidationError):
            ToolBudget(
                budget_id="b4", scope_kind="user", scope_key="a", hard_limit=-1, created_at=1
            )

    def test_inf_limit_rejected(self):
        with pytest.raises(ValidationError):
            ToolBudget(
                budget_id="b5",
                scope_kind="user",
                scope_key="a",
                hard_limit=float("inf"),
                created_at=1,
            )

    def test_invalid_window_rejected(self):
        with pytest.raises(ValidationError):
            ToolBudget(
                budget_id="b6", scope_kind="user", scope_key="a", hard_limit=1, window="1y", created_at=1
            )

    def test_tool_name_requires_server_id(self):
        with pytest.raises(ValidationError):
            ToolBudget(
                budget_id="b7",
                scope_kind="user",
                scope_key="a",
                tool_name="t",
                hard_limit=1,
                created_at=1,
            )


class TestToolBudgetRequest:
    def test_valid(self):
        req = ToolBudgetRequest(scope_kind="user", scope_key="a", hard_limit=3.5)
        assert req.hard_limit == 3.5

    def test_extra_field_forbidden(self):
        with pytest.raises(ValidationError):
            ToolBudgetRequest(scope_kind="user", scope_key="a", hard_limit=1, extra=1)

    def test_no_limits_rejected(self):
        with pytest.raises(ValidationError):
            ToolBudgetRequest(scope_kind="user", scope_key="a")

    def test_bad_window_rejected(self):
        with pytest.raises(ValidationError):
            ToolBudgetRequest(scope_kind="user", scope_key="a", hard_limit=1, window="1y")

    def test_valid_window_forms(self):
        for w in ("30s", "30m", "30h", "30d", "daily", "monthly"):
            assert ToolBudgetRequest(scope_kind="user", scope_key="a", hard_limit=1, window=w)


class TestAuditEvent:
    def test_valid(self):
        e = AuditEvent(
            event_id="e1",
            server_id="srv1",
            tool_name="create_issue",
            caller="alice",
            scope_kind="user",
            scope_key="alice",
            decision="allowed",
            status="completed",
            timestamp=100,
        )
        assert e.cost == 0.0
        assert e.redacted is True

    def test_invalid_decision_rejected(self):
        with pytest.raises(ValidationError):
            AuditEvent(
                event_id="e2",
                server_id="srv1",
                tool_name="t",
                caller="a",
                scope_kind="user",
                scope_key="a",
                decision="maybe",
                status="completed",
                timestamp=1,
            )

    def test_invalid_status_rejected(self):
        with pytest.raises(ValidationError):
            AuditEvent(
                event_id="e3",
                server_id="srv1",
                tool_name="t",
                caller="a",
                scope_kind="user",
                scope_key="a",
                decision="allowed",
                status="pending",
                timestamp=1,
            )


class TestAuditPage:
    def test_valid(self):
        page = AuditPage(data=[], limit=50, offset=0, total=0)
        assert page.object == "list"


class TestPolicyDecision:
    def test_valid(self):
        d = PolicyDecision(effect="allow", policy_id="p1", reason="allowed by policy p1")
        assert d.matched_scope is None


class TestRuleVerdict:
    def test_valid(self):
        v = RuleVerdict(allowed=False, rule="ssrf_guard", reason="ssrf: private address 10.0.0.1")
        assert v.detail is None


class TestApprovalRequest:
    def test_valid(self):
        a = ApprovalRequest(
            approval_id="aprv1",
            server_id="srv1",
            tool_name="t",
            caller="alice",
            scope_kind="user",
            scope_key="alice",
            args_hash="abc",
            status="pending",
            requested_at=100,
        )
        assert a.status == "pending"
