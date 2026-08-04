"""Unified browser console, catalog and local service manager API."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .completion_features import MigrationPlanner, PolicyRouteSimulator
from .console_ui import catalog, render_console
from .console_workflows import get_workflow, search_workflows
from .priority_features import (
    CockpitService,
    RunawayFirewall,
    RunLimits,
    RunState,
    SchemaFormService,
)
from .routing_control_plane import RoutingControlPlane
from .service_manager import ServiceManager
from .supply_chain import SBOMService, UpgradeRiskService
from .trace_outcomes import OutcomeAnalytics, OutcomeRecord, TraceSpan, TraceStore

_STYLE = """
<style>
#svc-launcher{position:fixed;right:18px;bottom:18px;z-index:90;border:0;border-radius:999px;padding:12px 17px;background:#2f5bea;color:#fff;font:600 14px system-ui;box-shadow:0 12px 30px #20377a55;cursor:pointer}
#svc-panel{position:fixed;inset:0 0 0 auto;z-index:95;width:min(620px,100%);padding:22px;background:#fff;color:#172033;border-left:1px solid #dce2ec;box-shadow:-24px 0 60px #0003;transform:translateX(105%);transition:.22s;overflow:auto;font:14px/1.45 system-ui}
[data-theme=dark] #svc-panel{background:#121a2c;color:#f4f7ff;border-color:#2a3650}#svc-panel.open{transform:none}.svc-head,.svc-actions,.svc-row{display:flex;align-items:center;gap:10px}.svc-head{justify-content:space-between}.svc-actions{flex-wrap:wrap;margin:12px 0 18px}.svc-row{justify-content:space-between;padding:12px 0;border-bottom:1px solid #dce2ec}.svc-row small{display:block;color:#647089}.svc-buttons{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}.svc-btn{border:1px solid #ccd4e2;border-radius:9px;padding:7px 10px;background:#fff;color:#172033;cursor:pointer}.svc-btn.primary{background:#2f5bea;color:#fff;border-color:#2f5bea}.svc-btn.danger{color:#b42331}.svc-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:#9aa4b5}.svc-dot.ok{background:#16845b}.svc-dot.wait{background:#ad6300}.svc-close{border:0;background:transparent;color:inherit;font-size:26px;cursor:pointer}#svc-message{padding:10px;border-radius:9px;background:#eef2f8;color:#45516a}[data-theme=dark] #svc-message{background:#1a2439;color:#a7b1c7}@media(max-width:640px){#svc-panel{width:100%}.svc-row{align-items:flex-start;flex-direction:column}.svc-buttons{justify-content:flex-start}}
</style>
"""

_PANEL = """
<button id="svc-launcher" type="button">Manage services</button>
<aside id="svc-panel" aria-label="Local service manager" aria-hidden="true">
  <div class="svc-head"><div><small>LOCAL DEVELOPMENT</small><h2>Service manager</h2></div><button id="svc-close" class="svc-close" aria-label="Close">x</button></div>
  <p>Start or stop each FastAPI service from this console. Child logs are written under <code>.gateway-console/logs/</code>.</p>
  <div class="svc-actions"><button class="svc-btn primary" id="svc-start-all">Start all</button><button class="svc-btn danger" id="svc-stop-all">Stop all</button><button class="svc-btn" id="svc-refresh">Refresh</button></div>
  <p id="svc-message" role="status" aria-live="polite">Ready.</p><div id="svc-list"></div>
