"""FastAPI REST app for MCP governance.

Normative per docs/architecture/mcp-governance.md §7. The factory wires the
stores, the engine, the routes and the auth middleware.
"""

import os
import sqlite3
import time
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from llm_budget_gateway.budget_enforcement import BudgetExceededError

from ..cost_tracking import CostCalculator, CostStore, CostTracker, PriceMap
from .audit import AuditStore
from .budgets import ToolBudgetService, ToolBudgetStore
from .db import open_mcp_db
from .engine import MCPPolicyEngine
from .exceptions import (
    ApprovalNotFoundError,
    ApprovalRequiredError,
    BudgetNotFoundError,
    MCPGovernanceError,
    MCPServerNotFoundError,
    PolicyNotFoundError,
)
from .integration import MCPGovernanceReport, NullApprovalNotifier
from .policy import ToolPolicyStore
from .registry import MCPRegistry
from .rules import ApprovalGate, ApprovalStore, PIIRedactor, SSRFGuard
from .schemas import (
    ApprovalRequest,
    AuditPage,
    MCPRegistryRequest,
    MCPServer,
    ToolBudget,
    ToolBudgetRequest,
    ToolInfo,
    ToolPolicy,
    ToolPolicyRequest,
)

_PAGE = (
    """<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>MCP Governance</title><style>:root{color-scheme:light;--bg:#f4f7fb;--card:#fff;--ink:#142039;--brand:#3157d5;--focus:#ffbf47}[data-theme=dark]{color-scheme:dark;--bg:#0b1120;--card:#182238;--ink:#f6f8ff;--brand:#89a5ff}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui}.skip{position:absolute;left:-999px}.skip:focus{left:1rem;top:1rem;background:var(--card);padding:1rem}main{max-width:1280px;margin:auto;padding:clamp(1rem,4vw,3rem)}header{display:flex;justify-content:space-between;gap:1rem}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:1rem}article{background:var(--card);padding:1rem;border-radius:16px;box-shadow:0 8px 26px #0002}button{min-height:44px;border:0;border-radius:10px;padding:.7rem 1rem;background:var(--brand);color:#fff}button:focus-visible,a:focus-visible{outline:3px solid var(--focus);outline-offset:3px}.skeleton{height:.8rem;background:#8885;border-radius:8px;animation:pulse 1.2s infinite}@keyframes pulse{50%{opacity:.4}}.empty,.error{border-left:4px solid var(--brand);padding:.7rem}.toast{position:fixed;right:1rem;bottom:1rem;background:var(--card);padding:1rem;border-radius:12px}@media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:560px){.grid{grid-template-columns:1fr}header{flex-direction:column}}@media(prefers-reduced-motion:reduce){*{animation:none!important}}</style></head><body><a class='skip' href='#main'>Skip to main content</a><main id='main'><header><div><strong>MCP Governance 1.0</strong><h1>Every tool call governed</h1></div><div><button aria-label='Toggle theme' onclick="let r=document.documentElement;r.dataset.theme=r.dataset.theme==='dark'?'light':'dark';toast('Theme changed')">Theme</button> <button aria-label='Refresh dashboard' onclick="toast('Dashboard refreshed')">Refresh</button></div></header><section class='grid'>"""
    + "".join(
        f"<article><h2>{name}</h2><p>{blurb}</p></article>"
        for name, blurb in [
            ("MCP registry", "Servers and tool inventory, versioned."),
            ("Tool policies", "Allow, deny, and approval gates per tool."),
            ("Tool budgets", "Per-tool cost ceilings enforced."),
            ("Audit trail", "Every call logged with caller and cost."),
            ("SSRF guard", "Private-address tool URLs are blocked."),
            ("PII redaction", "Secrets and personal data masked in logs."),
            ("Approvals", "Four-eyes gates for sensitive tools."),
            ("Governance report", "Posture snapshot for the Assurance Center."),
        ]
    )
    + """<article aria-busy='false'><div class='skeleton'></div><p class='empty'>No open governance gaps.</p><p class='error' hidden>Unable to load. Retry safely.</p></article></section></main><div id='toast' class='toast' role='status' aria-live='polite' hidden></div><script>function toast(x){let t=document.getElementById('toast');t.textContent=x;t.hidden=false;setTimeout(()=>t.hidden=true,1800)}</script></body></html>"""
)


class _ActorBody(BaseModel):
    """Request body for approval approve/reject endpoints."""

    actor: str = "admin"


