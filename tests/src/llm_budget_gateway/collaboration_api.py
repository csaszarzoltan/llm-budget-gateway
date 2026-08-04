"""Authenticated Collaboration Center API and accessible dashboard."""

from __future__ import annotations

import os

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

from .collaboration_suite import (
    ApprovalDelegation,
    InvitationService,
    KeyLifecycle,
    MemberBudget,
    RolePolicy,
)

PAGE = """<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Collaboration Center</title><style>:root{color-scheme:light dark;--bg:#f4f7fb;--card:#fff;--ink:#172033;--brand:#3157d5;--focus:#ffbf47}@media(prefers-color-scheme:dark){:root{--bg:#0c1220;--card:#182238;--ink:#f5f7ff;--brand:#89a5ff}}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui}.skip{position:absolute;left:-999px}.skip:focus{left:1rem;top:1rem;background:var(--card);padding:1rem}main{max-width:1200px;margin:auto;padding:clamp(1rem,4vw,3rem)}header{display:flex;justify-content:space-between;gap:1rem}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem}article{background:var(--card);border-radius:16px;padding:1.25rem;box-shadow:0 8px 28px #0002}button{min-height:44px;border:0;border-radius:10px;padding:.7rem 1rem;background:var(--brand);color:#fff}button:focus-visible,a:focus-visible{outline:3px solid var(--focus);outline-offset:3px}.skeleton{height:.8rem;background:#8884;border-radius:6px;animation:pulse 1.2s infinite}@keyframes pulse{50%{opacity:.4}}.empty,.error{border-left:4px solid var(--brand);padding:.75rem}.toast{position:fixed;right:1rem;bottom:1rem;padding:1rem;background:var(--card);border-radius:12px}@media(max-width:850px){.grid{grid-template-columns:1fr 1fr}}@media(max-width:560px){.grid{grid-template-columns:1fr}header{flex-direction:column}}@media(prefers-reduced-motion:reduce){*{animation:none!important}}</style></head><body><a class='skip' href='#main'>Skip to main content</a><main id='main'><header><div><strong>Collaboration Center</strong><h1>Govern teams without slowing them down</h1></div><button aria-label='Refresh collaboration dashboard' onclick="document.getElementById('toast').hidden=false">Refresh</button></header><section class='grid'><article><h2>Project roles</h2><p>Least privilege for every team.</p></article><article><h2>Invitations</h2><p>One-time expiring onboarding.</p></article><article><h2>Key lifecycle</h2><p>Rotate and revoke on schedule.</p></article><article><h2>Member budgets</h2><p>Stop one script draining the team.</p></article><article><h2>Delegated approvals</h2><p>Keep governance moving during absence.</p></article><article aria-busy='false'><div class='skeleton'></div><p class='empty'>No pending collaboration risks.</p><p class='error' hidden>Unable to load.</p></article></section></main><div class='toast' id='toast' role='status' aria-live='polite' hidden>Collaboration dashboard refreshed.</div></body></html>"""  # noqa:E501


def create_collaboration_app(
    path: str = "collaboration.db", api_key: str | None = None
) -> FastAPI:
    """Create the fail-closed Collaboration Center application."""
    key = (
        api_key
        if api_key is not None
        else os.getenv("GATEWAY_COLLABORATION_API_KEY", "")
    )
    r, i, k, b, a = (
        RolePolicy(),
        InvitationService(path),
        KeyLifecycle(),
        MemberBudget(),
        ApprovalDelegation(),
    )
    app = FastAPI(title="Gateway Collaboration API", version="1.0.0")

    def auth(x: str | None, t: str | None) -> str:
        if not key:
            raise HTTPException(503, "collaboration API key is not configured")
        if x != f"Bearer {key}" or not t:
            raise HTTPException(401, "authentication required")
        return t

    @app.get("/collaboration", response_class=HTMLResponse)
    async def ui() -> str:
        """Render the responsive Collaboration Center."""
        return PAGE

    @app.post("/v1/collaboration/roles/authorize")
    async def roles(
        x: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Authorize a role and project scope."""
        auth(authorization, x_tenant_id)
        return r.authorize(**x)

    @app.post("/v1/collaboration/invitations")
    async def invite(
        x: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Issue a one-time tenant invitation."""
        return i.issue(auth(authorization, x_tenant_id), **x)

    @app.post("/v1/collaboration/invitations/accept")
    async def accept(x: dict) -> dict:
        """Accept a one-time invitation token."""
        return i.accept(x.get("token", ""))

    @app.post("/v1/collaboration/keys/lifecycle")
    async def lifecycle(
        x: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Evaluate rotation and revocation timing."""
        auth(authorization, x_tenant_id)
        return k.evaluate(**x)

    @app.post("/v1/collaboration/members/budget")
    async def budget(
        x: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Evaluate member request and key limits."""
        auth(authorization, x_tenant_id)
        return b.evaluate(**x)

    @app.post("/v1/collaboration/approvals/delegate")
    async def delegate(
        x: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Evaluate time-limited delegated approval."""
        auth(authorization, x_tenant_id)
        return a.decide(**x)

    return app
