"""Authenticated resilience API and responsive dashboard."""

from __future__ import annotations

import os

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

from .resilience_suite import (
    AdaptiveConcurrency,
    ConfigDoctor,
    DeadLetterStore,
    IncidentTimeline,
    MaintenanceWindow,
)

PAGE = """<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Resilience Center</title><style>:root{color-scheme:light dark;--bg:#f4f7fb;--card:#fff;--ink:#172033;--brand:#3157d5;--focus:#ffbf47}@media(prefers-color-scheme:dark){:root{--bg:#0c1220;--card:#182238;--ink:#f5f7ff;--brand:#89a5ff}}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui}.skip{position:absolute;left:-999px}.skip:focus{left:1rem;top:1rem;background:var(--card);padding:1rem}main{max-width:1200px;margin:auto;padding:clamp(1rem,4vw,3rem)}header{display:flex;justify-content:space-between;gap:1rem}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem}article{background:var(--card);border-radius:16px;padding:1.25rem;box-shadow:0 8px 28px #0002}button{min-height:44px;border:0;border-radius:10px;padding:.7rem 1rem;background:var(--brand);color:#fff}button:focus-visible,a:focus-visible{outline:3px solid var(--focus);outline-offset:3px}.skeleton{height:.8rem;background:#8884;border-radius:6px;animation:pulse 1.2s infinite}@keyframes pulse{50%{opacity:.4}}.empty,.error{border-left:4px solid var(--brand);padding:.75rem}.toast{position:fixed;right:1rem;bottom:1rem;padding:1rem;background:var(--card);border-radius:12px}@media(max-width:850px){.grid{grid-template-columns:1fr 1fr}}@media(max-width:560px){.grid{grid-template-columns:1fr}header{flex-direction:column}}@media(prefers-reduced-motion:reduce){*{animation:none!important}}</style></head><body><a class='skip' href='#main'>Skip to main content</a><main id='main'><header><div><strong>Resilience Center</strong><h1>Keep production recoverable</h1></div><button aria-label='Refresh resilience dashboard' onclick="document.getElementById('toast').hidden=false">Refresh</button></header><section class='grid'><article><h2>Adaptive concurrency</h2><p>Protect latency and providers.</p></article><article><h2>Dead letters</h2><p>Replay failed work once.</p></article><article><h2>Maintenance</h2><p>Coordinate safe changes.</p></article><article><h2>Config doctor</h2><p>Catch unsafe deployment settings.</p></article><article><h2>Incident timeline</h2><p>Build an explainable post-mortem.</p></article><article aria-busy='false'><div class='skeleton'></div><p class='empty'>No incidents.</p><p class='error' hidden>Unable to load.</p></article></section></main><div class='toast' id='toast' role='status' aria-live='polite' hidden>Refreshed.</div></body></html>"""  # noqa:E501


def create_resilience_app(
    path: str = "resilience.db", api_key: str | None = None
) -> FastAPI:
    """Create the fail-closed resilience application."""
    key = (
        api_key if api_key is not None else os.getenv("GATEWAY_RESILIENCE_API_KEY", "")
    )
    a, d, m, c, i = (
        AdaptiveConcurrency(),
        DeadLetterStore(path),
        MaintenanceWindow(),
        ConfigDoctor(),
        IncidentTimeline(),
    )
    app = FastAPI(title="Gateway Resilience API", version="1.0.0")

    def auth(x: str | None, t: str | None) -> str:
        if not key:
            raise HTTPException(503, "resilience API key is not configured")
        if x != f"Bearer {key}" or not t:
            raise HTTPException(401, "authentication required")
        return t

    @app.get("/resilience", response_class=HTMLResponse)
    async def ui() -> str:
        """Render the resilient responsive dashboard."""
        return PAGE

    @app.post("/v1/resilience/concurrency")
    async def concurrency(
        b: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Calculate an adaptive concurrency limit."""
        auth(authorization, x_tenant_id)
        return a.tune(**b)

    @app.post("/v1/resilience/dead-letters")
    async def add(
        b: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Store a failed operation safely."""
        return d.add(
            auth(authorization, x_tenant_id), b.get("payload", {}), b.get("error", "")
        )

    @app.post("/v1/resilience/dead-letters/{rid}/replay")
    async def replay(
        rid: str,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Replay one tenant dead-letter idempotently."""
        return d.replay(auth(authorization, x_tenant_id), rid)

    @app.post("/v1/resilience/maintenance")
    async def maintenance(
        b: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Evaluate a UTC maintenance window."""
        auth(authorization, x_tenant_id)
        return m.evaluate(**b)

    @app.post("/v1/resilience/config/diagnose")
    async def diagnose(
        b: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Diagnose deployment configuration."""
        auth(authorization, x_tenant_id)
        return c.diagnose(b)

    @app.post("/v1/resilience/incidents/timeline")
    async def timeline(
        b: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Build a normalized incident timeline."""
        auth(authorization, x_tenant_id)
        return i.build(b.get("events", []))

    return app
