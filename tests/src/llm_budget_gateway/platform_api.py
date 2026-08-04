"""Authenticated API and responsive UI for the enterprise platform suite."""

from __future__ import annotations

import base64
import os
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

from .platform_suite import (
    AdoptionFunnel,
    AlertRuleEvaluator,
    CanaryPlanner,
    ContractCompatibility,
    CostAllocator,
    DatasetCurator,
    DLPClassifier,
    ExportManifest,
    FeedbackAggregator,
    IncidentDigest,
    ModelCatalog,
    PromptCatalog,
    ProviderScorecard,
    QualityDriftDetector,
    QuotaPlanner,
    RegionRouter,
    RetentionPolicy,
    RollbackDecision,
    SLOCalculator,
    UsageTagger,
)

PAGE = (
    """<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Platform Center</title><style>:root{color-scheme:light;--bg:#f3f6fc;--card:#fff;--ink:#172033;--muted:#53617a;--brand:#3157d5;--focus:#ffbf47}[data-theme=dark]{color-scheme:dark;--bg:#0c1220;--card:#182238;--ink:#f5f7ff;--muted:#bdc7dc;--brand:#89a5ff}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui}.skip{position:absolute;left:-999px}.skip:focus{left:1rem;top:1rem;background:var(--card);padding:1rem;z-index:2}main{max-width:1280px;margin:auto;padding:clamp(1rem,4vw,3rem)}header{display:flex;justify-content:space-between;align-items:center;gap:1rem}.actions{display:flex;gap:.5rem;flex-wrap:wrap}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:1rem}article{background:var(--card);border:1px solid color-mix(in srgb,var(--muted) 25%,transparent);border-radius:16px;padding:1rem;box-shadow:0 8px 28px #0002}button{min-height:44px;border:0;border-radius:10px;padding:.7rem 1rem;background:var(--brand);color:#fff}button:focus-visible,a:focus-visible{outline:3px solid var(--focus);outline-offset:3px}.skeleton{height:.8rem;background:color-mix(in srgb,var(--muted) 25%,transparent);border-radius:6px;animation:pulse 1.2s infinite}@keyframes pulse{50%{opacity:.4}}.empty,.error{border-left:4px solid var(--brand);padding:.75rem}.toast{position:fixed;right:1rem;bottom:1rem;padding:1rem;background:var(--card);border-radius:12px;box-shadow:0 8px 24px #0005}@media(max-width:1000px){.grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:580px){.grid{grid-template-columns:1fr}header{align-items:flex-start;flex-direction:column}}@media(prefers-reduced-motion:reduce){*{animation:none!important}}</style></head><body><a class='skip' href='#main'>Skip to main content</a><main id='main'><header><div><strong>Platform Center 2.0</strong><h1>Operate AI as a governed product</h1><p>Catalogs, FinOps, reliability, compliance, quality, releases, and adoption.</p></div><div class='actions'><button aria-label='Toggle light and dark theme' onclick="let r=document.documentElement;r.dataset.theme=r.dataset.theme==='dark'?'light':'dark';toast('Theme changed')">Theme</button><button aria-label='Refresh platform dashboard' onclick="toast('Dashboard refreshed')">Refresh</button></div></header><section class='grid' aria-label='Platform capabilities'>"""
    + "".join(
        f"<article><h2>{name}</h2><p>{desc}</p></article>"
        for name, desc in [
            ("Prompt catalog", "Version prompts across environments."),
            ("Model catalog", "Track capabilities and data boundaries."),
            ("Usage tags", "Attribute every request."),
            ("Cost allocation", "Charge back spend fairly."),
            ("Quota planning", "Protect request and token capacity."),
            ("Alert rules", "Act on explicit thresholds."),
            ("SLOs", "Manage error budgets."),
            ("Incident digest", "Summarize operational impact."),
            ("Retention", "Enforce expiry and legal hold."),
            ("DLP", "Block sensitive data locally."),
            ("Region routing", "Keep traffic in allowed regions."),
            ("Provider scores", "Compare cost, quality, and reliability."),
            ("Canary rollout", "Ship in bounded stages."),
            ("Rollback guardrails", "Stop harmful releases."),
            ("Feedback", "Measure user sentiment."),
            ("Quality drift", "Catch degradation early."),
            ("Dataset curation", "Remove duplicate examples."),
            ("Export integrity", "Verify portable evidence."),
            ("Contract checks", "Prevent breaking API changes."),
            ("Adoption funnel", "Find onboarding drop-off."),
        ]
    )
    + """<article aria-busy='false'><div class='skeleton' aria-hidden='true'></div><p class='empty'>No platform alerts.</p><p class='error' hidden>Unable to load. Retry safely.</p></article></section></main><div class='toast' id='toast' role='status' aria-live='polite' hidden></div><script>function toast(x){let t=document.getElementById('toast');t.textContent=x;t.hidden=false;setTimeout(()=>t.hidden=true,1800)}</script></body></html>"""
)


def create_platform_app(api_key: str | None = None) -> FastAPI:
    """Create the fail-closed Platform Center application."""
    expected = (
        api_key if api_key is not None else os.getenv("GATEWAY_PLATFORM_API_KEY", "")
    )
    services: dict[str, Callable[[dict[str, Any]], dict[str, object]]] = {
        "prompt-catalog": lambda b: PromptCatalog().register(**b),
        "model-catalog": lambda b: ModelCatalog().register(**b),
        "usage-tags": lambda b: UsageTagger().normalize(b["tags"]),
        "cost-allocation": lambda b: CostAllocator().allocate(**b),
        "quota-plan": lambda b: QuotaPlanner().plan(**b),
        "alert-rule": lambda b: AlertRuleEvaluator().evaluate(**b),
        "slo": lambda b: SLOCalculator().calculate(**b),
        "incident-digest": lambda b: IncidentDigest().summarize(b["events"]),
        "retention": lambda b: RetentionPolicy().expiry(**b),
        "dlp": lambda b: DLPClassifier().classify(b["text"]),
        "region-route": lambda b: RegionRouter().choose(**b),
        "provider-score": lambda b: ProviderScorecard().score(**b),
        "canary-plan": lambda b: CanaryPlanner().plan(b["percentages"]),
        "rollback": lambda b: RollbackDecision().decide(**b),
        "feedback": lambda b: FeedbackAggregator().aggregate(b["ratings"]),
        "quality-drift": lambda b: QualityDriftDetector().detect(**b),
        "dataset-curate": lambda b: DatasetCurator().curate(b["examples"]),
        "export-manifest": lambda b: ExportManifest().build(
            {k: base64.b64decode(v, validate=True) for k, v in b["files"].items()}
        ),
        "contract": lambda b: ContractCompatibility().compare(**b),
        "adoption-funnel": lambda b: AdoptionFunnel().calculate(b["stages"]),
    }
    app = FastAPI(title="Gateway Platform API", version="2.0.0")

    @app.get("/platform", response_class=HTMLResponse)
    async def platform_ui() -> str:
        """Render the accessible responsive platform dashboard."""
        return PAGE

    @app.post("/v1/platform/{capability}")
    async def execute(
        capability: str,
        body: dict[str, Any],
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict[str, object]:
        """Execute one tenant-authenticated platform capability."""
        if not expected:
            raise HTTPException(503, "platform API key is not configured")
        if authorization != f"Bearer {expected}" or not x_tenant_id:
            raise HTTPException(401, "valid bearer key and X-Tenant-Id are required")
        service = services.get(capability)
        if service is None:
            raise HTTPException(404, "unknown platform capability")
        try:
            return service(body)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    return app