</aside>
<script>
(()=>{const panel=document.getElementById('svc-panel'),list=document.getElementById('svc-list'),msg=document.getElementById('svc-message');const headers={'X-Console-Action':'1'};
function esc(x){return String(x).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function request(path,method='GET'){msg.textContent='Working...';const r=await fetch(path,{method,headers});const body=await r.json().catch(()=>({detail:'Invalid response'}));if(!r.ok)throw new Error(body.detail||`HTTP ${r.status}`);return body}
function render(items){list.innerHTML=items.map(s=>`<div class="svc-row"><div><strong><span class="svc-dot ${s.reachable?'ok':s.running?'wait':''}"></span>${esc(s.name)}</strong><small>:${s.port} · ${s.reachable?'reachable':s.running?'starting':'stopped'}${s.pid?' · PID '+s.pid:''}</small></div><div class="svc-buttons"><button class="svc-btn primary" data-start="${esc(s.slug)}" ${s.running?'disabled':''}>Start</button><button class="svc-btn danger" data-stop="${esc(s.slug)}" ${!s.managed?'disabled':''}>Stop</button><a class="svc-btn" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">Open</a></div></div>`).join('');
list.querySelectorAll('[data-start]').forEach(b=>b.onclick=()=>act(`/v1/console/services/${b.dataset.start}/start`));list.querySelectorAll('[data-stop]').forEach(b=>b.onclick=()=>act(`/v1/console/services/${b.dataset.stop}/stop`))}
async function refresh(){try{const body=await request('/v1/console/services');render(body.services);msg.textContent=`${body.running} managed process(es), ${body.reachable} reachable service(s).`}catch(e){msg.textContent=e.message}}
async function act(path){try{await request(path,'POST');await new Promise(r=>setTimeout(r,350));await refresh()}catch(e){msg.textContent=e.message}}
document.getElementById('svc-launcher').onclick=()=>{panel.classList.add('open');panel.setAttribute('aria-hidden','false');refresh()};document.getElementById('svc-close').onclick=()=>{panel.classList.remove('open');panel.setAttribute('aria-hidden','true')};document.getElementById('svc-refresh').onclick=refresh;document.getElementById('svc-start-all').onclick=()=>act('/v1/console/services/start-all');document.getElementById('svc-stop-all').onclick=()=>act('/v1/console/services/stop-all');
})();
</script>
"""


def _render_managed_console() -> str:
    page = render_console()
    return page.replace("</head>", _STYLE + "</head>").replace(
        "</body>", _PANEL + "</body>"
    )


def _require_local_action(request: Request, action: str | None) -> None:
    client = request.client.host if request.client else ""
    if client not in {"127.0.0.1", "::1", "testclient", "console"}:
        raise HTTPException(
            403, "service management is restricted to the local machine"
        )
    if action != "1":
        raise HTTPException(403, "X-Console-Action: 1 is required")


def create_console_app(
    manager: ServiceManager | None = None,
    *,
    trace_connection: sqlite3.Connection | None = None,
    project_root: Path | None = None,
    routing_connection: sqlite3.Connection | None = None,
) -> FastAPI:
    """Create the console with local one-click lifecycle controls."""
    service_manager = manager or ServiceManager()
    repository_root = project_root or Path(__file__).resolve().parents[2]
    if routing_connection is None:
        routing_url = os.getenv(
            "GATEWAY_ROUTING_DATABASE_URL", "sqlite:///.gateway-console/routing.db"
        )
        routing_path = Path(routing_url.removeprefix("sqlite:///"))
        routing_path.parent.mkdir(parents=True, exist_ok=True)
        routing_connection = sqlite3.connect(routing_path, check_same_thread=False)
    routing = RoutingControlPlane(routing_connection)
    trace_store = TraceStore(
        trace_connection or sqlite3.connect(":memory:", check_same_thread=False)
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        service_manager.stop_all()

    app = FastAPI(
        title="LLM Budget Gateway Console", version="8.0.0", lifespan=lifespan
    )

    cockpit_dist = Path(__file__).resolve().parents[2] / "ui" / "dist"
    if cockpit_dist.exists():
        app.mount(
            "/assets",
            StaticFiles(directory=cockpit_dist / "assets"),
            name="cockpit-assets",
        )

        @app.get("/cockpit", response_class=FileResponse)
        async def cockpit() -> FileResponse:
            """Serve the production React AI Operations Cockpit."""
            return FileResponse(cockpit_dist / "index.html")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    @app.get("/console", response_class=HTMLResponse)
    async def console() -> str:
        return _render_managed_console()

    @app.get("/v1/console/catalog")
    async def console_catalog() -> dict[str, object]:
        centers = catalog()
        return {
            "version": "8.0.0",
            "centers": centers,
            "center_count": len(centers),
            "capability_count": sum(len(center["capabilities"]) for center in centers),
        }

    @app.get("/v1/console/workflows")
    async def workflows(q: str = "") -> dict[str, object]:
        """Return task-oriented workflows, optionally filtered by symptom."""
        items = search_workflows(q)
        return {"version": "1", "count": len(items), "workflows": items}

    @app.get("/v1/console/workflows/{workflow_id}")
    async def workflow(workflow_id: str) -> dict[str, object]:
        """Return one guided workflow by stable identifier."""
        item = get_workflow(workflow_id)
        if item is None:
            raise HTTPException(404, "unknown workflow")
        return {"version": "1", "workflow": item}

    @app.post("/v1/console/cockpit/summary")
    async def cockpit_summary(body: dict[str, object]) -> dict[str, object]:
        """Combine spend, quality, operations and governance into one summary."""
        try:
            return CockpitService().summarize(
                spend=dict(body.get("spend", {})),
                quality=dict(body.get("quality", {})),
                operations=dict(body.get("operations", {})),
                governance=dict(body.get("governance", {})),
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/console/runaway/evaluate")
    async def runaway_evaluate(body: dict[str, object]) -> dict[str, object]:
        """Explain whether an agent run may execute its next step."""
        try:
            state = RunState(**dict(body.get("state", {})))
            limits = RunLimits(**dict(body.get("limits", {})))
            decision = RunawayFirewall().evaluate(state, limits)
            return {
                "allowed": decision.allowed,
                "code": decision.code,
                "explanation": decision.explanation,
                "next_action": decision.next_action,
            }
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/console/forms/generate")
    async def generate_form(body: dict[str, object]) -> dict[str, object]:
        """Generate accessible control metadata from a bounded JSON Schema."""
        try:
            return SchemaFormService().generate(
                str(body.get("form_id", "form")), dict(body.get("schema", {}))
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/console/traces", status_code=201)
    async def create_trace(body: dict[str, object]) -> dict[str, object]:
        """Append one privacy-safe trace span."""
        try:
            span = TraceSpan(**body)
            trace_store.append(span)
            return {"span_id": span.span_id, "run_id": span.run_id}
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/console/traces")
    async def list_traces(x_tenant_id: str | None = Header(None)) -> dict[str, object]:
        """List privacy-safe trace run summaries for one tenant."""
        if not x_tenant_id:
            raise HTTPException(422, "X-Tenant-Id is required")
        return {"runs": trace_store.list_runs(x_tenant_id)}

    @app.get("/v1/console/traces/{run_id}")
    async def get_trace(
        run_id: str, x_tenant_id: str | None = Header(None)
    ) -> dict[str, object]:
        """Return one tenant-isolated nested trace."""
        if not x_tenant_id:
            raise HTTPException(422, "X-Tenant-Id is required")
        try:
            return {"run_id": run_id, "trace": trace_store.trace(x_tenant_id, run_id)}
        except KeyError as exc:
            raise HTTPException(404, "unknown trace") from exc

    @app.post("/v1/console/outcomes/summary")
    async def outcome_summary(body: dict[str, object]) -> dict[str, object]:
        """Calculate cost-to-outcome unit economics and breakdowns."""
        try:
            records = [OutcomeRecord(**item) for item in body.get("records", [])]
            return OutcomeAnalytics().summarize(records)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/console/supply-chain/sbom")
    async def supply_chain_sbom() -> dict[str, object]:
        """Return a deterministic SBOM for pinned Python and npm dependencies."""
        package_lock = repository_root / "ui" / "package-lock.json"
        try:
            return SBOMService().generate(
                pyproject=repository_root / "pyproject.toml",
                package_lock=package_lock if package_lock.exists() else None,
            )
        except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/console/supply-chain/upgrade-risk")
    async def supply_chain_upgrade_risk(body: dict[str, object]) -> dict[str, object]:
        """Assess a dependency diff before automated rollout."""
        try:
            return UpgradeRiskService().assess(
                current=dict(body.get("current", {})),
                proposed=dict(body.get("proposed", {})),
                security_advisories=dict(body.get("security_advisories", {})),
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/console/simulate")
    async def simulate_policy_route(body: dict[str, object]) -> dict[str, object]:
        """Simulate policy and route decisions without provider execution."""
        try:
            return PolicyRouteSimulator().simulate(
                request=dict(body.get("request", {})),
                policy=dict(body.get("policy", {})),
                routes=list(body.get("routes", [])),
                minimum_quality=float(body.get("minimum_quality", 0)),
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/console/production/migration-readiness")
    async def migration_readiness(body: dict[str, object]) -> dict[str, object]:
        """Evaluate SQLite-to-Postgres production migration evidence."""
        try:
            return MigrationPlanner().assess(**body)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/admin/applications", status_code=201)
    async def create_application(body: dict[str, object]) -> dict[str, object]:
        """Connect an application and return its gateway key once."""
        try:
            return routing.create_application(
                str(body.get("name", "")), str(body.get("default_route", ""))
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/admin/applications")
    async def list_applications() -> dict[str, object]:
        """List connected applications without secret material."""
        return {"applications": routing.list_applications()}

    @app.post("/v1/admin/routes", status_code=201)
    async def create_route(body: dict[str, object]) -> dict[str, object]:
        """Create a validated logical route draft."""
        try:
            return routing.create_route(body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/admin/routes")
    async def list_routes() -> dict[str, object]:
        """List logical routes and current versions."""
        return {"routes": routing.list_routes()}

    @app.get("/v1/admin/routes/{route_id}")
    async def get_route(route_id: str) -> dict[str, object]:
        """Return one logical route."""
        try:
            return routing.get_route(route_id)
        except KeyError as exc:
            raise HTTPException(404, "unknown route") from exc

    @app.put("/v1/admin/routes/{route_id}")
    async def update_route(route_id: str, body: dict[str, object]) -> dict[str, object]:
        """Create a new immutable draft version."""
        try:
            return routing.update_route(route_id, body)
        except KeyError as exc:
            raise HTTPException(404, "unknown route") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/admin/routes/{route_id}/simulate")
    async def simulate_route(
        route_id: str, body: dict[str, object]
    ) -> dict[str, object]:
        """Preview the exact routing decision without calling a provider."""
        try:
            return routing.simulate(
                route_id,
                now=datetime.fromisoformat(str(body.get("at"))),
                quality_tier=str(body.get("quality_tier", "balanced")),
                estimated_cost=float(body.get("estimated_cost", 0)),
                spend_by_model=dict(body.get("spend_by_model", {})),
                health=dict(body.get("health", {})),
                region=str(body.get("region", "eu")),
                capabilities=list(body.get("capabilities", [])),
            )
        except KeyError as exc:
            raise HTTPException(404, "unknown route") from exc
        except (TypeError, ValueError, RuntimeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/admin/routes/{route_id}/publish")
    async def publish_route(route_id: str) -> dict[str, object]:
        """Publish the current route draft atomically."""
        try:
            return routing.publish_route(route_id)
        except KeyError as exc:
            raise HTTPException(404, "unknown route") from exc

    @app.post("/v1/admin/routes/{route_id}/rollback")
    async def rollback_route(route_id: str) -> dict[str, object]:
        """Restore the immediately previous production version."""
        try:
            return routing.rollback_route(route_id)
        except KeyError as exc:
            raise HTTPException(404, "unknown route") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/v1/admin/routes/{route_id}/usage")
    async def route_usage(route_id: str) -> dict[str, object]:
        """Return current-month model spend and budget headroom."""
        try:
            return routing.route_usage(route_id, at=datetime.now(UTC))
        except KeyError as exc:
            raise HTTPException(404, "unknown route") from exc

    @app.get("/v1/admin/routes/{route_id}/activity")
    async def route_activity(route_id: str) -> dict[str, object]:
        """Return newest-first explainable decisions."""
        try:
            routing.get_route(route_id)
            return {"activity": routing.route_activity(route_id)}
        except KeyError as exc:
            raise HTTPException(404, "unknown route") from exc

    @app.get("/v1/console/services")
    async def services(
        request: Request, x_console_action: str | None = Header(None)
    ) -> dict[str, object]:
        _require_local_action(request, x_console_action)
        states = service_manager.statuses()
        return {
            "services": states,
            "running": sum(bool(x["running"]) for x in states),
            "reachable": sum(bool(x["reachable"]) for x in states),
        }

    @app.post("/v1/console/services/{slug}/start")
    async def start_service(
        slug: str, request: Request, x_console_action: str | None = Header(None)
    ) -> dict[str, object]:
        _require_local_action(request, x_console_action)
        try:
            return service_manager.start(slug)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/v1/console/services/{slug}/stop")
    async def stop_service(
        slug: str, request: Request, x_console_action: str | None = Header(None)
    ) -> dict[str, object]:
        _require_local_action(request, x_console_action)
        try:
            return service_manager.stop(slug)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/v1/console/services/start-all")
    async def start_all(
        request: Request, x_console_action: str | None = Header(None)
    ) -> dict[str, object]:
        _require_local_action(request, x_console_action)
        return {"services": service_manager.start_all()}

    @app.post("/v1/console/services/stop-all")
    async def stop_all(
        request: Request, x_console_action: str | None = Header(None)
    ) -> dict[str, object]:
        _require_local_action(request, x_console_action)
        return {"services": service_manager.stop_all()}

    return app
