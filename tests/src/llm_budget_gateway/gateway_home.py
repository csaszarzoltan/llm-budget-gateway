"""Responsive root homepage for the core LLM Budget Gateway service."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>LLM Budget Gateway</title><style>
:root{color-scheme:light dark;--bg:#f5f7fb;--card:#fff;--ink:#172033;--muted:#647089;--line:#dce2ec;--brand:#2f5bea;--good:#16845b}
@media(prefers-color-scheme:dark){:root{--bg:#0b1020;--card:#121a2c;--ink:#f4f7ff;--muted:#a7b1c7;--line:#2a3650;--brand:#7d9cff;--good:#54d49b}}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui;padding:24px}main{width:min(1050px,100%);margin:auto;padding:clamp(24px,5vw,52px)}.hero,.card{background:var(--card);border:1px solid var(--line);border-radius:22px;box-shadow:0 18px 46px #0002}.hero{padding:clamp(24px,5vw,52px)}.mark{display:grid;place-items:center;width:54px;height:54px;border-radius:16px;background:linear-gradient(145deg,var(--brand),#823cf2);color:#fff;font-weight:800}.eyebrow{margin:22px 0 5px;color:var(--brand);font-size:12px;font-weight:800;letter-spacing:.12em;text-transform:uppercase}h1{margin:.1em 0;font-size:clamp(38px,7vw,68px);line-height:1.02;letter-spacing:-.04em}p{color:var(--muted)}.status{display:inline-flex;align-items:center;gap:8px;padding:7px 11px;border-radius:999px;background:color-mix(in srgb,var(--good) 13%,transparent)}.status i{width:9px;height:9px;border-radius:50%;background:var(--good)}nav{display:flex;flex-wrap:wrap;gap:10px;margin-top:27px}a{padding:11px 15px;border:1px solid var(--line);border-radius:11px;color:var(--ink);text-decoration:none}a:hover{border-color:var(--brand);color:var(--brand)}a.primary{background:var(--brand);border-color:var(--brand);color:#fff}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:18px}.card{padding:20px}.card h2{margin:0;font-size:17px}.card code{font-size:12px;color:var(--brand)}footer{margin-top:24px;color:var(--muted);font-size:13px}:focus-visible{outline:3px solid var(--brand);outline-offset:3px}@media(max-width:760px){.grid{grid-template-columns:1fr}}
</style></head><body><main><section class="hero"><div class="mark">LG</div><p class="eyebrow">Core data plane</p><h1>LLM Budget Gateway</h1><p>OpenAI-compatible proxy with virtual keys, hierarchical budgets, cost tracking, provider timeouts and automatic model fallback.</p><p class="status"><i></i>Gateway service is running on port 8000</p><nav aria-label="Gateway actions"><a class="primary" href="/docs">Open API documentation</a><a href="/v1/models">List models</a><a href="/health">Health check</a><a href="/openapi.json">OpenAPI JSON</a><a href="http://127.0.0.1:8013/" rel="noopener noreferrer">Unified Console</a></nav></section><section class="grid" aria-label="Core endpoints"><article class="card"><h2>Chat completions</h2><p>OpenAI-compatible chat requests and streaming.</p><code>POST /v1/chat/completions</code></article><article class="card"><h2>Embeddings</h2><p>Generate embeddings through configured providers.</p><code>POST /v1/embeddings</code></article><article class="card"><h2>Models</h2><p>Inspect the configured model catalog.</p><code>GET /v1/models</code></article></section><footer>LLM Budget Gateway 7.3 · Existing API routes remain unchanged.</footer></main></body></html>"""


def install_gateway_home(app: FastAPI) -> FastAPI:
    """Add the gateway landing page only when the app has no root route."""
    if any(getattr(route, "path", None) == "/" for route in app.routes):
        return app

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def gateway_homepage() -> str:
        return PAGE

    return app
