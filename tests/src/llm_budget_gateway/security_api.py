"""Authenticated Security Center API and responsive dashboard."""

from __future__ import annotations

import os

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

from .security_suite import (
    ChangeRiskAssessor,
    ProviderCompliancePolicy,
    ReplayProtector,
    SecretScanner,
    SecurityPosture,
)

PAGE = """<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Security Center</title><style>:root{color-scheme:light dark;--bg:#f4f7fb;--card:#fff;--ink:#172033;--brand:#3157d5;--focus:#ffbf47}@media(prefers-color-scheme:dark){:root{--bg:#0c1220;--card:#182238;--ink:#f5f7ff;--brand:#89a5ff}}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui}.skip{position:absolute;left:-999px}.skip:focus{left:1rem;top:1rem;background:var(--card);padding:1rem}main{max-width:1200px;margin:auto;padding:clamp(1rem,4vw,3rem)}header{display:flex;justify-content:space-between;align-items:center;gap:1rem}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem}article{background:var(--card);border-radius:16px;padding:1.25rem;box-shadow:0 8px 28px #0002}button{min-height:44px;border:0;border-radius:10px;padding:.7rem 1rem;background:var(--brand);color:#fff}button:focus-visible,a:focus-visible{outline:3px solid var(--focus);outline-offset:3px}.skeleton{height:.8rem;background:#8884;border-radius:6px;animation:pulse 1.2s infinite}@keyframes pulse{50%{opacity:.4}}.empty,.error{border-left:4px solid var(--brand);padding:.75rem}.toast{position:fixed;right:1rem;bottom:1rem;padding:1rem;background:var(--card);border-radius:12px}@media(max-width:850px){.grid{grid-template-columns:1fr 1fr}}@media(max-width:560px){.grid{grid-template-columns:1fr}header{flex-direction:column;align-items:flex-start}}@media(prefers-reduced-motion:reduce){*{animation:none!important}}</style></head><body><a class='skip' href='#main'>Skip to main content</a><main id='main'><header><div><strong>Security Center</strong><h1>Protect the gateway</h1><p>Secrets, replay, provider compliance, change risk and posture.</p></div><button aria-label='Refresh security dashboard' onclick="document.getElementById('toast').hidden=false">Refresh</button></header><section class='grid' aria-label='Security features'><article><h2>Secret scanner</h2><p>Redact credentials before provider dispatch.</p></article><article><h2>Replay protection</h2><p>Deduplicate inbound webhook deliveries.</p></article><article><h2>Provider compliance</h2><p>Fail closed on missing certifications or data promises.</p></article><article><h2>Change risk</h2><p>Require approvals for sensitive production changes.</p></article><article><h2>Posture score</h2><p>Prioritize missing gateway controls.</p></article><article aria-busy='false'><h2>Status</h2><div class='skeleton'></div><p class='empty'>No critical gaps.</p><p class='error' hidden>Unable to load. Retry safely.</p></article></section></main><div class='toast' id='toast' role='status' aria-live='polite' hidden>Security dashboard refreshed.</div></body></html>"""  # noqa:E501


def create_security_app(
    path: str = "security.db", api_key: str | None = None
) -> FastAPI:
    """Create the fail-closed Security Center application."""
    expected = (
        api_key if api_key is not None else os.getenv("GATEWAY_SECURITY_API_KEY", "")
    )
    scanner, replay, policy, risk, posture = (
        SecretScanner(),
        ReplayProtector(path),
        ProviderCompliancePolicy(),
        ChangeRiskAssessor(),
        SecurityPosture(),
    )
    app = FastAPI(title="Gateway Security API", version="1.0.0")

    def auth(a: str | None, t: str | None) -> str:
        if not expected:
            raise HTTPException(503, "security API key is not configured")
        if a != f"Bearer {expected}" or not t:
            raise HTTPException(401, "valid bearer key and X-Tenant-Id are required")
        return t

    @app.get("/security", response_class=HTMLResponse)
    async def dashboard() -> str:
        """Render the accessible Security Center dashboard."""
        return PAGE

    @app.post("/v1/security/secrets/scan")
    async def scan(
        body: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Redact supported secret patterns locally."""
        auth(authorization, x_tenant_id)
        r = scanner.scan(body.get("text", ""))
        return {"text": r.text, "categories": r.categories, "count": r.count}

    @app.post("/v1/security/replays/reserve")
    async def reserve(
        body: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Atomically reserve an inbound delivery identifier."""
        return replay.reserve(
            auth(authorization, x_tenant_id),
            body.get("event_id", ""),
            int(body.get("ttl", 0)),
        )

    @app.post("/v1/security/providers/evaluate")
    async def provider(
        body: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Evaluate a provider against tenant compliance requirements."""
        auth(authorization, x_tenant_id)
        return policy.evaluate(body.get("provider", {}), body.get("requirements", {}))

    @app.post("/v1/security/changes/assess")
    async def changes(
        body: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Assess a gateway change and required approvals."""
        auth(authorization, x_tenant_id)
        return risk.assess(body.get("changes", []), bool(body.get("production", True)))

    @app.post("/v1/security/posture")
    async def security_posture(
        body: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Score the configured gateway security controls."""
        auth(authorization, x_tenant_id)
        return posture.evaluate(body)

    return app
