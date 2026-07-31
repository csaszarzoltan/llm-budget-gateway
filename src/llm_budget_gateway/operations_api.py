"""FastAPI surface and responsive UI for production operations features."""

from __future__ import annotations

import os
from collections.abc import Mapping

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

from .operations_suite import (
    ModelCatalog,
    PromptRegistry,
    QuotaDiagnostic,
    RetryPolicy,
    SLOMonitor,
)

PAGE = """<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Gateway Operations</title><style>:root{color-scheme:light dark;--bg:#f4f7fb;--card:#fff;--ink:#172033;--muted:#58657a;--brand:#3157d5;--focus:#ffbf47;--s:1rem}@media(prefers-color-scheme:dark){:root{--bg:#0c1220;--card:#182238;--ink:#f5f7ff;--muted:#bdc7dc;--brand:#89a5ff}}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui}.skip{position:absolute;left:-999px}.skip:focus{left:1rem;top:1rem;background:var(--card);padding:1rem;z-index:2}main{max-width:1200px;margin:auto;padding:clamp(1rem,4vw,3rem)}header{display:flex;align-items:center;justify-content:space-between;gap:1rem}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:var(--s)}article{background:var(--card);border:1px solid color-mix(in srgb,var(--muted) 30%,transparent);border-radius:16px;padding:1.25rem;box-shadow:0 8px 28px #0002}button{min-height:44px;border:0;border-radius:10px;padding:.7rem 1rem;background:var(--brand);color:#fff}button:focus-visible,a:focus-visible{outline:3px solid var(--focus);outline-offset:3px}.skeleton{height:.8rem;border-radius:6px;background:color-mix(in srgb,var(--muted) 25%,transparent);animation:pulse 1.2s infinite}@keyframes pulse{50%{opacity:.4}}.empty,.error{border-left:4px solid var(--brand);padding:.75rem}.toast{position:fixed;right:1rem;bottom:1rem;padding:1rem;background:var(--card);border-radius:12px;box-shadow:0 8px 24px #0005}@media(max-width:850px){.grid{grid-template-columns:1fr 1fr}}@media(max-width:560px){.grid{grid-template-columns:1fr}header{align-items:flex-start;flex-direction:column}}@media(prefers-reduced-motion:reduce){*{animation:none!important}}</style></head><body><a class='skip' href='#main'>Skip to main content</a><main id='main'><header><div><strong>Gateway Operations</strong><h1>Reliable production controls</h1><p>Prompts, retries, quotas, model metadata and SLOs.</p></div><button aria-label='Refresh operations' onclick="document.getElementById('toast').hidden=false">Refresh</button></header><section class='grid' aria-label='Operations features'><article><h2>Prompt registry</h2><p>Immutable versions and deterministic experiments.</p></article><article><h2>Retry safety</h2><p>Bounded jitter prevents retry amplification.</p></article><article><h2>Quota diagnostics</h2><p>Distinguish billing, tokens, requests and availability.</p></article><article><h2>Model catalog</h2><p>Pricing, context, capabilities and regions.</p></article><article><h2>SLO monitor</h2><p>Availability and error-budget burn.</p></article><article aria-busy='false'><h2>Status</h2><div class='skeleton' aria-hidden='true'></div><p class='empty'>No active incidents.</p><p class='error' hidden>Unable to load. Retry safely.</p></article></section></main><div class='toast' id='toast' role='status' aria-live='polite' hidden>Operations refreshed.</div></body></html>"""  # noqa: E501


def create_operations_app(
    path: str = "operations.db", api_key: str | None = None
) -> FastAPI:
    """Create the authenticated operations API and dashboard."""
    expected_key = (
        api_key if api_key is not None else os.getenv("GATEWAY_OPERATIONS_API_KEY", "")
    )
    prompts = PromptRegistry(path)
    retry, quota, catalog, slo = (
        RetryPolicy(),
        QuotaDiagnostic(),
        ModelCatalog(),
        SLOMonitor(),
    )
    app = FastAPI(title="Gateway Operations API", version="1.0.0")

    def auth(authorization: str | None, tenant: str | None) -> str:
        if not expected_key:
            raise HTTPException(503, "operations API key is not configured")
        if authorization != f"Bearer {expected_key}" or not tenant:
            raise HTTPException(401, "valid bearer key and X-Tenant-Id are required")
        return tenant

    @app.get("/operations", response_class=HTMLResponse)
    async def operations() -> str:
        """Render the accessible operations dashboard."""
        return PAGE

    @app.post("/v1/operations/prompts")
    async def create_prompt(
        body: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Create an immutable prompt version."""
        return prompts.create(
            auth(authorization, x_tenant_id),
            body.get("name", ""),
            body.get("template", ""),
            body.get("metadata"),
        )

    @app.get("/v1/operations/prompts/{name}")
    async def list_prompts(
        name: str,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """List prompt versions for one tenant."""
        return {"items": prompts.list(auth(authorization, x_tenant_id), name)}

    @app.post("/v1/operations/prompts/{name}/assign")
    async def assign_prompt(
        name: str,
        body: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Assign a subject deterministically to an experiment version."""
        return prompts.assign(
            auth(authorization, x_tenant_id),
            name,
            body.get("subject", ""),
            body.get("versions", []),
        )

    @app.post("/v1/operations/retry-decisions")
    async def retry_decision(
        body: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Calculate a bounded retry decision."""
        auth(authorization, x_tenant_id)
        decision = retry.decide(
            int(body.get("attempt", 0)),
            int(body.get("elapsed_ms", 0)),
            body.get("status_code"),
            body.get("retry_after_ms"),
            int(body.get("seed", 0)),
        )
        return {
            "retry": decision.retry,
            "delay_ms": decision.delay_ms,
            "reason": decision.reason,
        }

    @app.post("/v1/operations/quota-diagnostics")
    async def quota_diagnostic(
        body: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Classify a provider failure into an actionable category."""
        auth(authorization, x_tenant_id)
        return quota.classify(
            body.get("status_code"), body.get("code"), body.get("message")
        )

    @app.post("/v1/operations/model-catalog/normalize")
    async def normalize_models(
        body: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Validate and normalize model pricing and capability metadata."""
        auth(authorization, x_tenant_id)
        return {"data": catalog.normalize(body.get("models", []))}

    @app.post("/v1/operations/slo")
    async def evaluate_slo(
        body: Mapping[str, object],
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Evaluate availability and error-budget burn."""
        auth(authorization, x_tenant_id)
        return slo.evaluate(
            body.get("total"), body.get("failed"), float(body.get("target", 0.99))
        )  # type: ignore[arg-type]

    return app
