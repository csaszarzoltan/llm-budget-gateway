"""Regression tests for the mcp_governance security backlog (S2/S5-S16).

Each test class maps to one finding of security review t_085dfcdf. S1
(deny-default) and S3 (per-tool budgets) were fixed earlier at 37be03b;
M1-M7 code-quality majors are tracked separately on t_ac97a038. S16 and S8
are documentation decisions (single-tenant scope / accepted risk) — the
tests here pin the observable enforcement that backs them.
"""

import httpx
import pytest

from llm_budget_gateway.budget_enforcement import (
    BudgetScope,
    budget_window_seconds,
)
from llm_budget_gateway.mcp_governance import (
    ApprovalRequiredError,
    AuditStore,
    InvalidArgumentsError,
    MCPPolicyEngine,
    MCPRegistry,
    PIIRedactor,
    PolicyViolationError,
    SSRFGuard,
    ToolBudgetService,
    ToolPolicyStore,
    create_mcp_governance_app,
    open_mcp_db,
)
from llm_budget_gateway.mcp_governance.rules import ApprovalGate, ApprovalStore
from llm_budget_gateway.mcp_governance.schemas import (
    MCPRegistryRequest,
    ToolInfo,
    ToolPolicyRequest,
    _window_seconds,
)

AUTH_HEADERS = {"Authorization": "Bearer k", "X-Tenant-Id": "t"}

SERVER_BODY = {
    "name": "github-mcp",
    "transport": "http",
    "endpoint": "https://mcp.example.com/mcp",
    "version": "1.0.0",
    "description": "GitHub tooling",
    "tools": [
        {
            "name": "create_issue",
            "description": "Create a GitHub issue",
            "input_schema": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
            },
            "enabled": True,
        },
        {
            "name": "get_repo",
            "description": "Read repository metadata",
            "input_schema": {
                "type": "object",
                "properties": {"owner": {"type": "string"}},
            },
            "enabled": True,
        },
    ],
    "config": {"auth": "bearer"},
}

BUDGET_BODY = {
    "scope_kind": "user",
    "scope_key": "alice",
    "server_id": "srv123",
    "tool_name": "create_issue",
    "hard_limit": 5.0,
    "window": "30d",
}

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


def make_engine(conn, *, tracker=None):
    from llm_budget_gateway.mcp_governance import ToolBudgetStore

    store = ToolBudgetService(tracker or FakeTracker(), ToolBudgetStore(conn))
    return MCPPolicyEngine(
        registry=MCPRegistry(conn),
        policies=ToolPolicyStore(conn),
        budgets=store,
        audit=AuditStore(conn),
        approvals=ApprovalStore(conn),
        redactor=PIIRedactor(),
        ssrf=SSRFGuard(),
    )


def register_allowed_tool(engine, *, name="t1", input_schema=None):
    """Register srv1:t1 + a global allow policy; returns the server_id."""
    srv = engine._registry.register(
        MCPRegistryRequest(
            name="srv1",
            transport="stdio",
            tools=[ToolInfo(name=name, input_schema=input_schema or {})],
        )
    )
    engine._policies.create_policy(
        ToolPolicyRequest(
            scope_kind="global",
            scope_key="default",
            server_id=srv.server_id,
            tool_name=name,
            effect="allow",
        )
    )
    return srv.server_id


def run_sync_request(transport, method, url, **kwargs):
    """Run one HTTP request synchronously against an ASGI transport."""
    import asyncio

    async def _run():
        async with httpx.AsyncClient(transport=transport, base_url="http://x") as c:
            return await c.request(method, url, **kwargs)

    return asyncio.run(_run())


