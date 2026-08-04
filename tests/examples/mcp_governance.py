"""MCP server governance: registry, per-tool policies, budgets, audit, REST.

Demonstrates the public ``mcp_governance`` API without a network: server
registration + tool inventory, allow/deny/approval policies with the
deny-by-default evaluator, per-tool cost ceilings against a stubbed cost
ledger, the audit trail, SSRF + PII rules, the policy engine
(``before_call`` / ``after_call``), and the REST API over
``httpx.ASGITransport`` (no server process needed).

The budget demo uses a stubbed cost tracker (like the test suite) so spend
is controllable; the REST section boots ``create_mcp_governance_app``
in-memory and exercises the endpoints exactly as a real deployment would
(with bearer + ``X-Tenant-Id`` auth).

Usage:
    .venv/bin/python examples/mcp_governance.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from llm_budget_gateway.budget_enforcement import BudgetExceededError, BudgetScope
from llm_budget_gateway.mcp_governance import (
    AccessDeniedError,
    ApprovalGate,
    ApprovalRequiredError,
    AuditStore,
    MCPGovernanceReport,
    MCPPolicyEngine,
    MCPRegistry,
    MCPRegistryRequest,
    PIIRedactor,
    PolicyEvaluator,
    SSRFGuard,
    ToolBudgetRequest,
    ToolBudgetService,
    ToolBudgetStore,
    ToolInfo,
    ToolPolicyRequest,
    ToolPolicyStore,
    create_mcp_governance_app,
    open_mcp_db,
)
from llm_budget_gateway.mcp_governance.rules import ApprovalStore


class FakeTracker:
    """Minimal async stand-in for CostTracker (mirrors the test suite).

    ``spend_since`` returns the configured spend (per scope), ``record``
    collects UsageRecords so we can show ledger attribution.
    """

    def __init__(self, spend: float = 0.0) -> None:
        self.spend = spend
        self.records: list = []

    async def spend_since(
        self, scope_key: str, since_epoch: int, tool_name: str | None = None
    ) -> float:
        return self.spend

    async def record(self, usage) -> None:
        self.records.append(usage)


def registry_demo() -> None:
    print("== MCP server registry ==")
    db = open_mcp_db(":memory:")
    registry = MCPRegistry(db)
    server = registry.register(
        MCPRegistryRequest(
            name="github-mcp",
            transport="http",
            endpoint="https://mcp.example.com/mcp",
            version="1.0.0",
            description="GitHub tooling",
            tools=[
                ToolInfo(
                    name="create_issue",
                    description="Create a GitHub issue",
                    input_schema={
                        "type": "object",
                        "properties": {"title": {"type": "string"}},
                    },
                ),
                ToolInfo(
                    name="get_repo",
                    description="Read repository metadata",
                    input_schema={
                        "type": "object",
                        "properties": {"owner": {"type": "string"}},
                    },
                ),
            ],
            config={"auth": "bearer"},
        )
    )
    print(f"  registered {server.name} v{server.version} -> server_id={server.server_id}")
    print(f"  transport={server.transport} endpoint={server.endpoint} status={server.status}")
    tools = registry.list_tools(server.server_id)
    print(f"  tools ({len(tools)}): {[t.name for t in tools]}")
    # Versioning: same name, newer version is a separate row; latest wins.
    registry.register(
        MCPRegistryRequest(
            name="github-mcp",
            transport="http",
            endpoint="https://mcp.example.com/mcp",
            version="1.1.0",
        )
    )
    print(f"  list_servers -> latest version: {registry.list_servers()[0].version}")
    retired = registry.retire_server(server.server_id)
    print(f"  retired {server.name}: status={retired.status}")
    registry.list_servers()  # retired servers are hidden by default
    print(f"  active servers after retire: {len(registry.list_servers())}")
    db.close()


def policy_demo() -> None:
    print("== per-tool policies (deny by default) ==")
    db = open_mcp_db(":memory:")
    registry = MCPRegistry(db)
    policies = ToolPolicyStore(db)  # default_effect="deny"
    server = registry.register(
        MCPRegistryRequest(
            name="github-mcp",
            transport="http",
            endpoint="https://mcp.example.com/mcp",
            version="1.0.0",
            tools=[ToolInfo(name="create_issue"), ToolInfo(name="delete_repo")],
        )
    )
    alice = [BudgetScope("user", "alice"), BudgetScope("team", "eng")]
    # No policy yet -> default deny.
    evaluator = PolicyEvaluator(policies)
    decision = evaluator.decide(
        scopes=alice, server_id=server.server_id, tool_name="create_issue"
    )
    print(f"  no policy:        effect={decision.effect} ({decision.reason})")
    policies.create_policy(
        ToolPolicyRequest(
            scope_kind="user",
            scope_key="alice",
            server_id=server.server_id,
            tool_name="create_issue",
            effect="allow",
            description="Alice may create issues",
        )
    )
    decision = evaluator.decide(
        scopes=alice, server_id=server.server_id, tool_name="create_issue"
    )
    print(f"  allow policy:     effect={decision.effect} ({decision.reason})")
    decision = evaluator.decide(
        scopes=alice, server_id=server.server_id, tool_name="delete_repo"
    )
    print(f"  other tool:       effect={decision.effect} ({decision.reason})")
    policies.create_policy(
        ToolPolicyRequest(
            scope_kind="user",
            scope_key="alice",
            server_id=server.server_id,
            tool_name="delete_repo",
            effect="approval",
            description="Deletes need four eyes",
        )
    )
    decision = evaluator.decide(
        scopes=alice, server_id=server.server_id, tool_name="delete_repo"
    )
    print(f"  approval policy:  effect={decision.effect} ({decision.reason})")
    db.close()


async def budget_demo() -> None:
    print("== per-tool budgets (soft/hard ceilings) ==")
    db = open_mcp_db(":memory:")
    budgets = ToolBudgetStore(db)
    tracker = FakeTracker(spend=0.0)
    service = ToolBudgetService(tracker, budgets)
    alice = [BudgetScope("user", "alice")]
    budgets.create_budget(
        ToolBudgetRequest(
            scope_kind="user",
            scope_key="alice",
            hard_limit=5.0,
            window="30d",
        )
    )
    await service.check(alice, server_id="srv1", tool_name="create_issue")
    print("  spend $0.00 < hard $5.00 -> check passes (no raise)")
    tracker.spend = 6.0
    try:
        await service.check(alice, server_id="srv1", tool_name="create_issue")
    except BudgetExceededError as exc:
        print(
            f"  spend ${exc.spend:.2f} >= hard ${exc.limit:.2f} -> "
            f"BudgetExceededError (HTTP 412)"
        )
    db.close()


def rules_demo() -> None:
    print("== SSRF guard + PII redaction ==")
    ssrf = SSRFGuard()
    verdict = ssrf.check({"url": "https://api.github.com/repos/csaszarzoltan/llm-budget-gateway"})
    print(f"  public url:   allowed={verdict.allowed} ({verdict.reason})")
    verdict = ssrf.check({"webhook": "http://169.254.169.254/latest/meta-data/"})
    print(f"  link-local:   allowed={verdict.allowed} ({verdict.reason})")
    verdict = ssrf.check({"url": "http://10.0.0.5/admin"})
    print(f"  private 10.x: allowed={verdict.allowed} ({verdict.reason})")
    redactor = PIIRedactor()
    redacted = redactor.redact(
        {"email": "alice@example.com", "nested": {"token": "sk-ant-1234567890abcdef1234567890abcdef"}}
    )
    print(f"  pii redacted: {redacted}")


async def engine_demo() -> None:
    print("== policy engine: before_call / after_call ==")
    db = open_mcp_db(":memory:")
    registry = MCPRegistry(db)
    policies = ToolPolicyStore(db)
    budget_store = ToolBudgetStore(db)
    budgets = ToolBudgetService(FakeTracker(0.0), budget_store)
    audit = AuditStore(db)
    approvals = ApprovalStore(db)
    engine = MCPPolicyEngine(
        registry=registry,
        policies=policies,
        budgets=budgets,
        audit=audit,
        approvals=approvals,
        redactor=PIIRedactor(),
        ssrf=SSRFGuard(),
    )
    server = registry.register(
        MCPRegistryRequest(
            name="github-mcp",
            transport="http",
            endpoint="https://mcp.example.com/mcp",
            version="1.0.0",
            tools=[ToolInfo(name="create_issue"), ToolInfo(name="delete_repo")],
        )
    )
    alice = [BudgetScope("user", "alice"), BudgetScope("team", "eng")]
    # Deny path (default deny, no policy).
    try:
        await engine.before_call(
            caller="alice", scopes=alice, server_id=server.server_id,
            tool_name="create_issue", args={"title": "Fix the bug"},
        )
    except AccessDeniedError as exc:
        print(f"  deny path:    AccessDeniedError: {exc}")
    # Allow path + after_call ledger attribution.
    policies.create_policy(
        ToolPolicyRequest(
            scope_kind="user", scope_key="alice",
            server_id=server.server_id, tool_name="create_issue",
            effect="allow",
        )
    )
    ctx = await engine.before_call(
        caller="alice", scopes=alice, server_id=server.server_id,
        tool_name="create_issue", args={"title": "Fix the bug"},
    )
    print(f"  allow path:   decision={ctx.decision} call_id={ctx.call_id}")
    event = await engine.after_call(ctx, status="completed", cost=0.0042, latency_ms=812)
    print(f"  after_call:   audit event decision={event.decision} status={event.status} "
          f"cost=${event.cost:.4f} redacted={event.redacted}")
    # Approval path: policy effect=approval -> blocked until a human approves.
    policies.create_policy(
        ToolPolicyRequest(
            scope_kind="user", scope_key="alice",
            server_id=server.server_id, tool_name="delete_repo",
            effect="approval",
        )
    )
    try:
        await engine.before_call(
            caller="alice", scopes=alice, server_id=server.server_id,
            tool_name="delete_repo", args={"repo": "llm-budget-gateway"},
        )
    except ApprovalRequiredError as exc:
        print(f"  approval req: ApprovalRequiredError approval_id={exc.approval_id}")
        ApprovalGate(store=approvals).approve(exc.approval_id, actor="bob")
        ctx = await engine.before_call(
            caller="alice", scopes=alice, server_id=server.server_id,
            tool_name="delete_repo", args={"repo": "llm-budget-gateway"},
        )
        print(f"  after approve: decision={ctx.decision} (consumed one-time approval)")
    page = audit.query(limit=10)
    decisions = sorted({e.decision for e in page.data})
    print(f"  audit rows:   {page.total} (decisions={decisions})")
    report = MCPGovernanceReport().build(
        registry=registry, policies=policies, budgets=budget_store,
        audit=audit, approvals=approvals, since_epoch=0,
    )
    print(f"  report:       risk_tier={report['risk_tier']} total_tools={report['total_tools']} "
          f"tools_with_policy={report['tools_with_policy']}")
    db.close()


async def rest_demo() -> None:
    print("== REST API (httpx ASGITransport, no server process) ==")
    app = create_mcp_governance_app("k")
    headers = {"Authorization": "Bearer k", "X-Tenant-Id": "t"}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/v1/mcp/servers",
            headers=headers,
            json={
                "name": "github-mcp",
                "transport": "http",
                "endpoint": "https://mcp.example.com/mcp",
                "version": "1.0.0",
                "tools": [{"name": "create_issue", "enabled": True}],
            },
        )
        print(f"  POST /v1/mcp/servers -> {r.status_code} (server_id={r.json()['server_id']})")
        r = await client.get("/v1/mcp/servers", headers=headers)
        print(f"  GET  /v1/mcp/servers -> {r.status_code} data={len(r.json()['data'])}")
        r = await client.post(
            "/v1/mcp/policies",
            headers=headers,
            json={
                "scope_kind": "user",
                "scope_key": "alice",
                "server_id": "srv123",
                "tool_name": "create_issue",
                "effect": "allow",
            },
        )
        print(f"  POST /v1/mcp/policies -> {r.status_code}")
        r = await client.get("/v1/mcp/audit", headers=headers)
        print(f"  GET  /v1/mcp/audit -> {r.status_code} total={r.json()['total']}")
        r = await client.get("/v1/mcp/report", headers=headers)
        body = r.json()
        print(f"  GET  /v1/mcp/report -> {r.status_code} risk_tier={body['risk_tier']}")
        # Fail closed: missing tenant header -> 401.
        r = await client.get("/v1/mcp/servers", headers={"Authorization": "Bearer k"})
        print(f"  GET  /v1/mcp/servers (no X-Tenant-Id) -> {r.status_code}")


async def main() -> None:
    registry_demo()
    policy_demo()
    await budget_demo()
    rules_demo()
    await engine_demo()
    await rest_demo()


if __name__ == "__main__":
    asyncio.run(main())
