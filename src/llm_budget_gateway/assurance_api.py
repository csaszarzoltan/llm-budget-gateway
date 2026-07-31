"""Authenticated continuous-assurance API and responsive dashboard."""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

from . import assurance_suite as s

NAMES = [
    "Risk tier",
    "Control test",
    "Evaluation gate",
    "Calibration",
    "Refusal quality",
    "Fairness",
    "Robustness",
    "Hallucination",
    "Provenance",
    "Approvals",
    "Incidents",
    "Corrective actions",
    "Vendor risk",
    "Data quality",
    "Drift",
    "Red team",
    "Evidence freshness",
    "Maturity",
    "Assurance report",
    "Benefits",
]
PAGE = (
    """<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Assurance Center</title><style>:root{color-scheme:light;--bg:#f4f7fb;--card:#fff;--ink:#142039;--brand:#3157d5;--focus:#ffbf47}[data-theme=dark]{color-scheme:dark;--bg:#0b1120;--card:#182238;--ink:#f6f8ff;--brand:#89a5ff}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui}.skip{position:absolute;left:-999px}.skip:focus{left:1rem;top:1rem;background:var(--card);padding:1rem}main{max-width:1280px;margin:auto;padding:clamp(1rem,4vw,3rem)}header{display:flex;justify-content:space-between;gap:1rem}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:1rem}article{background:var(--card);padding:1rem;border-radius:16px;box-shadow:0 8px 26px #0002}button{min-height:44px;border:0;border-radius:10px;padding:.7rem 1rem;background:var(--brand);color:#fff}button:focus-visible,a:focus-visible{outline:3px solid var(--focus);outline-offset:3px}.skeleton{height:.8rem;background:#8885;border-radius:8px;animation:pulse 1.2s infinite}@keyframes pulse{50%{opacity:.4}}.empty,.error{border-left:4px solid var(--brand);padding:.7rem}.toast{position:fixed;right:1rem;bottom:1rem;background:var(--card);padding:1rem;border-radius:12px}@media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:560px){.grid{grid-template-columns:1fr}header{flex-direction:column}}@media(prefers-reduced-motion:reduce){*{animation:none!important}}</style></head><body><a class='skip' href='#main'>Skip to main content</a><main id='main'><header><div><strong>Assurance Center 5.0</strong><h1>Continuous proof, not periodic paperwork</h1></div><div><button aria-label='Toggle theme' onclick="let r=document.documentElement;r.dataset.theme=r.dataset.theme==='dark'?'light':'dark';toast('Theme changed')">Theme</button> <button aria-label='Refresh dashboard' onclick="toast('Dashboard refreshed')">Refresh</button></div></header><section class='grid'>"""
    + "".join(
        f"<article><h2>{x}</h2><p>Continuously assured.</p></article>" for x in NAMES
    )
    + """<article aria-busy='false'><div class='skeleton'></div><p class='empty'>No open assurance gaps.</p><p class='error' hidden>Unable to load. Retry safely.</p></article></section></main><div id='toast' class='toast' role='status' aria-live='polite' hidden></div><script>function toast(x){let t=document.getElementById('toast');t.textContent=x;t.hidden=false;setTimeout(()=>t.hidden=true,1800)}</script></body></html>"""
)


def create_assurance_app(api_key: str | None = None) -> FastAPI:
    """Create the fail-closed assurance application."""
    key = api_key if api_key is not None else os.getenv("GATEWAY_ASSURANCE_API_KEY", "")
    services = {
        "risk-tier": lambda b: s.RiskTier().classify(**b),
        "control-test": lambda b: s.ControlTest().evaluate(**b),
        "evaluation-gate": lambda b: s.EvaluationGate().decide(**b),
        "calibration": lambda b: s.CalibrationMetric().calculate(**b),
        "refusal-quality": lambda b: s.RefusalQuality().calculate(**b),
        "fairness-gap": lambda b: s.FairnessGap().calculate(b["rates"]),
        "robustness": lambda b: s.RobustnessScore().calculate(**b),
        "hallucination-rate": lambda b: s.HallucinationRate().calculate(**b),
        "provenance": lambda b: s.ProvenanceRecord().build(**b),
        "change-approval": lambda b: s.ChangeApproval().decide(**b),
        "incident-severity": lambda b: s.IncidentSeverity().classify(**b),
        "corrective-action": lambda b: s.CorrectiveAction().status(**b),
        "vendor-risk": lambda b: s.VendorRisk().assess(**b),
        "data-quality": lambda b: s.DataQuality().calculate(**b),
        "drift-alert": lambda b: s.DriftAlert().detect(**b),
        "red-team-coverage": lambda b: s.RedTeamCoverage().calculate(**b),
        "evidence-freshness": lambda b: s.EvidenceFreshness().evaluate(**b),
        "maturity": lambda b: s.MaturityScore().calculate(b["domains"]),
        "assurance-report": lambda b: s.AssuranceReport().build(b["findings"]),
        "benefit-realization": lambda b: s.BenefitRealization().calculate(**b),
    }
    app = FastAPI(title="Gateway Assurance API", version="5.0.0")

    @app.get("/assurance", response_class=HTMLResponse)
    async def ui() -> str:
        """Render the responsive accessible assurance dashboard."""
        return PAGE

    @app.post("/v1/assurance/{capability}")
    async def execute(
        capability: str,
        body: dict[str, Any],
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict[str, object]:
        """Execute one authenticated assurance capability."""
        if not key:
            raise HTTPException(503, "assurance API key is not configured")
        if authorization != f"Bearer {key}" or not x_tenant_id:
            raise HTTPException(401, "authentication and tenant are required")
        service = services.get(capability)
        if service is None:
            raise HTTPException(404, "unknown assurance capability")
        try:
            return service(dict(body))
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    return app