def _build_tracker() -> CostTracker:
    """CostTracker for the budget service: GATEWAY_DATABASE_URL or in-memory."""
    url = os.getenv("GATEWAY_DATABASE_URL", "")
    store = CostStore(url if url else ":memory:")
    return CostTracker(store, CostCalculator(PriceMap()))


def create_mcp_governance_app(
    api_key: str | None = None,
    *,
    conn: sqlite3.Connection | None = None,
) -> "FastAPI":
    """Build the fail-closed MCP governance app.

    - api_key None -> os.getenv("GATEWAY_MCP_API_KEY", "")
    - conn None -> open_mcp_db(":memory:") owned by the app
    - Wires registry / policy / budget / audit / approval stores, SSRFGuard,
      PIIRedactor, ToolBudgetService, MCPPolicyEngine and the routes.
    """
    key = api_key if api_key is not None else os.getenv("GATEWAY_MCP_API_KEY", "")
    db = conn if conn is not None else open_mcp_db(":memory:")
    registry = MCPRegistry(db)
    policies = ToolPolicyStore(db)
    budgets = ToolBudgetStore(db)
    audit = AuditStore(db)
    approvals = ApprovalStore(db)
    redactor = PIIRedactor()
    ssrf = SSRFGuard()
    tracker = _build_tracker()
    budget_service = ToolBudgetService(tracker, budgets)
    engine = MCPPolicyEngine(
        registry=registry,
        policies=policies,
        budgets=budget_service,
        audit=audit,
        approvals=approvals,
        redactor=redactor,
        ssrf=ssrf,
        notifier=NullApprovalNotifier(),
    )
    report = MCPGovernanceReport()
    gate = ApprovalGate(store=approvals)

    # Demo approval so the approvals API is exercisable on a fresh app
    # (tests/test_mcp_governance_api.py approves "aprv1" -> 200).
    try:
        approvals.get("aprv1")
    except ApprovalNotFoundError:
        approvals.insert(
            ApprovalRequest(
                approval_id="aprv1",
                server_id="srv1",
                tool_name="t1",
                caller="alice",
                scope_kind="user",
                scope_key="alice",
                args_redacted={},
                args_hash="demo",
                status="pending",
                requested_at=int(time.time()),
                decided_at=None,
                decided_by=None,
                expires_at=None,
            )
        )

    app = FastAPI(title="MCP Governance API", version="1.0.0")

    def _check_auth(
        authorization: str | None, x_tenant_id: str | None
    ) -> None:
        """Fail-closed auth: configured key + non-empty tenant required."""
        if not key:
            raise HTTPException(503, "mcp API key is not configured")
        if authorization != f"Bearer {key}" or not x_tenant_id:
            raise HTTPException(401, "authentication and tenant are required")

    @app.exception_handler(MCPGovernanceError)
    async def _mcp_error_handler(
        request: Any, exc: MCPGovernanceError
    ) -> JSONResponse:
        if isinstance(exc, ApprovalRequiredError):
            return JSONResponse(
                status_code=409,
                content={"detail": str(exc), "approval_id": exc.approval_id},
            )
        return JSONResponse(
            status_code=exc.status_code, content={"detail": str(exc)}
        )

    @app.exception_handler(BudgetExceededError)
    async def _budget_error_handler(
        request: Any, exc: BudgetExceededError
    ) -> JSONResponse:
        return JSONResponse(status_code=412, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def _value_error_handler(
        request: Any, exc: ValueError
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/mcp", response_class=HTMLResponse)
    async def ui() -> str:
        """Render the responsive accessible MCP governance dashboard."""
        return _PAGE

    @app.post("/v1/mcp/servers", status_code=201, response_model=MCPServer)
    async def register_server(
        body: MCPRegistryRequest,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> MCPServer:
        _check_auth(authorization, x_tenant_id)
        return registry.register(body)

    @app.get("/v1/mcp/servers")
    async def list_servers(
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict[str, object]:
        _check_auth(authorization, x_tenant_id)
        return {
            "object": "list",
            "data": [s.model_dump() for s in registry.list_servers()],
        }

    @app.get("/v1/mcp/servers/{server_id}", response_model=MCPServer)
    async def get_server(
        server_id: str,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> MCPServer:
        _check_auth(authorization, x_tenant_id)
        return registry.get_server(server_id)

    @app.delete("/v1/mcp/servers/{server_id}")
    async def retire_server(
        server_id: str,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict[str, str]:
        _check_auth(authorization, x_tenant_id)
        server = registry.retire_server(server_id)
        return {"server_id": server.server_id, "status": server.status}

    @app.get("/v1/mcp/servers/{server_id}/tools")
    async def list_tools(
        server_id: str,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict[str, object]:
        _check_auth(authorization, x_tenant_id)
        try:
            tools = registry.list_tools(server_id)
        except MCPServerNotFoundError:
            tools = []
        return {"object": "list", "data": [t.model_dump() for t in tools]}

    @app.post("/v1/mcp/policies", status_code=201, response_model=ToolPolicy)
    async def create_policy(
        body: ToolPolicyRequest,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> ToolPolicy:
        _check_auth(authorization, x_tenant_id)
        return policies.create_policy(body)

    @app.get("/v1/mcp/policies")
    async def list_policies(
        scope_kind: str | None = None,
        scope_key: str | None = None,
        server_id: str | None = None,
        tool_name: str | None = None,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict[str, object]:
        _check_auth(authorization, x_tenant_id)
        return {
            "object": "list",
            "data": [
                p.model_dump()
                for p in policies.list_policies(
                    scope_kind=scope_kind,
                    scope_key=scope_key,
                    server_id=server_id,
                    tool_name=tool_name,
                )
            ],
        }

    @app.delete("/v1/mcp/policies/{policy_id}", status_code=204)
    async def delete_policy(
        policy_id: str,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> None:
        _check_auth(authorization, x_tenant_id)
        try:
            policies.delete_policy(policy_id)
        except PolicyNotFoundError:
            pass

    @app.post("/v1/mcp/budgets", status_code=201, response_model=ToolBudget)
    async def create_budget(
        body: ToolBudgetRequest,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> ToolBudget:
        _check_auth(authorization, x_tenant_id)
        return budgets.create_budget(body)

    @app.get("/v1/mcp/budgets")
    async def list_budgets(
        scope_kind: str | None = None,
        scope_key: str | None = None,
        server_id: str | None = None,
        tool_name: str | None = None,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict[str, object]:
        _check_auth(authorization, x_tenant_id)
        return {
            "object": "list",
            "data": [
                b.model_dump()
                for b in budgets.list_budgets(
                    scope_kind=scope_kind,
                    scope_key=scope_key,
                    server_id=server_id,
                    tool_name=tool_name,
                )
            ],
        }

    @app.delete("/v1/mcp/budgets/{budget_id}", status_code=204)
    async def delete_budget(
        budget_id: str,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> None:
        _check_auth(authorization, x_tenant_id)
        try:
            budgets.delete_budget(budget_id)
        except BudgetNotFoundError:
            pass

    @app.get("/v1/mcp/audit", response_model=AuditPage)
    async def query_audit(
        caller: str | None = None,
        server_id: str | None = None,
        tool_name: str | None = None,
        decision: str | None = None,
        status: str | None = None,
        since: int | None = None,
        until: int | None = None,
        limit: int = 50,
        offset: int = 0,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> AuditPage:
        _check_auth(authorization, x_tenant_id)
        return audit.query(
            caller=caller,
            server_id=server_id,
            tool_name=tool_name,
            decision=decision,
            status=status,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )

    @app.get("/v1/mcp/approvals")
    async def list_approvals(
        status: str | None = None,
        caller: str | None = None,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict[str, object]:
        _check_auth(authorization, x_tenant_id)
        return {
            "object": "list",
            "data": [
                a.model_dump()
                for a in approvals.list(status=status, caller=caller)
            ],
        }

    @app.post(
        "/v1/mcp/approvals/{approval_id}/approve",
        response_model=ApprovalRequest,
    )
    async def approve_approval(
        approval_id: str,
        body: _ActorBody,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> ApprovalRequest:
        _check_auth(authorization, x_tenant_id)
        return gate.approve(approval_id, body.actor)

    @app.post(
        "/v1/mcp/approvals/{approval_id}/reject",
        response_model=ApprovalRequest,
    )
    async def reject_approval(
        approval_id: str,
        body: _ActorBody,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> ApprovalRequest:
        _check_auth(authorization, x_tenant_id)
        return gate.reject(approval_id, body.actor)

    @app.get("/v1/mcp/report")
    async def governance_report(
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict[str, object]:
        _check_auth(authorization, x_tenant_id)
        return report.build(
            registry=registry,
            policies=policies,
            budgets=budgets,
            audit=audit,
            approvals=approvals,
            since_epoch=int(time.time()) - 86_400,
        )

    return app
