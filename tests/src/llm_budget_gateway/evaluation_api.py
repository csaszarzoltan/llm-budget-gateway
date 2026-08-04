"""Authenticated FastAPI surface and accessible UI for evaluation workflows."""

from __future__ import annotations

import os
from collections.abc import Mapping

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

from .evaluation_suite import (
    AuditReport,
    BatchManifest,
    EvaluationStore,
    ReleaseGate,
    RuleEvaluator,
    TraceContext,
)

PAGE = """<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Gateway Quality</title><style>:root{color-scheme:light dark;--bg:#f4f7fb;--card:#fff;--ink:#172033;--muted:#59667c;--brand:#3157d5;--focus:#ffbf47;--space:1rem}@media(prefers-color-scheme:dark){:root{--bg:#0c1220;--card:#182238;--ink:#f5f7ff;--muted:#bdc7dc;--brand:#89a5ff}}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui}.skip{position:absolute;left:-999px}.skip:focus{left:1rem;top:1rem;background:var(--card);padding:1rem;z-index:2}main{max-width:1200px;margin:auto;padding:clamp(1rem,4vw,3rem)}header{display:flex;justify-content:space-between;align-items:center;gap:1rem}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:var(--space)}article{background:var(--card);border:1px solid color-mix(in srgb,var(--muted) 30%,transparent);border-radius:16px;padding:1.25rem;box-shadow:0 8px 28px #0002}button{min-height:44px;border:0;border-radius:10px;padding:.7rem 1rem;background:var(--brand);color:#fff}button:focus-visible,a:focus-visible{outline:3px solid var(--focus);outline-offset:3px}.skeleton{height:.8rem;border-radius:6px;background:color-mix(in srgb,var(--muted) 25%,transparent);animation:pulse 1.2s infinite}@keyframes pulse{50%{opacity:.4}}.empty,.error{border-left:4px solid var(--brand);padding:.75rem}.toast{position:fixed;right:1rem;bottom:1rem;padding:1rem;background:var(--card);border-radius:12px;box-shadow:0 8px 24px #0005}@media(max-width:850px){.grid{grid-template-columns:1fr 1fr}}@media(max-width:560px){.grid{grid-template-columns:1fr}header{align-items:flex-start;flex-direction:column}}@media(prefers-reduced-motion:reduce){*{animation:none!important}}</style></head><body><a class='skip' href='#main'>Skip to main content</a><main id='main'><header><div><strong>Gateway Quality</strong><h1>Evaluate before production</h1><p>Rules, release gates, traces, batches and audit evidence.</p></div><button aria-label='Refresh quality dashboard' onclick="document.getElementById('toast').hidden=false">Refresh</button></header><section class='grid' aria-label='Quality features'><article><h2>Evaluation runs</h2><p>Deterministic offline checks with immutable history.</p></article><article><h2>Release gates</h2><p>Block regressions before rollout.</p></article><article><h2>Trace context</h2><p>Correlate sessions without prompt retention.</p></article><article><h2>Batch planning</h2><p>Validate manifests and estimate discounted cost.</p></article><article><h2>Audit reports</h2><p>Redacted, versioned, integrity-protected evidence.</p></article><article aria-busy='false'><h2>Status</h2><div class='skeleton' aria-hidden='true'></div><p class='empty'>No blocked releases.</p><p class='error' hidden>Unable to load quality data. Retry safely.</p></article></section></main><div class='toast' id='toast' role='status' aria-live='polite' hidden>Quality dashboard refreshed.</div></body></html>"""  # noqa: E501


def create_evaluation_app(
    path: str = "evaluations.db", api_key: str | None = None
) -> FastAPI:
    """Create the authenticated evaluation API and responsive dashboard."""
    expected = (
        api_key if api_key is not None else os.getenv("GATEWAY_EVALUATION_API_KEY", "")
    )
    evaluator, gate = RuleEvaluator(), ReleaseGate()
    traces, batches, audits = TraceContext(), BatchManifest(), AuditReport()
    store = EvaluationStore(path)
    app = FastAPI(title="Gateway Quality API", version="1.0.0")

    def auth(authorization: str | None, tenant: str | None) -> str:
        if not expected:
            raise HTTPException(503, "evaluation API key is not configured")
        if authorization != f"Bearer {expected}" or not tenant:
            raise HTTPException(401, "valid bearer key and X-Tenant-Id are required")
        return tenant

    @app.get("/quality", response_class=HTMLResponse)
    async def quality() -> str:
        """Render the accessible quality dashboard."""
        return PAGE

    @app.post("/v1/quality/evaluations")
    async def evaluate(
        body: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Evaluate output using deterministic rules and persist the run."""
        tenant = auth(authorization, x_tenant_id)
        result = evaluator.evaluate(body.get("output", ""), body.get("rules", {}))
        return {
            **store.record(tenant, body.get("name", ""), result),
            "checks": result.checks,
        }

    @app.get("/v1/quality/evaluations")
    async def evaluations(
        authorization: str | None = Header(None), x_tenant_id: str | None = Header(None)
    ) -> dict:
        """List tenant-scoped evaluation runs."""
        return {"items": store.list(auth(authorization, x_tenant_id))}

    @app.post("/v1/quality/release-gates")
    async def release_gate(
        body: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Evaluate release thresholds and regression limits."""
        auth(authorization, x_tenant_id)
        return gate.decide(
            body.get("scores", []),
            float(body.get("minimum", 0)),
            float(body.get("max_regression", 0)),
            body.get("baseline"),
        )

    @app.post("/v1/quality/traces/resolve")
    async def resolve_trace(
        body: Mapping[str, object],
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Resolve a trace context from supplied request headers."""
        auth(authorization, x_tenant_id)
        headers = body.get("headers", {})
        if not isinstance(headers, Mapping):
            raise HTTPException(400, "headers must be an object")
        return traces.resolve({str(k): str(v) for k, v in headers.items()})

    @app.post("/v1/quality/batches/manifest")
    async def batch_manifest(
        body: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Validate a batch manifest and estimate its discounted cost."""
        auth(authorization, x_tenant_id)
        return batches.build(body.get("requests", []), float(body.get("discount", 0.5)))

    @app.post("/v1/quality/audit-reports")
    async def audit_report(
        body: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Create a redacted integrity-protected audit report."""
        auth(authorization, x_tenant_id)
        return audits.create(body.get("findings", []), int(body.get("generated_at", 0)))

    @app.post("/v1/quality/audit-reports/verify")
    async def verify_report(
        body: dict,
        authorization: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
    ) -> dict:
        """Verify an audit report's schema and SHA-256 integrity value."""
        auth(authorization, x_tenant_id)
        return {"valid": audits.verify(body)}

    return app
