# ruff: noqa: F403,F405
"""Authenticated Fleet Governance API and responsive control-center UI."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

from .fleet_suite import *  # noqa: F403

_CAPABILITIES = [
    "Identity cards",
    "Fleet inventory",
    "Lifecycle",
    "Credential expiry",
    "Capability grants",
    "Platform authorization",
    "Kill switch",
    "Policy simulation",
    "Blast radius",
    "Human responsibility",
    "Evidence bundles",
    "Policy coverage",
    "Shadow agents",
    "Cost ceilings",
    "Runaway detection",
    "Outcome economics",
    "Model tiers",
    "Tool costs",
    "Data readiness",
    "Reproducibility",
    "Compliance crosswalk",
]
PAGE = (
    """<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Fleet Governance</title><style>:root{color-scheme:light;--bg:#f4f7fb;--card:#fff;--ink:#142039;--brand:#3157d5;--focus:#ffbf47}[data-theme=dark]{color-scheme:dark;--bg:#0b1120;--card:#182238;--ink:#f6f8ff;--brand:#89a5ff}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui}.skip{position:absolute;left:-999px}.skip:focus{left:1rem;top:1rem;background:var(--card);padding:1rem}main{max-width:1280px;margin:auto;padding:clamp(1rem,4vw,3rem)}header{display:flex;justify-content:space-between;gap:1rem}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:1rem}article{background:var(--card);padding:1rem;border-radius:16px;box-shadow:0 8px 26px #0002}button{min-height:44px;border:0;border-radius:10px;padding:.7rem 1rem;background:var(--brand);color:#fff}button:focus-visible,a:focus-visible{outline:3px solid var(--focus);outline-offset:3px}.skeleton{height:.8rem;background:#8885;border-radius:8px;animation:pulse 1.2s infinite}@keyframes pulse{50%{opacity:.4}}.empty,.error{border-left:4px solid var(--brand);padding:.7rem}.toast{position:fixed;right:1rem;bottom:1rem;background:var(--card);padding:1rem;border-radius:12px}@media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:560px){.grid{grid-template-columns:1fr}header{flex-direction:column}}@media(prefers-reduced-motion:reduce){*{animation:none!important}}</style></head><body><a class='skip' href='#main'>Skip to main content</a><main id='main'><header><div><strong>Fleet Governance 4.0</strong><h1>Accountable digital workers</h1></div><div><button aria-label='Toggle theme' onclick="let r=document.documentElement;r.dataset.theme=r.dataset.theme==='dark'?'light':'dark';toast('Theme changed')">Theme</button> <button aria-label='Refresh dashboard' onclick="toast('Dashboard refreshed')">Refresh</button></div></header><section class='grid'>"""
    + "".join(
        f"<article><h2>{x}</h2><p>Governed and evidence-ready.</p></article>"
        for x in _CAPABILITIES
    )
    + """<article aria-busy='false'><div class='skeleton'></div><p class='empty'>No fleet risks.</p><p class='error' hidden>Unable to load. Retry safely.</p></article></section></main><div id='toast' class='toast' role='status' aria-live='polite' hidden></div><script>function toast(x){let t=document.getElementById('toast');t.textContent=x;t.hidden=false;setTimeout(()=>t.hidden=true,1800)}</script></body></html>"""
)


def create_fleet_app(api_key: str | None = None) -> FastAPI:
    """Create the fail-closed fleet-governance application."""
    key = api_key if api_key is not None else os.getenv("GATEWAY_FLEET_API_KEY", "")
    services: dict[str, Callable[[dict[str, Any]], dict[str, object]]] = {
        "identity": lambda b: AgentIdentityCard().issue(**b),
        "inventory": lambda b: AgentInventory().summarize(b["agents"]),
        "lifecycle": lambda b: LifecyclePolicy().transition(**b),
        "credential-expiry": lambda b: CredentialExpiry().evaluate(**b),
        "capability-grant": lambda b: CapabilityGrant().decide(**b),
        "platform-authorization": lambda b: PlatformAuthorization().decide(**b),
        "kill-switch": lambda b: KillSwitch().decide(**b),
        "policy-simulation": lambda b: PolicySimulation().compare(**b),
        "blast-radius": lambda b: BlastRadiusEstimator().estimate(**b),
        "responsibility": lambda b: HumanResponsibility().resolve(**b),
        "evidence": lambda b: EvidenceBundle().build(b["artifacts"]),
        "policy-coverage": lambda b: PolicyCoverage().calculate(**b),
        "shadow-agents": lambda b: ShadowAgentDetector().detect(**b),
        "cost-ceiling": lambda b: CostCeiling().decide(**b),
        "runaway": lambda b: RunawayDetector().detect(**b),
        "outcome-economics": lambda b: OutcomeEconomics().calculate(**b),
        "model-tier": lambda b: ModelTierPolicy().choose(**b),
        "tool-costs": lambda b: ToolCostLedger().aggregate(b["calls"]),
        "data-readiness": lambda b: DataReadiness().assess(**b),
        "reproducibility": lambda b: ReproducibilityRecord().build(**b),
        "compliance": lambda b: ComplianceCrosswalk().evaluate(**b),
    }  # noqa:E501
    app = FastAPI(title="Gateway Fleet Governance API", version="4.0.0")

    @app.get("/fleet", response_class=HTMLResponse)
    async def ui() -> str:
        """Render the responsive accessible fleet dashboard."""
        return PAGE

    @app.post("/v1/fleet/{capability}")
    async def execute(
        capability: str,
        body: dict[str, Any],
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict[str, object]:
        """Execute one authenticated fleet-governance capability."""
        if not key:
            raise HTTPException(503, "fleet API key is not configured")
        if authorization != f"Bearer {key}" or not x_tenant_id:
            raise HTTPException(401, "authentication and tenant are required")
        service = services.get(capability)
        if service is None:
            raise HTTPException(404, "unknown fleet capability")
        try:
            return service(dict(body))
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    return app