class TestS2InputSchemaValidation:
    """S2: tool args must be JSON-Schema validated in before_call (422)."""

    @pytest.mark.asyncio
    async def test_args_validated_against_registered_schema(self, conn):
        engine = make_engine(conn)
        schema = {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        }
        server_id = register_allowed_tool(engine, input_schema=schema)
        with pytest.raises(InvalidArgumentsError):
            await engine.before_call(
                caller="alice", scopes=ALICE_SCOPES,
                server_id=server_id, tool_name="t1",
                args={"owner": 42, "title": 7},
            )
        ctx = await engine.before_call(
            caller="alice", scopes=ALICE_SCOPES,
            server_id=server_id, tool_name="t1",
            args={"title": "ok"},
        )
        assert ctx.decision == "allowed"

    @pytest.mark.asyncio
    async def test_empty_schema_allows_any_args(self, conn):
        engine = make_engine(conn)
        server_id = register_allowed_tool(engine)
        ctx = await engine.before_call(
            caller="alice", scopes=ALICE_SCOPES,
            server_id=server_id, tool_name="t1",
            args={"anything": [1, 2, {"x": None}]},
        )
        assert ctx.decision == "allowed"

    def test_invalid_arguments_error_is_422(self):
        assert InvalidArgumentsError().status_code == 422


