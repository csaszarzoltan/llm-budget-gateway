"""Authenticated AgentOps API and responsive operations dashboard."""

from __future__ import annotations

import base64
import os
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

from .agentops_suite import (
    AuditChain,
    CarbonEstimator,
    ChangeRiskAssessor,
    CircuitBreakerPolicy,
    DelegationDepthPolicy,
    HumanApprovalGate,
    InjectionRiskScorer,
    LocaleNegotiator,
    MCPServerRegistry,
    ReplayProtector,
    ResidencyPolicy,
    SemanticCacheKey,
    SensitiveDataRedactor,
    SessionAffinity,
    SupportTriage,
    TaskCostMeter,
    TaskLease,
    TokenDensityMetric,
    ToolAccessPolicy,
    TraceSampler,
)

_NAMES = [
    "MCP registry",
    "Tool access",
    "Delegation depth",
    "Task leases",
    "Replay protection",
    "Session affinity",
    "Circuit breaker",
    "Semantic cache",
    "Data redaction",
    "Injection risk",
    "Human approval",
    "Audit chain",
    "Trace sampling",
    "Task cost",
    "Token density",
    "Carbon estimate",
    "Change risk",
    "Support triage",
    "Localization",
    "Residency",
]
PAGE = (
    """<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>AgentOps Center</title><style>:root{color-scheme:light;--bg:#f4f7fb;--card:#fff;--ink:#142039;--brand:#3157d5;--focus:#ffbf47}[data-theme=dark]{color-scheme:dark;--bg:#0b1120;--card:#182238;--ink:#f6f8ff;--brand:#89a5ff}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui}.skip{position:absolute;left:-999px}.skip:focus{left:1rem;top:1rem;background:var(--card);padding:1rem}main{max-width:1280px;margin:auto;padding:clamp(1rem,4vw,3rem)}header{display:flex;justify-content:space-between;gap:1rem}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:1rem}article{background:var(--card);padding:1rem;border-radius:16px;box-shadow:0 8px 26px #0002}button{min-height:44px;border:0;border-radius:10px;padding:.7rem 1rem;background:var(--brand);color:#fff}button:focus-visible,a:focus-visible{outline:3px solid var(--focus);outline-offset:3px}.skeleton{height:.8rem;background:#8885;border-radius:8px;animation:pulse 1.2s infinite}@keyframes pulse{50%{opacity:.4}}.empty,.error{border-left:4px solid var(--brand);padding:.7rem}.toast{position:fixed;right:1rem;bottom:1rem;background:var(--card);padding:1rem;border-radius:12px}@media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:560px){.grid{grid-template-columns:1fr}header{flex-direction:column}}@media(prefers-reduced-motion:reduce){*{animation:none!important}}</style></head><body><a class='skip' href='#main'>Skip to main content</a><main id='main'><header><div><strong>AgentOps Center 3.0</strong><h1>Safe agents, observable economics</h1></div><div><button aria-label='Toggle theme' onclick="let r=document.documentElement;r.dataset.theme=r.dataset.theme==='dark'?'light':'dark';toast('Theme changed')">Theme</button> <button aria-label='Refresh dashboard' onclick="toast('Dashboard refreshed')">Refresh</button></div></header><section class='grid'>"""
    + "".join(
        f"<article><h2>{x}</h2><p>Policy and evidence ready.</p></article>"
        for x in _NAMES
    )
    + """<article aria-busy='false'><div class='skeleton'></div><p class='empty'>No active agent risks.</p><p class='error' hidden>Unable to load. Retry safely.</p></article></section></main><div id='toast' class='toast' role='status' aria-live='polite' hidden></div><script>function toast(x){let t=document.getElementById('toast');t.textContent=x;t.hidden=false;setTimeout(()=>t.hidden=true,1800)}</script></body></html>"""
)


def create_agentops_app(api_key: str | None = None) -> FastAPI:
    """Create the fail-closed AgentOps application."""
    key = api_key if api_key is not None else os.getenv("GATEWAY_AGENTOPS_API_KEY", "")
    services: dict[str, Callable[[dict[str, Any]], dict[str, object]]] = {
        "mcp-registry": lambda b: MCPServerRegistry().register(**b),
        "tool-access": lambda b: ToolAccessPolicy().decide(**b),
        "delegation-depth": lambda b: DelegationDepthPolicy().evaluate(**b),
        "task-lease": lambda b: TaskLease().evaluate(**b),
        "replay-protection": lambda b: ReplayProtector().verify(
            base64.b64decode(b.pop("body_b64"), validate=True), **b
        ),
        "session-affinity": lambda b: SessionAffinity().choose(**b),
        "circuit-breaker": lambda b: CircuitBreakerPolicy().evaluate(**b),
        "semantic-cache": lambda b: SemanticCacheKey().build(**b),
        "redact": lambda b: SensitiveDataRedactor().redact(**b),
        "injection-risk": lambda b: InjectionRiskScorer().score(**b),
        "human-approval": lambda b: HumanApprovalGate().decide(**b),
        "audit-chain": lambda b: AuditChain().append(**b),
        "trace-sampling": lambda b: TraceSampler().decide(**b),
        "task-cost": lambda b: TaskCostMeter().calculate(**b),
        "token-density": lambda b: TokenDensityMetric().calculate(**b),
        "carbon": lambda b: CarbonEstimator().estimate(**b),
        "change-risk": lambda b: ChangeRiskAssessor().assess(**b),
        "support-triage": lambda b: SupportTriage().prioritize(**b),
        "locale": lambda b: LocaleNegotiator().choose(**b),
        "residency": lambda b: ResidencyPolicy().decide(**b),
    }
    app = FastAPI(title="Gateway AgentOps API", version="3.0.0")

    @app.get("/agentops", response_class=HTMLResponse)
    async def ui() -> str:
        """Render the responsive accessible AgentOps dashboard."""
        return PAGE

    @app.post("/v1/agentops/{capability}")
    async def execute(
        capability: str,
        body: dict[str, Any],
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict[str, object]:
        """Execute one authenticated AgentOps capability."""
        if not key:
            raise HTTPException(503, "AgentOps API key is not configured")
        if authorization != f"Bearer {key}" or not x_tenant_id:
            raise HTTPException(401, "authentication and tenant are required")
        service = services.get(capability)
        if service is None:
            raise HTTPException(404, "unknown AgentOps capability")
        try:
            return service(dict(body))
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    return app
