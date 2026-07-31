"""Versioned REST API and accessible control-center UI."""

from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from .control_plane import ControlPlane, PermissionDenied

UI = """<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>LLM Budget Gateway Control Center</title><style>
:root{font-family:Inter,system-ui,sans-serif;color:#172033;background:#f5f7fb}*{box-sizing:border-box}body{margin:0}a{color:inherit}.skip{position:absolute;left:-999px}.skip:focus{left:12px;top:12px;background:#fff;padding:12px;z-index:3}.shell{display:grid;grid-template-columns:250px 1fr;min-height:100vh}.side{background:#10182b;color:#fff;padding:24px}.brand{font-weight:800;font-size:1.15rem}.nav{display:grid;gap:8px;margin-top:32px}.nav button{border:0;background:transparent;color:#c9d3ea;text-align:left;padding:12px;border-radius:10px;font:inherit}.nav button[aria-current=page],.nav button:hover{background:#263558;color:#fff}.main{padding:32px;max-width:1300px}.top{display:flex;justify-content:space-between;align-items:center;gap:20px}.badge{padding:6px 10px;border-radius:999px;background:#e2e8ff;color:#263b91}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin:24px 0}.card{background:#fff;border:1px solid #dde3ef;border-radius:16px;padding:20px;box-shadow:0 6px 20px #1720330d}.card h2{font-size:1rem;margin-top:0}.metric{font-size:2rem;font-weight:800}.actions{display:flex;gap:12px;flex-wrap:wrap}.primary{background:#3157d5;color:#fff;border:0;border-radius:10px;padding:12px 16px;font-weight:700}.secondary{background:#fff;border:1px solid #aab6cf;border-radius:10px;padding:12px 16px}.status{border-left:4px solid #df9b20;padding:12px;background:#fff8e6}.skeleton{animation:pulse 1.2s infinite;background:#e7ebf3;height:16px;border-radius:6px}@keyframes pulse{50%{opacity:.45}}:focus-visible{outline:3px solid #ffbf47;outline-offset:3px}@media(max-width:850px){.shell{grid-template-columns:1fr}.side{padding:16px}.nav{display:flex;overflow:auto;margin-top:16px}.grid{grid-template-columns:1fr}.main{padding:20px}.top{align-items:flex-start;flex-direction:column}}
</style></head><body><a class='skip' href='#content'>Skip to main content</a><div class='shell'><aside class='side' aria-label='Primary navigation'><div class='brand'>Budget Gateway</div><nav class='nav'><button aria-current='page'>Overview</button><button>Keys</button><button>Budgets</button><button>Routes</button><button>Policies</button><button>Observability</button></nav></aside><main class='main' id='content'><header class='top'><div><h1>Control Center</h1><p>Operate budgets, access, policy and routing from one tenant-safe workspace.</p></div><span class='badge'>Production</span></header><div class='status' role='status' aria-live='polite'><strong>Setup in progress.</strong> Create a key, budget and route to complete onboarding.</div><section class='grid' aria-label='Operational summary'><article class='card'><h2>Budget utilization</h2><div class='metric'>0%</div><p>No reconciled spend in this window.</p></article><article class='card'><h2>Active keys</h2><div class='metric'>0</div><p>Secrets are shown once and stored only as hashes.</p></article><article class='card'><h2>Healthy routes</h2><div class='metric'>0</div><p>Circuit state and recovery are visible here.</p></article></section><section class='card'><h2>Guided onboarding</h2><ol><li>Configure the workspace.</li><li>Issue a scoped virtual key.</li><li>Set a budget reservation policy.</li><li>Create a health-aware route.</li><li>Enable policy and alerts.</li></ol><div class='actions'><button class='primary'>Continue setup</button><button class='secondary'>View documentation</button></div></section><noscript><p class='status'>JavaScript is optional for reading this dashboard. Use the versioned REST API for changes.</p></noscript></main></div></body></html>"""


def create_control_app(path: str = "control-plane.db") -> FastAPI:
    cp = ControlPlane(path)
    app = FastAPI(title="LLM Budget Gateway Control API", version="1.0")

    def ctx(x_tenant_id: str | None, x_role: str | None):
        if not x_tenant_id or not x_role:
            raise HTTPException(401, "X-Tenant-Id and X-Role are required")
        return x_tenant_id, x_role

    @app.exception_handler(PermissionDenied)
    async def permission(_r: Request, e: PermissionDenied):
        return PlainTextResponse(str(e), status_code=403)

    @app.get("/control", response_class=HTMLResponse)
    async def control():
        return UI

    @app.get("/v1/admin/dashboard")
    async def dashboard(
        x_tenant_id: str | None = Header(None), x_role: str | None = Header(None)
    ):
        t, r = ctx(x_tenant_id, x_role)
        return cp.dashboard(t, r)

    @app.post("/v1/admin/workspace")
    async def workspace(
        body: dict,
        x_tenant_id: str | None = Header(None),
        x_role: str | None = Header(None),
    ):
        t, r = ctx(x_tenant_id, x_role)
        cp.configure_workspace(t, r, str(body.get("name", "")).strip())
        return {"status": "active"}

    @app.post("/v1/admin/keys", status_code=201)
    async def keys(
        body: dict,
        idempotency_key: str | None = Header(None),
        x_tenant_id: str | None = Header(None),
        x_role: str | None = Header(None),
    ):
        t, r = ctx(x_tenant_id, x_role)
        return cp.issue_key(
            t,
            r,
            body.get("label", ""),
            body.get("models", []),
            body.get("expires_at"),
            idempotency_key,
        )

    @app.get("/v1/admin/keys")
    async def list_keys(
        x_tenant_id: str | None = Header(None), x_role: str | None = Header(None)
    ):
        t, r = ctx(x_tenant_id, x_role)
        return {"items": cp.list_keys(t, r)}

    @app.put("/v1/admin/budgets/{scope}")
    async def budget(
        scope: str,
        body: dict,
        x_tenant_id: str | None = Header(None),
        x_role: str | None = Header(None),
    ):
        t, r = ctx(x_tenant_id, x_role)
        cp.set_budget(t, r, scope, float(body["limit"]))
        return cp.budget_status(t, scope)

    @app.get("/v1/admin/spend.csv", response_class=PlainTextResponse)
    async def spend(
        x_tenant_id: str | None = Header(None), x_role: str | None = Header(None)
    ):
        t, r = ctx(x_tenant_id, x_role)
        return cp.export_spend_csv(t, r)

    app.state.control_plane = cp
    return app