class TestS5GenericErrorBodies:
    """S5: 4xx bodies must not leak searched ids / internal state."""

    def test_unknown_approval_404_is_generic(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(
            tr, "POST", "/v1/mcp/approvals/nope/approve",
            headers=AUTH_HEADERS, json={"actor": "bob"},
        )
        assert r.status_code == 404
        assert r.json()["detail"] == "not found"
        assert "nope" not in str(r.json())

    def test_duplicate_server_409_is_generic(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        run_sync_request(tr, "POST", "/v1/mcp/servers", headers=AUTH_HEADERS, json=SERVER_BODY)
        r = run_sync_request(tr, "POST", "/v1/mcp/servers", headers=AUTH_HEADERS, json=SERVER_BODY)
        assert r.status_code == 409
        assert r.json()["detail"] == "conflict"
        assert "github-mcp" not in str(r.json())

    def test_unknown_server_404_is_generic(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(tr, "GET", "/v1/mcp/servers/nope", headers=AUTH_HEADERS)
        assert r.status_code == 404
        assert r.json()["detail"] == "not found"
        assert "nope" not in str(r.json())


class TestS6ListTools404:
    """S6: list_tools must 404 for an unknown server like get_server."""

    def test_list_tools_unknown_server_404(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(tr, "GET", "/v1/mcp/servers/ghost/tools", headers=AUTH_HEADERS)
        assert r.status_code == 404
        assert r.json()["detail"] == "not found"

    def test_list_tools_known_server_200(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(tr, "POST", "/v1/mcp/servers", headers=AUTH_HEADERS, json=SERVER_BODY)
        server_id = r.json()["server_id"]
        r = run_sync_request(
            tr, "GET", f"/v1/mcp/servers/{server_id}/tools", headers=AUTH_HEADERS
        )
        assert r.status_code == 200
        assert len(r.json()["data"]) == 2


class TestS9DemoSeedGated:
    """S9: the demo approval must be a test fixture, not production data."""

    def test_no_demo_approval_without_flag(self, monkeypatch):
        monkeypatch.delenv("MCP_GOVERNANCE_SEED_DEMO", raising=False)
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(
            tr, "POST", "/v1/mcp/approvals/aprv1/approve",
            headers=AUTH_HEADERS, json={"actor": "bob"},
        )
        assert r.status_code == 404

    def test_demo_approval_seeded_with_flag(self, monkeypatch):
        monkeypatch.setenv("MCP_GOVERNANCE_SEED_DEMO", "1")
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(
            tr, "POST", "/v1/mcp/approvals/aprv1/approve",
            headers=AUTH_HEADERS, json={"actor": "bob"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "approved"

    def test_fresh_start_report_has_zero_pending_approvals(self, monkeypatch):
        monkeypatch.delenv("MCP_GOVERNANCE_SEED_DEMO", raising=False)
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(tr, "GET", "/v1/mcp/report", headers=AUTH_HEADERS)
        assert r.status_code == 200
        assert r.json()["pending_approvals"] == 0


class TestS11AsyncSSRF:
    """S11: the async path must resolve DNS off the event loop (to_thread)."""

    @pytest.mark.asyncio
    async def test_acheck_blocks_private_resolution(self, monkeypatch):
        def fake_getaddrinfo(host, port, *a, **k):
            return [(0, 0, 0, "", ("10.9.9.9", port))]

        monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)
        guard = SSRFGuard()
        verdict = await guard.acheck({"url": "http://evil.example.com/x"})
        assert verdict.allowed is False
        assert "10.9.9.9" in verdict.reason

    @pytest.mark.asyncio
    async def test_acheck_blocks_unknown_host(self, monkeypatch):
        def fake_getaddrinfo(host, port, *a, **k):
            raise OSError("no address")

        monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)
        guard = SSRFGuard()
        verdict = await guard.acheck({"url": "http://nxdomain.example.com/x"})
        assert verdict.allowed is False

    @pytest.mark.asyncio
    async def test_acheck_ip_literal_blocked(self):
        guard = SSRFGuard()
        verdict = await guard.acheck({"url": "http://10.0.0.1/x"})
        assert verdict.allowed is False

    @pytest.mark.asyncio
    async def test_acheck_no_url_fields_allowed(self):
        guard = SSRFGuard()
        verdict = await guard.acheck({"title": "hello"})
        assert verdict.allowed is True

    @pytest.mark.asyncio
    async def test_engine_ssrf_gate_uses_async_path(self, conn):
        engine = make_engine(conn)
        server_id = register_allowed_tool(engine)
        with pytest.raises(PolicyViolationError):
            await engine.before_call(
                caller="alice", scopes=ALICE_SCOPES,
                server_id=server_id, tool_name="t1",
                args={"url": "http://127.0.0.1/x"},
            )


class TestS12WindowValidation:
    """S12: 0/negative budget windows must be rejected, not silently disable."""

    @pytest.mark.parametrize("window", ["0d", "0s", "0m", "0h", "-5d", "-1h"])
    def test_schema_window_seconds_rejects(self, window):
        with pytest.raises(ValueError):
            _window_seconds(window)

    @pytest.mark.parametrize("window", ["0d", "0s", "0m", "0h", "-5d", "-1h"])
    def test_budget_window_seconds_rejects(self, window):
        with pytest.raises(ValueError):
            budget_window_seconds(window)

    def test_positive_windows_still_accepted(self):
        assert _window_seconds("1h") == 3600
        assert budget_window_seconds("30d") == 30 * 86400
        assert budget_window_seconds("daily") == 86400

    def test_api_rejects_zero_window_budget(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        body = dict(BUDGET_BODY, window="0d")
        r = run_sync_request(tr, "POST", "/v1/mcp/budgets", headers=AUTH_HEADERS, json=body)
        assert r.status_code == 422


class TestS13ConstantTimeAuth:
    """S13: API key comparison via secrets.compare_digest (constant time)."""

    def test_exact_key_accepted(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(tr, "GET", "/v1/mcp/servers", headers=AUTH_HEADERS)
        assert r.status_code == 200

    @pytest.mark.parametrize(
        "bad",
        ["Bearer k ", "Bearer kk", "Bearer", "", "k", "bearer k"],
    )
    def test_key_variants_rejected(self, bad):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(
            tr, "GET", "/v1/mcp/servers",
            headers={"Authorization": bad, "X-Tenant-Id": "t"},
        )
        assert r.status_code == 401


class TestS14ArgsMustBeMapping:
    """S14: non-mapping args must be rejected with 422, not a 500 TypeError."""

    @pytest.mark.asyncio
    async def test_list_args_rejected_before_server_lookup(self, conn):
        engine = make_engine(conn)
        with pytest.raises(InvalidArgumentsError):
            await engine.before_call(
                caller="alice", scopes=ALICE_SCOPES,
                server_id="does-not-exist", tool_name="t1",
                args=["a", "b"],
            )

    @pytest.mark.asyncio
    async def test_string_args_rejected(self, conn):
        engine = make_engine(conn)
        with pytest.raises(InvalidArgumentsError):
            await engine.before_call(
                caller="alice", scopes=ALICE_SCOPES,
                server_id="srv1", tool_name="t1", args="nope",
            )

    @pytest.mark.asyncio
    async def test_mapping_args_accepted(self, conn):
        engine = make_engine(conn)
        server_id = register_allowed_tool(engine)
        ctx = await engine.before_call(
            caller="alice", scopes=ALICE_SCOPES,
            server_id=server_id, tool_name="t1", args={"x": 1},
        )
        assert ctx.decision == "allowed"


class TestS15AtomicApprovalConsume:
    """S15: find + consume must be a single transaction (no double-consume)."""

    def test_consume_approved_claims_once(self, conn):
        store = ApprovalStore(conn)
        gate = ApprovalGate(store)
        req = gate.create_request(
            policy=_approval_policy(), caller="alice", scopes=ALICE_SCOPES,
            server_id="srv1", tool_name="t1", args={"x": 1},
        )
        gate.approve(req.approval_id, "bob")
        claimed = gate.consume_approved(
            caller="alice", server_id="srv1", tool_name="t1", args_hash=req.args_hash
        )
        assert claimed is not None
        assert claimed.approval_id == req.approval_id
        assert claimed.status == "consumed"
        # A second concurrent caller must NOT get the same approval.
        again = gate.consume_approved(
            caller="alice", server_id="srv1", tool_name="t1", args_hash=req.args_hash
        )
        assert again is None

    def test_consume_approved_none_when_only_pending(self, conn):
        gate = ApprovalGate(ApprovalStore(conn))
        req = gate.create_request(
            policy=_approval_policy(), caller="alice", scopes=ALICE_SCOPES,
            server_id="srv1", tool_name="t1", args={"x": 1},
        )
        assert (
            gate.consume_approved(
                caller="alice", server_id="srv1", tool_name="t1",
                args_hash=req.args_hash,
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_engine_approval_flow_consumes_once(self, conn):
        engine = make_engine(conn)
        srv = engine._registry.register(
            MCPRegistryRequest(
                name="srv1", transport="stdio", tools=[ToolInfo(name="t1")]
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
        approval_id = exc.value.approval_id
        engine._gate.approve(approval_id, "bob")
        ctx = await engine.before_call(
            caller="alice", scopes=ALICE_SCOPES,
            server_id=srv.server_id, tool_name="t1", args={"x": 1},
        )
        assert ctx.decision == "approved"
        assert ctx.approval_id == approval_id
        # Approval is single-use: the next call requires a fresh one.
        with pytest.raises(ApprovalRequiredError):
            await engine.before_call(
                caller="alice", scopes=ALICE_SCOPES,
                server_id=srv.server_id, tool_name="t1", args={"x": 1},
            )


class TestS16TenantRequired:
    """S16: X-Tenant-Id is enforced; scoping is a documented single-tenant
    decision (docs/architecture/mcp-governance.md)."""

    def test_missing_tenant_header_401(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(
            tr, "GET", "/v1/mcp/servers", headers={"Authorization": "Bearer k"}
        )
        assert r.status_code == 401

    def test_tenant_supplied_accepted(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(tr, "GET", "/v1/mcp/servers", headers=AUTH_HEADERS)
        assert r.status_code == 200


def _approval_policy():
    from llm_budget_gateway.mcp_governance import ToolPolicy

    return ToolPolicy(
        policy_id="pol1",
        scope_kind="user",
        scope_key="alice",
        server_id="srv1",
        tool_name="t1",
        effect="approval",
        created_at=100,
    )
