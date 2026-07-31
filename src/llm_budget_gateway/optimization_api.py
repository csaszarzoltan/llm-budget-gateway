"""Authenticated Optimization Center API and responsive dashboard."""

from __future__ import annotations

import os

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

from .optimization_suite import (
    BudgetForecast,
    CachePolicyAdvisor,
    OptimizationExperimentStore,
    PromptCompressor,
    SavingsAttributor,
)

PAGE = """<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Optimization Center</title><style>:root{color-scheme:light dark;--bg:#f4f7fb;--card:#fff;--ink:#172033;--brand:#3157d5;--focus:#ffbf47}@media(prefers-color-scheme:dark){:root{--bg:#0c1220;--card:#182238;--ink:#f5f7ff;--brand:#89a5ff}}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui}.skip{position:absolute;left:-999px}.skip:focus{left:1rem;top:1rem;background:var(--card);padding:1rem}main{max-width:1200px;margin:auto;padding:clamp(1rem,4vw,3rem)}header{display:flex;justify-content:space-between;gap:1rem}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem}article{background:var(--card);border-radius:16px;padding:1.25rem;box-shadow:0 8px 28px #0002}button{min-height:44px;border:0;border-radius:10px;padding:.7rem 1rem;background:var(--brand);color:#fff}button:focus-visible,a:focus-visible{outline:3px solid var(--focus);outline-offset:3px}.skeleton{height:.8rem;background:#8884;border-radius:6px;animation:pulse 1.2s infinite}@keyframes pulse{50%{opacity:.4}}.empty,.error{border-left:4px solid var(--brand);padding:.75rem}.toast{position:fixed;right:1rem;bottom:1rem;padding:1rem;background:var(--card);border-radius:12px}@media(max-width:850px){.grid{grid-template-columns:1fr 1fr}}@media(max-width:560px){.grid{grid-template-columns:1fr}header{flex-direction:column}}@media(prefers-reduced-motion:reduce){*{animation:none!important}}</style></head><body><a class='skip' href='#main'>Skip to main content</a><main id='main'><header><div><strong>Optimization Center</strong><h1>Turn AI efficiency into margin</h1></div><button aria-label='Refresh optimization dashboard' onclick="document.getElementById('toast').hidden=false">Refresh</button></header><section class='grid'><article><h2>Prompt compression</h2><p>Remove deterministic waste.</p></article><article><h2>Savings attribution</h2><p>Prove which control saved money.</p></article><article><h2>Cache policy</h2><p>Balance reuse, volatility and privacy.</p></article><article><h2>Budget forecast</h2><p>Predict period-end spend.</p></article><article><h2>Experiments</h2><p>Select the cheapest quality-safe variant.</p></article><article aria-busy='false'><div class='skeleton'></div><p class='empty'>No optimization alerts.</p><p class='error' hidden>Unable to load.</p></article></section></main><div class='toast' id='toast' role='status' aria-live='polite' hidden>Optimization dashboard refreshed.</div></body></html>"""  # noqa:E501


def create_optimization_app(
    path: str = "optimization.db", api_key: str | None = None
) -> FastAPI:
    """Create the fail-closed Optimization Center application."""
    key = (
        api_key
        if api_key is not None
        else os.getenv("GATEWAY_OPTIMIZATION_API_KEY", "")
    )
    p, s, c, b, e = (
        PromptCompressor(),
        SavingsAttributor(),
        CachePolicyAdvisor(),
        BudgetForecast(),
        OptimizationExperimentStore(path),
    )
    app = FastAPI(title="Gateway Optimization API", version="1.0.0")

    def auth(a: str | None, t: str | None) -> str:
        if not key:
            raise HTTPException(503, "optimization API key is not configured")
        if a != f"Bearer {key}" or not t:
            raise HTTPException(401, "authentication required")
        return t

    @app.get("/optimization", response_class=HTMLResponse)
    async def ui() -> str:
        """Render the accessible optimization dashboard."""
        return PAGE

    @app.post("/v1/optimization/prompts/compress")
    async def compress(
        x: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Compress prompt text deterministically."""
        auth(authorization, x_tenant_id)
        return p.compress(x.get("text", ""))

    @app.post("/v1/optimization/savings/attribute")
    async def savings(
        x: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Attribute realized savings to optimization drivers."""
        auth(authorization, x_tenant_id)
        return s.calculate(**x)

    @app.post("/v1/optimization/cache/recommend")
    async def cache(
        x: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Recommend a privacy-aware exact-cache policy."""
        auth(authorization, x_tenant_id)
        return c.recommend(**x)

    @app.post("/v1/optimization/budget/forecast")
    async def forecast(
        x: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Forecast period-end spend and risk."""
        auth(authorization, x_tenant_id)
        return b.forecast(**x)

    @app.post("/v1/optimization/experiments")
    async def record(
        x: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Record a tenant optimization observation."""
        return e.record(auth(authorization, x_tenant_id), **x)

    @app.post("/v1/optimization/experiments/{name}/winner")
    async def winner(
        name: str,
        x: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Select the cheapest eligible experiment variant."""
        return e.winner(
            auth(authorization, x_tenant_id), name, float(x.get("minimum_quality", 0))
        )

    return app
