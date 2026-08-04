"""MCPPolicyEngine + CallContext interface and behavioral (RED) tests.

Normative per docs/architecture/mcp-governance.md §6.6. Interface tests pass
immediately; behavioral tests fail with NotImplementedError until the
implementer lands before_call / after_call.
"""

import inspect
from dataclasses import fields

import pytest

from llm_budget_gateway.budget_enforcement import (
    BudgetExceededError,
    BudgetScope,
)
from llm_budget_gateway.mcp_governance import (
    AccessDeniedError,
    AuditStore,
    CallContext,
    MCPPolicyEngine,
    MCPRegistry,
    MCPServerNotFoundError,
    MCPToolNotFoundError,
    PIIRedactor,
    PolicyViolationError,
    SSRFGuard,
    ToolBudgetService,
    ToolPolicyStore,
    open_mcp_db,
)
from llm_budget_gateway.mcp_governance.rules import ApprovalStore

ALICE_SCOPES = [
    BudgetScope("user", "alice"),
    BudgetScope("team", "eng"),
    BudgetScope("global", "default"),
]


class FakeTracker:
    def __init__(self, spend: float = 0.0):
        self.spend = spend
        self.records = []

    async def spend_since(self, scope_key, since_epoch, tool_name=None) -> float:
        return self.spend

    async def record(self, usage) -> None:
        self.records.append(usage)


@pytest.fixture
def conn():
    c = open_mcp_db(":memory:")
    yield c
    c.close()


@pytest.fixture
def engine(conn):
    store = ToolBudgetService(FakeTracker(), _budget_store(conn))
    return MCPPolicyEngine(
        registry=MCPRegistry(conn),
        policies=ToolPolicyStore(conn),
        budgets=store,
        audit=AuditStore(conn),
        approvals=ApprovalStore(conn),
        redactor=PIIRedactor(),
        ssrf=SSRFGuard(),
    )


def _budget_store(conn):
    from llm_budget_gateway.mcp_governance import ToolBudgetStore

    return ToolBudgetStore(conn)


class TestCallContextInterface:
    def test_is_dataclass(self):
        assert CallContext.__dataclass_fields__

    def test_has_all_fields(self):
        names = {f.name for f in fields(CallContext)}
        for field in (
            "call_id",
            "request_id",
            "caller",
            "scopes",
            "server_id",
            "tool_name",
            "args_redacted",
            "decision",
            "policy_id",
            "approval_id",
            "reason",
        ):
            assert field in names

    def test_constructs(self):
        ctx = CallContext(
            call_id="c1",
            request_id=None,
            caller="alice",
            scopes=ALICE_SCOPES,
            server_id="srv1",
            tool_name="t1",
            args_redacted={},
            decision="allowed",
            policy_id=None,
            approval_id=None,
            reason=None,
        )
        assert ctx.call_id == "c1"


class TestMCPPolicyEngineInterface:
    def test_constructor_keyword_only(self):
        sig = inspect.signature(MCPPolicyEngine.__init__)
        for name in (
            "registry",
            "policies",
            "budgets",
            "audit",
            "approvals",
            "redactor",
            "ssrf",
        ):
            assert sig.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert sig.parameters["notifier"].default is None
        assert sig.parameters["request_id_factory"].default is None

    def test_before_call_is_async(self):
        assert inspect.iscoroutinefunction(MCPPolicyEngine.before_call)

    def test_after_call_is_async(self):
        assert inspect.iscoroutinefunction(MCPPolicyEngine.after_call)

    def test_before_call_keyword_only(self, engine):
        sig = inspect.signature(MCPPolicyEngine.before_call)
        for name in ("caller", "scopes", "server_id", "tool_name", "args"):
            assert sig.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert sig.parameters["request_id"].default is None


