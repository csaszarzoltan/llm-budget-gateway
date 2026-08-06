"""Unified browser console, catalog and local service manager API."""

from __future__ import annotations

import sqlite3
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .completion_features import MigrationPlanner, PolicyRouteSimulator
from .console_ui import catalog, render_console
from .console_workflows import get_workflow, search_workflows
from .evidence_plane import EvidenceEvent, EvidencePlane
from .market_priority import (
    ChangeImpactLab,
    CompatibilityContract,
    CompatibilityContractCatalog,
    ReplayCandidate,
    ReplayTrace,
    RuntimeGovernor,
    RuntimeStep,
)
from .p0_workflows import (
    CompatibilityProbe,
    CompatibilityRunStore,
    IncidentEvidence,
    IncidentTimelineStore,
    ProviderCompatibilityLab,
    ProviderCompatibilityRunner,
)
from .priority_features import (
    CockpitService,
    RunawayFirewall,
    RunLimits,
    RunState,
    SchemaFormService,
)
from .priority_routes import PriorityRouteStore
from .cost_tracking import CostStore
from .product_console import ProductConsoleStore
from .product_extensions import ProductExtensions
from .production_readiness import (
    AutopilotCandidate,
    OutcomeAutopilot,
    ReleaseRecoveryService,
)
from .provider_connections import (
    CredentialVault,
    ProviderConnectionStore,
    ProviderDiscovery,
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
    bootstrap = """<script>
// Apply saved theme before first paint.
(()=>{const saved=localStorage.getItem('gateway-theme');const dark=window.matchMedia('(prefers-color-scheme: dark)').matches;document.documentElement.dataset.theme=saved||(dark?'dark':'light')})();
</script>"""
    page = page.replace("<head>", "<head>" + bootstrap)
    page = page.replace("id='theme'", "id='theme' aria-pressed='false'")
    return page.replace("</head>", _STYLE + "</head>").replace(
        "</body>", _PANEL + "</body>"
    )


def _require_local_client(request: Request) -> None:
    """Restrict sensitive console evidence workflows to local callers."""
    client = request.client.host if request.client else ""
    if client not in {"127.0.0.1", "::1", "testclient", "console"}:
        raise HTTPException(403, "safety evidence workflows are local-only")


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
    routing_connection: sqlite3.Connection | None = None,
    priority_routing_connection: sqlite3.Connection | None = None,
    incident_connection: sqlite3.Connection | None = None,
    compatibility_connection: sqlite3.Connection | None = None,
    market_connection: sqlite3.Connection | None = None,
    evidence_connection: sqlite3.Connection | None = None,
    recovery_root: Path | None = None,
    project_root: Path | None = None,
    product_connection: sqlite3.Connection | None = None,
    provider_connection: sqlite3.Connection | None = None,
    cost_connection: sqlite3.Connection | None = None,
    credential_key_path: Path | None = None,
    provider_discovery_transport: object | None = None,
    auto_start_services: bool = False,
    cockpit_first: bool = False,
) -> FastAPI:
    """Create the console with local one-click lifecycle controls."""
    repository_root = project_root or Path(__file__).resolve().parents[2]
    service_manager = manager or ServiceManager(workdir=repository_root)
    extensions = ProductExtensions(
        product_connection or sqlite3.connect(":memory:", check_same_thread=False)
    )
    product = ProductConsoleStore(
        product_connection or sqlite3.connect(":memory:", check_same_thread=False)
    )
    cost_store = CostStore(
        connection=cost_connection
        or sqlite3.connect(":memory:", check_same_thread=False)
    )
    trace_store = TraceStore(
        trace_connection or sqlite3.connect(":memory:", check_same_thread=False)
    )
    provider_store = ProviderConnectionStore(
        provider_connection or sqlite3.connect(":memory:", check_same_thread=False),
        CredentialVault(
            credential_key_path
            or Path(tempfile.mkdtemp(prefix="gateway-console-")) / "provider-master.key"
        ),
    )
    routing = RoutingControlPlane(
        routing_connection or sqlite3.connect(":memory:", check_same_thread=False)
    )
    priority_routing = PriorityRouteStore(
        priority_routing_connection
        or sqlite3.connect(":memory:", check_same_thread=False)
    )
    compatibility_store = CompatibilityRunStore(
        compatibility_connection or sqlite3.connect(":memory:", check_same_thread=False)
    )
    recovery = ReleaseRecoveryService(
        recovery_root or Path(tempfile.mkdtemp(prefix="gateway-recovery-"))
    )
    evidence_plane = EvidencePlane(
        evidence_connection or sqlite3.connect(":memory:", check_same_thread=False)
    )
    market_catalog = CompatibilityContractCatalog(
        market_connection or sqlite3.connect(":memory:", check_same_thread=False)
    )
    incident_store = IncidentTimelineStore(
        incident_connection or sqlite3.connect(":memory:", check_same_thread=False)
    )
    provider_discovery = ProviderDiscovery(
        provider_store, transport=provider_discovery_transport
    )  # type: ignore[arg-type]
    compatibility_runner = ProviderCompatibilityRunner(
        provider_store, provider_discovery_transport
    )

    startup_results: list[dict[str, object]] = []

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if auto_start_services:
            startup_results[:] = service_manager.start_all()
        yield
        service_manager.stop_all()

    app = FastAPI(
        title="LLM Budget Gateway Console", version="13.2.2", lifespan=lifespan
    )
    app.state.service_manager = service_manager
    app.state.routing_control_plane = routing
    app.state.priority_routing = priority_routing

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

    if cockpit_first:

        @app.get("/", response_class=RedirectResponse)
        async def cockpit_root() -> RedirectResponse:
            """Make the product cockpit the default landing page."""
            return RedirectResponse("/cockpit")
    else:

        @app.get("/", response_class=HTMLResponse)
        async def console_root() -> str:
            return _render_managed_console()

    @app.get("/console", response_class=HTMLResponse)
    async def console() -> str:
        return _render_managed_console()

    @app.get("/v1/system/status")
    async def system_status() -> dict[str, object]:
        states = startup_results or service_manager.statuses()
        failures = [state for state in states if not state.get("reachable")]
        return {
            "ready": not failures,
            "cockpit_available": cockpit_dist.exists(),
            "services": states,
            "failures": failures,
        }

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

    @app.post("/v1/console/releases/plan")
    async def release_plan(body: dict[str, object], request: Request) -> dict[str, object]:
        """Validate provenance, backup, migration, regression, and canary gates."""
        _require_local_client(request)
        try:
            return recovery.plan_rollout(**body)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/console/releases/canary-decision")
    async def release_canary(body: dict[str, object], request: Request) -> dict[str, object]:
        """Promote or roll back a canary from measured guardrails."""
        _require_local_client(request)
        try:
            return recovery.canary_decision(**body)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/console/autopilot/recommend")
    async def autopilot_recommend(body: dict[str, object], request: Request) -> dict[str, object]:
        """Recommend a bounded route improvement without mutating production."""
        _require_local_client(request)
        try:
            return OutcomeAutopilot().recommend(
                baseline=AutopilotCandidate(**dict(body.get("baseline", {}))),
                candidates=[AutopilotCandidate(**dict(item)) for item in list(body.get("candidates", []))],
                minimum_quality=float(body.get("minimum_quality", 0)),
                minimum_success_rate=float(body.get("minimum_success_rate", 0)),
                maximum_latency_ms=float(body.get("maximum_latency_ms", 0)),
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/console/evidence/spans", status_code=201)
    async def evidence_span(body: dict[str, object], request: Request) -> dict[str, object]:
        """Persist one tenant-scoped OpenInference evidence span."""
        _require_local_client(request)
        try:
            from dataclasses import asdict
            return asdict(evidence_plane.record(EvidenceEvent(**body)))  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/console/evidence/traces/{trace_id}")
    async def evidence_trace(trace_id: str, tenant_id: str, request: Request) -> dict[str, object]:
        """Export one tenant trace in an OTLP-shaped OpenInference document."""
        _require_local_client(request)
        try:
            return evidence_plane.export_trace(tenant_id=tenant_id, trace_id=trace_id)
        except KeyError as exc:
            raise HTTPException(404, "trace evidence not found") from exc

    @app.post("/v1/console/replay/compare")
    async def replay_compare(body: dict[str, object], request: Request) -> dict[str, object]:
        """Compare privacy-safe production evidence with a candidate replay."""
        _require_local_client(request)
        try:
            from dataclasses import asdict
            result = ChangeImpactLab().compare(
                ReplayTrace(**dict(body.get("baseline", {}))),
                ReplayCandidate(**dict(body.get("candidate", {}))),
            )
            return asdict(result)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/console/governor/evaluate")
    async def governor_evaluate(body: dict[str, object], request: Request) -> dict[str, object]:
        """Detect loops, intent drift, and unapproved irreversible actions."""
        _require_local_client(request)
        try:
            from dataclasses import asdict
            decision = RuntimeGovernor(int(body.get("loop_threshold", 3))).evaluate(
                intent=str(body.get("intent", "")),
                steps=[RuntimeStep(**dict(item)) for item in list(body.get("steps", []))],
                approved_actions=set(body.get("approved_actions", [])),
            )
            return asdict(decision)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/console/contracts", status_code=201)
    async def record_contract(body: dict[str, object], request: Request) -> dict[str, object]:
        """Persist one measured provider/model capability contract."""
        _require_local_client(request)
        try:
            from dataclasses import asdict
            return asdict(market_catalog.record(CompatibilityContract(**body)))
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/console/contracts/{provider_id}")
    async def contract_matrix(provider_id: str, request: Request) -> dict[str, object]:
        """Return a provider's fresh compatibility and pricing evidence."""
        _require_local_client(request)
        return {"provider_id": provider_id, "contracts": market_catalog.matrix(provider_id)}

    @app.post("/v1/console/forms/generate")
    async def generate_form(body: dict[str, object]) -> dict[str, object]:
        """Generate accessible control metadata from a bounded JSON Schema."""
        try:
            return SchemaFormService().generate(
                str(body.get("form_id", "form")), dict(body.get("schema", {}))
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/console/compatibility/evaluate")
    async def compatibility_evaluate(
        body: dict[str, object], request: Request
    ) -> dict[str, object]:
        """Import externally measured probes for offline scoring and repair guidance."""
        _require_local_client(request)
        try:
            probes = [
                CompatibilityProbe(**dict(item))
                for item in list(body.get("probes", []))
            ]
            result = ProviderCompatibilityLab().evaluate(
                provider_id=str(body.get("provider_id", "")), probes=probes
            )
            import time

            run = compatibility_store.save(result, checked_at=int(time.time()))
            return {
                **run,
                "probes": [probe.__dict__ for probe in result.probes],
            }
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/console/compatibility/{provider_id}/run")
    async def compatibility_run(
        provider_id: str, request: Request
    ) -> dict[str, object]:
        """Execute measured, non-destructive checks against a stored provider."""
        _require_local_client(request)
        import time

        try:
            result = await compatibility_runner.run(provider_id)
            run = compatibility_store.save(result, checked_at=int(time.time()))
            return {
                **run,
                "measured": True,
                "probes": [probe.__dict__ for probe in result.probes],
            }
        except KeyError as exc:
            raise HTTPException(404, "unknown provider connection") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/console/compatibility/{provider_id}/history")
    async def compatibility_history(
        provider_id: str, request: Request, limit: int = 20
    ) -> dict[str, object]:
        """Return newest persisted compatibility runs for one provider."""
        _require_local_client(request)
        try:
            return {
                "provider_id": provider_id,
                "runs": compatibility_store.list(provider_id, limit=limit),
            }
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/console/incidents/events", status_code=201)
    async def incident_event(
        body: dict[str, object], request: Request
    ) -> dict[str, object]:
        """Append one privacy-safe event to an incident timeline."""
        _require_local_client(request)
        try:
            evidence = IncidentEvidence(**body)  # type: ignore[arg-type]
            return incident_store.append(evidence).__dict__
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/console/incidents/from-request/{request_id}")
    async def incident_from_request(
        request_id: str, request: Request
    ) -> dict[str, object]:
        """Build an incident explanation from a real product routing decision."""
        _require_local_client(request)
        try:
            item = product.activity_item(request_id)
        except KeyError as exc:
            raise HTTPException(404, "unknown request") from exc
        reason = str(
            item.get("reason")
            or ("Request succeeded" if item["success"] else "Request failed")
        )
        severity = "info" if item["success"] else "critical"
        events = [
            IncidentEvidence(
                request_id,
                0,
                "request",
                "completed" if item["success"] else "failed",
                reason,
                severity,
                {"app_id": item["app_id"], "latency_ms": item["latency_ms"]},
            ),
            IncidentEvidence(
                request_id,
                1,
                "route",
                "selected",
                f"Route {item['route']} selected {item['model']}",
                "info",
                {"route": item["route"]},
            ),
            IncidentEvidence(
                request_id,
                2,
                "provider",
                "completed" if item["success"] else "failed",
                reason,
                severity,
                {"model": item["model"]},
            ),
            IncidentEvidence(
                request_id,
                3,
                "cost",
                "recorded",
                f"Request cost ${float(item['cost_usd']):.6f}",
                "warning" if float(item["cost_usd"]) > 1 else "info",
                {"cost_usd": item["cost_usd"]},
            ),
        ]
        for event in events:
            incident_store.append(event)
        return {**incident_store.explain(request_id), "source": "product_activity"}

    @app.get("/v1/console/incidents/{incident_id}")
    async def incident_explain(incident_id: str, request: Request) -> dict[str, object]:
        """Explain why an incident happened, its impact, and the next fix."""
        _require_local_client(request)
        try:
            return incident_store.explain(incident_id)
        except KeyError as exc:
            raise HTTPException(404, "unknown incident") from exc

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

    @app.get("/v1/product/home")
    async def product_home(role: str = "developer") -> dict[str, object]:
        """Return the role-aware product home state."""
        payload = product.home(role)
        connection_count = len(provider_store.list())
        payload["counts"]["providers"] = connection_count  # type: ignore[index]
        if connection_count:
            payload["activation"]["steps"][0]["done"] = True  # type: ignore[index]
            complete = sum(step["done"] for step in payload["activation"]["steps"])  # type: ignore[index]
            payload["activation"]["complete"] = complete  # type: ignore[index]
        return payload

    @app.get("/v1/product/templates")
    async def product_templates() -> dict[str, object]:
        return {"templates": product.route_templates()}

    @app.get("/v1/product/applications")
    async def product_applications() -> dict[str, object]:
        return {"applications": product.applications()}

    @app.post("/v1/product/applications", status_code=201)
    async def create_product_application(body: dict[str, object]) -> dict[str, object]:
        try:
            return product.create_application(
                str(body.get("name", "")), str(body.get("default_route", ""))
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/product/provider-types")
    async def product_provider_types() -> dict[str, object]:
        """Return provider-specific connection fields for the setup wizard."""
        return {"provider_types": provider_store.provider_types()}

    @app.get("/v1/product/provider-connections")
    async def product_provider_connections() -> dict[str, object]:
        """List named provider accounts without secret material."""
        return {"providers": provider_store.list()}

    @app.post("/v1/product/provider-connections", status_code=201)
    async def create_product_provider_connection(
        body: dict[str, object],
    ) -> dict[str, object]:
        """Store one encrypted credential set for one named provider account."""
        try:
            return provider_store.create(body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/product/provider-connections/{provider_id}/sync-models")
    async def sync_product_provider_models(provider_id: str) -> dict[str, object]:
        """Verify credentials and download the provider-native model catalog."""
        try:
            return await provider_discovery.sync(provider_id)
        except KeyError as exc:
            raise HTTPException(404, "unknown provider connection") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/product/discovered-models")
    async def product_discovered_models() -> dict[str, object]:
        """Return every discovered model with its named provider alias."""
        models: list[dict[str, object]] = []
        for connection in provider_store.list():
            models.extend(provider_store.models(str(connection["id"])))
        return {"models": models}

    @app.get("/v1/product/provider-connections/{provider_id}/models")
    async def product_provider_models(provider_id: str) -> dict[str, object]:
        """Return models discovered for one named provider account."""
        try:
            return {"models": provider_store.models(provider_id)}
        except KeyError as exc:
            raise HTTPException(404, "unknown provider connection") from exc

    @app.get("/v1/product/providers")
    async def product_providers() -> dict[str, object]:
        return {"providers": product.providers()}

    @app.post("/v1/product/providers", status_code=201)
    async def create_product_provider(body: dict[str, object]) -> dict[str, object]:
        try:
            return product.create_provider(
                str(body.get("name", "")),
                str(body.get("slug", "")),
                str(body.get("region", "global")),
                list(body.get("models", [])),
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/product/routes")
    async def product_routes() -> dict[str, object]:
        return {"routes": product.routes()}

    @app.post("/v1/product/routes", status_code=201)
    async def create_product_route(body: dict[str, object]) -> dict[str, object]:
        try:
            return product.create_route(
                str(body.get("name", "")), list(body.get("targets", []))
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.put("/v1/product/routes/{route_id}")
    async def update_product_route(
        route_id: str, body: dict[str, object]
    ) -> dict[str, object]:
        try:
            return product.update_route(route_id, list(body.get("targets", [])))
        except KeyError as exc:
            raise HTTPException(404, "unknown route") from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/product/routes/{route_id}/dependencies")
    async def product_route_dependencies(route_id: str) -> dict[str, object]:
        try:
            return product.route_dependencies(route_id)
        except KeyError as exc:
            raise HTTPException(404, "unknown route") from exc

    @app.get("/v1/product/routes/{route_id}/versions")
    async def product_route_versions(route_id: str) -> dict[str, object]:
        try:
            return {"versions": product.route_versions(route_id)}
        except KeyError as exc:
            raise HTTPException(404, "unknown route") from exc

    @app.post("/v1/product/routes/{route_id}/duplicate", status_code=201)
    async def duplicate_product_route(route_id: str, body: dict[str, object]) -> dict[str, object]:
        try:
            return product.duplicate_route(route_id, str(body.get("name", "")))
        except KeyError as exc:
            raise HTTPException(404, "unknown route") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/product/routes/{route_id}/archive")
    async def archive_product_route(route_id: str) -> dict[str, object]:
        try:
            return product.archive_route(route_id)
        except KeyError as exc:
            raise HTTPException(404, "unknown route") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/v1/product/routes/{route_id}/restore")
    async def restore_product_route(route_id: str) -> dict[str, object]:
        try:
            return product.restore_route(route_id)
        except KeyError as exc:
            raise HTTPException(404, "unknown route") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.delete("/v1/product/routes/{route_id}", status_code=204)
    async def delete_product_route(route_id: str, body: dict[str, object]) -> None:
        try:
            product.delete_route(route_id, confirmation=str(body.get("confirmation", "")))
        except KeyError as exc:
            raise HTTPException(404, "unknown route") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/v1/product/routes/{route_id}/validate")
    async def validate_product_route(route_id: str) -> dict[str, object]:
        try:
            return product.validate_route(route_id)
        except KeyError as exc:
            raise HTTPException(404, "unknown route") from exc

    @app.post("/v1/product/routes/{route_id}/simulate")
    async def simulate_product_route(route_id: str, body: dict[str, object]) -> dict[str, object]:
        try:
            return product.simulate_route(route_id, capabilities=list(body.get("capabilities", [])), budget_remaining_usd=float(body.get("budget_remaining_usd", 0)))
        except KeyError as exc:
            raise HTTPException(404, "unknown route") from exc
        except (TypeError, ValueError, RuntimeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/product/routes/{route_id}/publish")
    async def publish_product_route(route_id: str) -> dict[str, object]:
        try:
            return product.publish_route(route_id)
        except KeyError as exc:
            raise HTTPException(404, "unknown route") from exc

    @app.post("/v1/product/routes/{route_id}/test")
    async def test_product_route(
        route_id: str, body: dict[str, object]
    ) -> dict[str, object]:
        try:
            return product.test_route(
                route_id, str(body.get("at")), list(body.get("capabilities", []))
            )
        except KeyError as exc:
            raise HTTPException(404, "unknown route") from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/product/activity")
    async def product_activity() -> dict[str, object]:
        return {"activity": product.activity()}

    @app.get("/v1/product/usage")
    async def product_usage(
        days: int = 14, route: str = ""
    ) -> dict[str, object]:
        base = product.usage()
        base["daily"] = cost_store.daily_usage(
            days=max(1, min(days, 90)), route=route or None
        )
        return base

    @app.post("/v1/product/applications/{app_id}/keys/rotate")
    async def rotate_product_key(app_id: str) -> dict[str, object]:
        return extensions.rotate_key(app_id)

    @app.post("/v1/product/keys/{key_id}/revoke")
    async def revoke_product_key(key_id: str) -> dict[str, object]:
        try:
            return extensions.revoke_key(key_id)
        except KeyError as exc:
            raise HTTPException(404, "unknown active key") from exc

    @app.put("/v1/product/budgets/{scope}")
    async def set_product_budget(
        scope: str, body: dict[str, object]
    ) -> dict[str, object]:
        try:
            return extensions.set_budget(
                scope, float(body.get("limit_usd", 0)), int(body.get("reset_day", 1))
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/product/alerts")
    async def product_alerts() -> dict[str, object]:
        return {"alerts": extensions.alerts()}

    @app.post("/v1/product/alerts", status_code=201)
    async def create_product_alert(body: dict[str, object]) -> dict[str, object]:
        try:
            return extensions.create_alert(
                str(body.get("name", "")),
                str(body.get("metric", "")),
                float(body.get("threshold", 0)),
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/product/environments")
    async def product_environments() -> dict[str, object]:
        return {"environments": extensions.environments()}

    @app.post("/v1/product/environments", status_code=201)
    async def create_product_environment(body: dict[str, object]) -> dict[str, object]:
        try:
            return extensions.create_environment(
                str(body.get("name", "")),
                str(body.get("base_url", "")),
                bool(body.get("default", False)),
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/product/views")
    async def product_views(role: str = "developer") -> dict[str, object]:
        return {"views": extensions.views(role)}

    @app.post("/v1/product/views", status_code=201)
    async def create_product_view(body: dict[str, object]) -> dict[str, object]:
        return extensions.save_view(
            str(body.get("name", "")),
            str(body.get("role", "developer")),
            dict(body.get("filters", {})),
        )

    @app.post("/v1/product/providers/{provider_id}/check")
    async def check_product_provider(
        provider_id: str, body: dict[str, object]
    ) -> dict[str, object]:
        return extensions.provider_check(
            provider_id, bool(body.get("healthy", True)), int(body.get("latency_ms", 0))
        )

    @app.post("/v1/product/routes/{route_id}/snapshots/{version}")
    async def snapshot_product_route(
        route_id: str, version: int, body: dict[str, object]
    ) -> dict[str, object]:
        return extensions.snapshot_route(route_id, version, body)

    @app.post("/v1/product/routes/{route_id}/rollback/{version}")
    async def rollback_product_route(route_id: str, version: int) -> dict[str, object]:
        try:
            return extensions.rollback_route(route_id, version)
        except KeyError as exc:
            raise HTTPException(404, "unknown route snapshot") from exc

    @app.post("/v1/product/archive/{kind}/{resource_id}")
    async def archive_product_resource(
        kind: str, resource_id: str, body: dict[str, object]
    ) -> dict[str, object]:
        return extensions.archive(kind, resource_id, body)

    @app.get("/v1/product/export")
    async def export_product_bundle() -> dict[str, object]:
        return extensions.export_bundle()

    @app.post("/v1/product/import")
    async def import_product_bundle(body: dict[str, object]) -> dict[str, object]:
        try:
            return extensions.import_bundle(body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/product/recommendations")
    async def product_recommendations() -> dict[str, object]:
        return {"recommendations": extensions.recommendations()}

    @app.get("/v1/product/audit")
    async def product_audit() -> dict[str, object]:
        return {"audit": extensions.audit()}

    # Logical routing administration API.
    @app.post("/v1/admin/applications", status_code=201)
    async def admin_create_application(body: dict[str, object]) -> dict[str, object]:
        try:
            return routing.create_application(
                str(body.get("name", "")), str(body.get("default_route", ""))
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/admin/applications")
    async def admin_list_applications() -> dict[str, object]:
        return {"applications": routing.list_applications()}

    @app.post("/v1/admin/routes", status_code=201)
    async def admin_create_route(body: dict[str, object]) -> dict[str, object]:
        try:
            return routing.create_route(dict(body))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/admin/routes")
    async def admin_list_routes() -> dict[str, object]:
        return {"routes": routing.list_routes()}

    @app.get("/v1/admin/routes/{route_id}")
    async def admin_get_route(route_id: str) -> dict[str, object]:
        try:
            return routing.get_route(route_id)
        except KeyError as exc:
            raise HTTPException(404, "unknown route") from exc

    @app.put("/v1/admin/routes/{route_id}")
    async def admin_update_route(
        route_id: str, body: dict[str, object]
    ) -> dict[str, object]:
        try:
            return routing.update_route(route_id, dict(body))
        except KeyError as exc:
            raise HTTPException(404, "unknown route") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/admin/routes/{route_id}/publish")
    async def admin_publish_route(route_id: str) -> dict[str, object]:
        try:
            return routing.publish_route(route_id)
        except KeyError as exc:
            raise HTTPException(404, "unknown route") from exc

    @app.post("/v1/admin/routes/{route_id}/rollback")
    async def admin_rollback_route(route_id: str) -> dict[str, object]:
        try:
            return routing.rollback_route(route_id)
        except KeyError as exc:
            raise HTTPException(404, "unknown route") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/admin/routes/{route_id}/simulate")
    async def admin_simulate_route(
        route_id: str, body: dict[str, object]
    ) -> dict[str, object]:
        from datetime import datetime

        try:
            routing.get_route(route_id)
            return routing.simulate(
                route_id,
                now=datetime.fromisoformat(str(body.get("at", ""))),
                quality_tier=str(body.get("quality_tier", "balanced")),
                estimated_cost=float(body.get("estimated_cost", 0)),
                spend_by_model=dict(body.get("spend_by_model", {})),
                health=dict(body.get("health", {})),
                region=str(body.get("region", "")),
                capabilities=list(body.get("capabilities", [])),
            )
        except KeyError as exc:
            raise HTTPException(404, "unknown route") from exc
        except (TypeError, ValueError, RuntimeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/admin/routes/{route_id}/activity")
    async def admin_route_activity(route_id: str) -> dict[str, object]:
        try:
            routing.get_route(route_id)
            return {"activity": routing.route_activity(route_id)}
        except KeyError as exc:
            raise HTTPException(404, "unknown route") from exc

    # Priority route administration API.
    @app.post("/v1/admin/priority-routes", status_code=201)
    async def admin_create_priority_route(body: dict[str, object]) -> dict[str, object]:
        try:
            return priority_routing.create_route(
                str(body.get("name", "")), list(body.get("targets", []))
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/admin/priority-routes")
    async def admin_list_priority_routes() -> dict[str, object]:
        return {"routes": priority_routing.list_routes()}

    @app.get("/v1/admin/priority-routes/{route_id}")
    async def admin_get_priority_route(route_id: str) -> dict[str, object]:
        try:
            return priority_routing.get_route(route_id)
        except KeyError as exc:
            raise HTTPException(404, "unknown priority route") from exc

    @app.put("/v1/admin/priority-routes/{route_id}")
    async def admin_update_priority_route(
        route_id: str, body: dict[str, object]
    ) -> dict[str, object]:
        try:
            return priority_routing.update_route(
                route_id, str(body.get("name", "")), list(body.get("targets", []))
            )
        except KeyError as exc:
            raise HTTPException(404, "unknown priority route") from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/admin/priority-routes/{route_id}/publish")
    async def admin_publish_priority_route(route_id: str) -> dict[str, object]:
        try:
            return priority_routing.publish(route_id)
        except KeyError as exc:
            raise HTTPException(404, "unknown priority route") from exc

    @app.post("/v1/admin/priority-routes/{route_id}/simulate")
    async def admin_simulate_priority_route(
        route_id: str, body: dict[str, object]
    ) -> dict[str, object]:
        from datetime import datetime

        try:
            route = priority_routing.get_route(route_id)
            return priority_routing.resolve(
                str(route["name"]),
                at=datetime.fromisoformat(str(body.get("at", ""))),
                capabilities=list(body.get("capabilities", [])),
            )
        except KeyError as exc:
            raise HTTPException(404, "unknown priority route") from exc
        except (TypeError, ValueError, RuntimeError) as exc:
            raise HTTPException(422, str(exc)) from exc

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
