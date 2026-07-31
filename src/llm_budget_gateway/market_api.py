"""FastAPI application exposing the market-driven control suite."""

from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

from .market_features import (
    CostAwareRouter,
    ExactResponseCache,
    PIIRedactor,
    SignedWebhook,
    UsageAnomalyDetector,
)

DASHBOARD = """<!doctype html><html lang='en' data-theme='auto'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Gateway Intelligence</title><style>:root{color-scheme:light dark;--bg:#f5f7fb;--surface:#fff;--text:#172033;--muted:#58657a;--accent:#3157d5;--focus:#ffbf47;--space:1rem}@media(prefers-color-scheme:dark){:root{--bg:#0d1321;--surface:#182238;--text:#f4f7ff;--muted:#bac5da;--accent:#89a5ff}}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 system-ui}.skip{position:absolute;left:-999px}.skip:focus{left:var(--space);top:var(--space);background:var(--surface);padding:var(--space)}main{max-width:1200px;margin:auto;padding:clamp(1rem,4vw,3rem)}header{display:flex;justify-content:space-between;gap:1rem;align-items:center}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:var(--space)}article{background:var(--surface);border:1px solid color-mix(in srgb,var(--muted) 35%,transparent);border-radius:16px;padding:1.25rem;box-shadow:0 8px 30px #0002}.badge{color:var(--accent);font-weight:700}.skeleton{height:.8rem;background:color-mix(in srgb,var(--muted) 20%,transparent);border-radius:5px;animation:pulse 1.2s infinite}@keyframes pulse{50%{opacity:.45}}button{min-height:44px;padding:.65rem 1rem;border:0;border-radius:10px;background:var(--accent);color:white}button:focus-visible,a:focus-visible{outline:3px solid var(--focus);outline-offset:3px}.toast{position:fixed;right:1rem;bottom:1rem;background:var(--surface);padding:1rem;border-radius:10px;box-shadow:0 8px 30px #0004}.empty,.error{padding:1rem;border-left:4px solid var(--accent)}@media(max-width:850px){.grid{grid-template-columns:1fr 1fr}}@media(max-width:560px){.grid{grid-template-columns:1fr}header{align-items:flex-start;flex-direction:column}}@media(prefers-reduced-motion:reduce){*{animation:none!important}}</style></head><body><a class='skip' href='#main'>Skip to main content</a><main id='main'><header><div><span class='badge'>Gateway Intelligence</span><h1>Cost, privacy and reliability controls</h1><p>Five production controls in one tenant-safe workspace.</p></div><button type='button' aria-label='Refresh dashboard' onclick="document.getElementById('toast').hidden=false">Refresh</button></header><section class='grid' aria-label='Feature overview'><article><h2>PII guard</h2><p>Redact email, phone and card data before dispatch.</p></article><article><h2>Response cache</h2><p>Deduplicate exact requests with tenant isolation and TTL.</p></article><article><h2>Signed alerts</h2><p>Verify HMAC event delivery before acting.</p></article><article><h2>Anomaly detection</h2><p>Explain cost spikes using baseline statistics.</p></article><article><h2>Cost-aware routing</h2><p>Select the cheapest healthy model that meets policy.</p></article><article aria-busy='false'><h2>System state</h2><div class='skeleton' aria-hidden='true'></div><p class='empty'>No unresolved incidents.</p><p class='error' hidden>Data could not be loaded. Retry safely.</p></article></section></main><div id='toast' class='toast' role='status' aria-live='polite' hidden>Dashboard refreshed.</div></body></html>"""  # noqa: E501


def create_market_app(
    path: str = "market-features.db", webhook_secret: str = "development-only"
) -> FastAPI:
    """Create the tenant-aware market feature API and dashboard."""
    app = FastAPI(title="Gateway Intelligence API", version="1.0.0")
    redactor, cache = PIIRedactor(), ExactResponseCache(path)
    detector, router = UsageAnomalyDetector(), CostAwareRouter()

    def tenant(value: str | None) -> str:
        if not value:
            raise HTTPException(401, "X-Tenant-Id is required")
        return value

    @app.get("/intelligence", response_class=HTMLResponse)
    async def dashboard() -> str:
        """Render the accessible responsive dashboard."""
        return DASHBOARD

    @app.post("/v1/intelligence/redact")
    async def redact(body: dict, x_tenant_id: str | None = Header(None)) -> dict:
        """Redact PII from text without persisting the original value."""
        tenant(x_tenant_id)
        result = redactor.redact(body.get("text", ""))
        return {
            "text": result.text,
            "categories": result.categories,
            "count": result.count,
        }

    @app.post("/v1/intelligence/cache")
    async def cache_put(body: dict, x_tenant_id: str | None = Header(None)) -> dict:
        """Store a tenant-isolated exact response cache entry."""
        key = cache.put(
            tenant(x_tenant_id),
            body.get("request", {}),
            body.get("response"),
            int(body.get("ttl", 0)),
        )
        return {"key": key, "status": "stored"}

    @app.post("/v1/intelligence/cache/lookup")
    async def cache_get(body: dict, x_tenant_id: str | None = Header(None)) -> dict:
        """Look up an exact response cache entry."""
        value = cache.get(tenant(x_tenant_id), body.get("request", {}))
        return {"hit": value is not None, "value": value}

    @app.post("/v1/intelligence/webhooks/sign")
    async def sign(body: dict, x_tenant_id: str | None = Header(None)) -> dict:
        """Create a signed alert event for an authenticated tenant."""
        tenant(x_tenant_id)
        return SignedWebhook.build(
            webhook_secret,
            body.get("event", ""),
            body.get("payload", {}),
            int(body.get("timestamp", 0)),
        )

    @app.post("/v1/intelligence/anomalies")
    async def anomaly(body: dict, x_tenant_id: str | None = Header(None)) -> dict:
        """Evaluate an explainable cost anomaly."""
        tenant(x_tenant_id)
        return detector.detect(
            body.get("history", []),
            float(body.get("current", -1)),
            float(body.get("z_limit", 3)),
        )

    @app.post("/v1/intelligence/route")
    async def route(body: dict, x_tenant_id: str | None = Header(None)) -> dict:
        """Select an eligible cost-efficient model."""
        tenant(x_tenant_id)
        return router.choose(
            body.get("candidates", []),
            float(body.get("min_quality", 0)),
            body.get("max_latency_ms"),
        )

    return app