class TestMCPPolicyEngineBehavior:
    """RED-phase: the gate flow is not implemented yet."""

    @pytest.mark.asyncio
    async def test_before_call_unknown_server(self, engine):
        with pytest.raises(MCPServerNotFoundError):
            await engine.before_call(
                caller="alice", scopes=ALICE_SCOPES,
                server_id="nope", tool_name="t1", args={},
            )

    @pytest.mark.asyncio
    async def test_before_call_unknown_tool(self, engine):
        from llm_budget_gateway.mcp_governance import MCPRegistryRequest, ToolInfo

        engine._registry.register(
            MCPRegistryRequest(
                name="srv1", transport="stdio",
                tools=[ToolInfo(name="t1")],
            )
        )
        with pytest.raises(MCPToolNotFoundError):
            await engine.before_call(
                caller="alice", scopes=ALICE_SCOPES,
                server_id=engine._registry.list_servers()[0].server_id,
                tool_name="ghost", args={},
            )

    @pytest.mark.asyncio
    async def test_before_call_allow_path_returns_context(self, engine):
        from llm_budget_gateway.mcp_governance import (
            MCPRegistryRequest,
            ToolInfo,
            ToolPolicyRequest,
        )

        srv = engine._registry.register(
            MCPRegistryRequest(
                name="srv1", transport="stdio",
                tools=[ToolInfo(name="t1")],
            )
        )
        engine._policies.create_policy(
            ToolPolicyRequest(
                scope_kind="global", scope_key="default",
                server_id=srv.server_id, tool_name="t1",
                effect="allow",
            )
        )
        ctx = await engine.before_call(
            caller="alice", scopes=ALICE_SCOPES,
            server_id=srv.server_id, tool_name="t1", args={"x": 1},
        )
        assert ctx.decision == "allowed"
        assert isinstance(ctx, CallContext)

    @pytest.mark.asyncio
    async def test_before_call_denied_policy_raises(self, engine):
        from llm_budget_gateway.mcp_governance import (
            MCPRegistryRequest,
            ToolInfo,
            ToolPolicyRequest,
        )

        srv = engine._registry.register(
            MCPRegistryRequest(
                name="srv1", transport="stdio",
                tools=[ToolInfo(name="t1")],
            )
        )
        engine._policies.create_policy(
            ToolPolicyRequest(
                scope_kind="user", scope_key="alice",
                server_id=srv.server_id, tool_name="t1", effect="deny",
            )
        )
        with pytest.raises(AccessDeniedError):
            await engine.before_call(
                caller="alice", scopes=ALICE_SCOPES,
                server_id=srv.server_id, tool_name="t1", args={},
            )

    @pytest.mark.asyncio
    async def test_before_call_no_policy_denies(self, engine):
        """Deny-by-default (8780f9c): a registered tool with NO matching
        policy is rejected with AccessDeniedError, not silently allowed."""
        from llm_budget_gateway.mcp_governance import (
            MCPRegistryRequest,
            ToolInfo,
        )

        srv = engine._registry.register(
            MCPRegistryRequest(
                name="srv1", transport="stdio",
                tools=[ToolInfo(name="t1")],
            )
        )
        # no policy is created for srv1:t1 — the engine default must deny
        with pytest.raises(AccessDeniedError):
            await engine.before_call(
                caller="alice", scopes=ALICE_SCOPES,
                server_id=srv.server_id, tool_name="t1", args={},
            )

    @pytest.mark.asyncio
    async def test_before_call_approval_required(self, engine):
        from llm_budget_gateway.mcp_governance import (
            ApprovalRequiredError,
            MCPRegistryRequest,
            ToolInfo,
            ToolPolicyRequest,
        )

        srv = engine._registry.register(
            MCPRegistryRequest(
                name="srv1", transport="stdio",
                tools=[ToolInfo(name="t1")],
            )
        )
        engine._policies.create_policy(
            ToolPolicyRequest(
                scope_kind="user", scope_key="alice",
                server_id=srv.server_id, tool_name="t1", effect="approval",
            )
        )
        with pytest.raises(ApprovalRequiredError) as exc:
            await engine.before_call(
                caller="alice", scopes=ALICE_SCOPES,
                server_id=srv.server_id, tool_name="t1", args={"x": 1},
            )
        assert exc.value.approval_id

    @pytest.mark.asyncio
    async def test_before_call_ssrf_block(self, engine):
        from llm_budget_gateway.mcp_governance import (
            MCPRegistryRequest,
            ToolInfo,
            ToolPolicyRequest,
        )

        srv = engine._registry.register(
            MCPRegistryRequest(
                name="srv1", transport="stdio",
                tools=[ToolInfo(name="t1")],
            )
        )
        engine._policies.create_policy(
            ToolPolicyRequest(
                scope_kind="global", scope_key="default",
                server_id=srv.server_id, tool_name="t1",
                effect="allow",
            )
        )
        with pytest.raises(PolicyViolationError):
            await engine.before_call(
                caller="alice", scopes=ALICE_SCOPES,
                server_id=srv.server_id, tool_name="t1",
                args={"url": "http://10.0.0.1/x"},
            )

    @pytest.mark.asyncio
    async def test_before_call_budget_block(self, engine):
        from llm_budget_gateway.mcp_governance import (
            MCPRegistryRequest,
            ToolBudgetRequest,
            ToolInfo,
            ToolPolicyRequest,
        )

        engine._budgets._budgets.create_budget(
            ToolBudgetRequest(
                scope_kind="user", scope_key="alice",
                server_id="srv1", tool_name="t1", hard_limit=5.0,
            )
        )
        engine._budgets._tracker.spend = 9.0
        srv = engine._registry.register(
            MCPRegistryRequest(
                name="srv1", transport="stdio",
                tools=[ToolInfo(name="t1")],
            )
        )
        engine._policies.create_policy(
            ToolPolicyRequest(
                scope_kind="global", scope_key="default",
                server_id=srv.server_id, tool_name="t1",
                effect="allow",
            )
        )
        with pytest.raises(BudgetExceededError):
            await engine.before_call(
                caller="alice", scopes=ALICE_SCOPES,
                server_id=srv.server_id, tool_name="t1", args={},
            )

    @pytest.mark.asyncio
    async def test_after_call_writes_event_and_ledger(self, engine):
        from llm_budget_gateway.mcp_governance import MCPRegistryRequest, ToolInfo

        srv = engine._registry.register(
            MCPRegistryRequest(
                name="srv1", transport="stdio",
                tools=[ToolInfo(name="t1")],
            )
        )
        ctx = CallContext(
            call_id="c1",
            request_id=None,
            caller="alice",
            scopes=ALICE_SCOPES,
            server_id=srv.server_id,
            tool_name="t1",
            args_redacted={},
            decision="allowed",
            policy_id=None,
            approval_id=None,
            reason=None,
        )
        event = await engine.after_call(ctx, status="completed", cost=0.01, latency_ms=5)
        assert event.status == "completed"
        assert event.cost == 0.01
