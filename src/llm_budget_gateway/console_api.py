"""Unified browser console, catalog and local service manager API."""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles

from .alert_api import build_alerts_router, create_alerts_app
from .alert_models import AlertRule
from .completion_features import MigrationPlanner, PolicyRouteSimulator
from .console_ui import catalog, render_console
from .console_workflows import get_workflow, search_workflows
from .control_plane import ControlPlane
from .cost_tracking import CostStore
from .dispatch_engine import (
    AlertDispatcher,
    ChannelAdapter,
    EmailDispatcher,
    SlackDispatcher,
    TelegramDispatcher,
    WebhookDispatcher,
)
from .evaluation_suite import (
    AuditReport,
    BatchManifest,
    EvaluationStore,
    ReleaseGate,
    RuleEvaluator,
)
from .evidence_plane import EvidenceEvent, EvidencePlane
from .market_features import (
    CostAwareRouter as MarketCostAwareRouter,
)
from .market_features import (
    ExactResponseCache,
    PIIRedactor,
    UsageAnomalyDetector,
)
from .market_priority import (
    ChangeImpactLab,
    CompatibilityContract,
    CompatibilityContractCatalog,
    ReplayCandidate,
    ReplayTrace,
    RuntimeGovernor,
    RuntimeStep,
)
from .operations_suite import PromptRegistry, QuotaDiagnostic, SLOMonitor
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
    replay_executor: object | None = None,
    alerts_db_path: str | Path | None = None,
) -> FastAPI:
    """Create the console with local one-click lifecycle controls.

    The console mounts the alert notification feature (BLOCKER-2): the
    alert rules/history API is reachable at ``/api/alerts`` and the
    ``app.state.alert_dispatcher`` is a real ``AlertDispatcher`` built
    from the persisted alert rules, so triggered budgets fire through
    the webhook/slack/telegram/email adapters with the canonical
    SSRFGuard in front of every target.
    """
    repository_root = project_root or Path(__file__).resolve().parents[2]
    service_manager = manager or ServiceManager(workdir=repository_root)
    control_plane = ControlPlane(":memory:")
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
    attribution = cost_store.attribution_store()
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

    # ─── Alert notification feature (BLOCKER-2 production wiring) ───────
    # The standalone alerts app is mounted so the rules/history API is
    # reachable through the shipped console, and a real AlertDispatcher
    # is wired in with adapters built from the persisted rule configs.
    # The dispatcher is a module-level singleton built lazily so every
    # console app instance shares one cooldown ledger and retry budget.
    _alerts_db = str(
        alerts_db_path or (repository_root / ".gateway-console" / "alerts.db")
    )
    # Ensure the DB's parent directory exists (a bare tmp_path project
    # root has no .gateway-console yet; the system launcher creates it,
    # but create_console_app must be self-sufficient).
    Path(_alerts_db).parent.mkdir(parents=True, exist_ok=True)
    alerts_app = create_alerts_app(_alerts_db)
    # Include the alert routes directly (not a sub-app mount or
    # include_router) so the console serves the exact ``/api/alerts``
    # paths without shadowing its own routes (a mount at ``/`` would
    # 404 /health etc.) and keeps them flat in ``app.routes``.
    alerts_router = build_alerts_router(alerts_app.state.db)
    app.router.routes.extend(alerts_router.routes)
    app.state.alerts_app = alerts_app

    def build_alert_adapter(rule: AlertRule) -> object:
        """Build a channel adapter from a persisted alert rule config.

        Each adapter carries the rule's own config (URL/token/host), so
        dispatch targets exactly what the rule author configured — with
        the canonical SSRFGuard re-checking the target at dispatch time
        inside the adapters themselves (defense-in-depth).
        """
        channel = rule.channel.value if hasattr(rule.channel, "value") else str(rule.channel)
        cfg = rule.config or {}
        if channel == "webhook":
            return WebhookDispatcher(
                url=str(cfg.get("url", "")), secret=str(cfg.get("secret", ""))
            )
        if channel == "slack":
            return SlackDispatcher(
                bot_token=str(cfg.get("bot_token", "")),
                channel=str(cfg.get("channel", "")),
            )
        if channel == "telegram":
            return TelegramDispatcher(
                bot_token=str(cfg.get("bot_token", "")),
                chat_id=str(cfg.get("chat_id", "")),
            )
        if channel == "email":
            return EmailDispatcher(
                host=str(cfg.get("host", "")),
                port=int(cfg.get("port", 587)),
                username=str(cfg.get("username", "")),
                password=str(cfg.get("password", "")),
                to_address=str(cfg.get("to_address", "")),
                from_address=str(cfg.get("from_address", "")) or None,
                use_tls=bool(cfg.get("use_tls", True)),
            )
        raise ValueError(f"unknown alert channel: {channel}")

    def build_alert_dispatcher() -> AlertDispatcher:
        """Rebuild the dispatcher registry from the current alert rules.

        Called on every evaluate so rules created through the API are
        picked up without a restart; adapters are cheap to construct.
        """
        from .alert_models import AlertRule as _Rule

        rows = alerts_app.state.db.execute(
            "SELECT id, channel, config FROM alert_rules WHERE enabled=1"
        ).fetchall()
        adapters: dict[str, ChannelAdapter] = {}
        for row in rows:
            try:
                rule = _Rule(
                    name="wired",
                    threshold=0.0,
                    channel=row["channel"],
                    config=json.loads(row["config"]),
                )
                adapters[row["channel"]] = build_alert_adapter(rule)  # type: ignore[assignment]
            except (ValueError, TypeError, KeyError):
                continue
        # Every channel must have an adapter in the registry (matching the
        # dispatch engine's default registry contract). Channels without a
        # persisted rule get an empty-shell adapter; the SSRF guard inside
        # the adapters still blocks any unsafe target, and events for
        # unconfigured channels fail cleanly through the retry machinery.
        for channel, adapter in (
            ("webhook", WebhookDispatcher(url="", secret="")),
            ("slack", SlackDispatcher(bot_token="", channel="")),
            ("telegram", TelegramDispatcher(bot_token="", chat_id="")),
            ("email", EmailDispatcher(host="")),
        ):
            adapters.setdefault(channel, adapter)
        return AlertDispatcher(adapters=adapters)

    _dispatcher_singleton: AlertDispatcher | None = None
    app.state.build_alert_adapter = build_alert_adapter
    app.state.build_alert_dispatcher = build_alert_dispatcher
    app.state.alert_dispatcher = _dispatcher_singleton or build_alert_dispatcher()

    @app.post("/v1/console/alerts/evaluate")
    async def console_alerts_evaluate(body: dict[str, object]) -> dict[str, object]:
        """Evaluate all alert rules for a tenant and fire triggered ones.

        Production caller of ``evaluate_alerts(dispatch=...)`` — the
        dispatcher is rebuilt from the persisted rules on every call so
        new rules take effect immediately. Dispatch is asynchronous
        (fire-and-forget inside the control plane), so this endpoint
        never blocks on webhook/SMTP delivery.
        """
        dispatcher = build_alert_dispatcher()
        triggered = control_plane.evaluate_alerts(
            str(body.get("tenant", "default")), dispatch=dispatcher
        )
        return {"alerts": triggered}


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

    @app.post("/v1/console/replay/run")
    async def replay_run(body: dict[str, object], request: Request) -> dict[str, object]:
        """Execute a bounded candidate replay through the local gateway."""
        _require_local_client(request)
        if replay_executor is None:
            raise HTTPException(409, "replay executor is not configured")
        try:
            from dataclasses import asdict

            from .replay_execution import ReplayRequest

            result = await replay_executor.execute(
                ReplayRequest(
                    request_id=str(body.get("request_id", "")),
                    model=str(body.get("candidate_model", "")),
                    messages=tuple(body.get("messages", [])),
                    max_completion_tokens=int(
                        body.get("max_completion_tokens", 256)
                    ),
                    estimated_cost_usd=float(body.get("estimated_cost_usd", 0)),
                )
            )
            baseline_cost = float(body.get("baseline_cost_usd", 0))
            return {
                "executed": True,
                "candidate": asdict(result),
                "impact": {
                    "cost_delta_usd": result.estimated_cost_usd - baseline_cost,
                    "tokens": result.tokens,
                    "latency_ms": result.latency_ms,
                },
            }
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
        # Token totals for the "Last 24 hours" metric strip (cost_store owns
        # token data; pc_activity only carries cost/latency).
        try:
            day = cost_store.usage_by_period(period="day", days=1, route=None)
            tokens = {"tokens": 0, "prompt_tokens": 0, "completion_tokens": 0}
            for bucket in day.get("days", []):
                for m in bucket.get("models", []):
                    tokens["tokens"] += int(m.get("total_tokens", 0))
                    tokens["prompt_tokens"] += int(m.get("prompt_tokens", 0))
                    tokens["completion_tokens"] += int(
                        m.get("completion_tokens", 0)
                    )
            payload["metrics"].update(tokens)  # type: ignore[index]
        except Exception:
            pass
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

    @app.put("/v1/product/provider-connections/{provider_id}")
    async def update_product_provider_connection(
        provider_id: str, body: dict[str, object]
    ) -> dict[str, object]:
        """Update a provider connection (name/slug/region/base_url/key).

        Secret fields are only replaced when the payload carries a non-empty
        value — an empty api_key means "keep the stored key".
        """
        try:
            return provider_store.update(provider_id, body)
        except KeyError as exc:
            raise HTTPException(404, "unknown provider connection") from exc
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
        days: int = 14,
        route: str = "",
        period: str = "day",
        page: int = 1,
        page_size: int = 200,
    ) -> dict[str, object]:
        base = product.usage()
        bucket_key = {"hour": "hourly", "day": "daily", "month": "monthly"}.get(
            period, "daily"
        )
        base[bucket_key] = cost_store.usage_by_period(
            period=period,
            days=max(1, min(days, 90)),
            route=route or None,
            page=page,
            page_size=page_size,
        )
        return base

    # -- Cost attribution (US-001) -----------------------------------------

    @app.get("/v1/product/customers")
    async def product_customers() -> dict[str, object]:
        """List customers with MTD summary and budget status each."""
        customers = []
        for cust in attribution.list_customers():
            entry = {
                "id": cust["id"],
                "name": cust["name"],
                "tenant": cust["tenant"],
                "created_at": cust["created_at"],
                "mtd": {
                    "cost_usd": cust["mtd_cost_usd"],
                    "calls": cust["mtd_calls"],
                    "total_tokens": cust["mtd_total_tokens"],
                    "prompt_tokens": cust["mtd_prompt_tokens"],
                    "completion_tokens": cust["mtd_completion_tokens"],
                },
            }
            budget = attribution.get_budget(cust["id"])
            if budget is not None:
                entry["budget"] = {
                    "monthly_limit_usd": budget["monthly_limit_usd"],
                    "percent_used": budget["percent_used"],
                    "remaining_usd": budget["remaining_usd"],
                }
            else:
                entry["budget"] = None
            customers.append(entry)
        return {"customers": customers, "total_customers": len(customers)}

    @app.post("/v1/product/customers", status_code=201)
    async def product_create_customer(body: dict[str, object]) -> dict[str, object]:
        """Create a customer (409 on duplicate name, 422 on empty name)."""
        name = str(body.get("name", "") or "").strip()
        if not name:
            raise HTTPException(422, "name is required")
        try:
            return attribution.create_customer(name)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/v1/product/customers/{customer_id}")
    async def product_customer_detail(customer_id: str) -> dict[str, object]:
        """Customer detail payload (summary + budget status)."""
        customer = attribution.get_customer(customer_id)
        if customer is None:
            raise HTTPException(404, "unknown customer")
        summary = attribution.mtd_summary(customer_id)
        budget = attribution.get_budget(customer_id)
        return {
            "customer": customer,
            "summary": {
                "mtd_cost_usd": summary.mtd_cost_usd,
                "mtd_calls": summary.mtd_calls,
                "mtd_total_tokens": summary.mtd_total_tokens,
                "mtd_prompt_tokens": summary.mtd_prompt_tokens,
                "mtd_completion_tokens": summary.mtd_completion_tokens,
            },
            "budget": budget,
        }

    @app.put("/v1/product/customers/{customer_id}/budget")
    async def product_set_customer_budget(
        customer_id: str, body: dict[str, object]
    ) -> dict[str, object]:
        """Set a monthly budget for a customer (422 on non-positive limit)."""
        raw = body.get("monthly_limit_usd") or body.get("limit_usd") or 0
        try:
            limit = float(str(raw))
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, "monthly_limit_usd must be positive") from exc
        if limit <= 0:
            raise HTTPException(422, "monthly_limit_usd must be positive")
        if attribution.get_customer(customer_id) is None:
            raise HTTPException(404, "unknown customer")
        return attribution.set_monthly_budget(customer_id, limit)

    @app.get("/v1/product/customers/{customer_id}/daily-spend")
    async def product_customer_daily_spend(
        customer_id: str, days: int = 31, granularity: str = "day"
    ) -> dict[str, object]:
        """Daily/weekly/monthly spend chart data for a customer."""
        if attribution.get_customer(customer_id) is None:
            raise HTTPException(404, "unknown customer")
        if granularity not in ("day", "week", "month"):
            raise HTTPException(422, "granularity must be day|week|month")
        points = attribution.daily_spend(customer_id, days=days, granularity=granularity)
        return {
            "customer_id": customer_id,
            "granularity": granularity,
            "points": [
                {
                    "date": p.date,
                    "cost_usd": p.cost_usd,
                    "calls": p.calls,
                    "total_tokens": p.total_tokens,
                }
                for p in points
            ],
        }

    @app.get("/v1/product/customers/{customer_id}/models")
    async def product_customer_models(customer_id: str) -> dict[str, object]:
        """Breakdown by model (MTD by default) sorted by cost desc."""
        if attribution.get_customer(customer_id) is None:
            raise HTTPException(404, "unknown customer")
        models = attribution.spend_by_model(customer_id)
        return {
            "customer_id": customer_id,
            "since_epoch": int(time.time()),
            "models": [
                {
                    "model": m.model,
                    "cost_usd": m.cost_usd,
                    "calls": m.calls,
                    "total_tokens": m.total_tokens,
                }
                for m in models
            ],
        }

    @app.get("/v1/product/customers/{customer_id}/export.csv")
    async def product_customer_export_csv(customer_id: str) -> PlainTextResponse:
        """CSV ledger export: one row per entry, newest first."""

        def _csv_field(value: str) -> str:
            """Neutralize spreadsheet formula triggers in a CSV cell.

            A leading '=', '+', '-', '@', tab or CR makes Excel/LibreOffice/
            Sheets interpret the cell as a formula on open (spreadsheet
            exfiltration class). Prefix a single apostrophe so the value is
            rendered as literal text; csv.writer quoting applies afterwards.
            """
            if value[:1] in ("=", "+", "-", "@", "\t", "\r"):
                return "'" + value
            return value

        customer = attribution.get_customer(customer_id)
        if customer is None:
            raise HTTPException(404, "unknown customer")
        rows = attribution.ledger_rows(customer_id)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["customer", "timestamp", "model", "tokens", "cost"])
        for row in rows:
            writer.writerow(
                [
                    _csv_field(row.customer),
                    row.timestamp,
                    _csv_field(row.model),
                    row.tokens,
                    f"{row.cost:.6f}",
                ]
            )
        filename = f"{customer['name']}-usage.csv"
        return PlainTextResponse(
            buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/v1/product/routes/{route_id}/status")
    async def product_route_status(route_id: str) -> dict[str, object]:
        try:
            route = product.route(route_id)
        except KeyError as exc:
            raise HTTPException(404, "unknown route") from exc
        models = [str(t.get("model", "")) for t in route.get("targets", [])]
        return {
            "route": route["name"],
            "status": cost_store.route_status(route["name"], models),
        }

    @app.delete("/v1/product/routes/{route_id}/cooldowns")
    async def clear_product_cooldown(
        route_id: str, model: str
    ) -> dict[str, object]:
        try:
            route = product.route(route_id)
        except KeyError as exc:
            raise HTTPException(404, "unknown route") from exc
        cost_store.clear_cooldown(route["name"], model)
        return {"cleared": True, "route": route["name"], "model": model}

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

    # ------------------------------------------------------------------
    # Integrated capabilities (formerly separate satellite services)
    # ------------------------------------------------------------------
    # Intelligence: PII redaction, exact-response cache, anomaly detection
    # and cost-aware routing, exposed from the cockpit product API with the
    # same bearer-token auth as every other product endpoint.
    redactor = PIIRedactor()
    cache = ExactResponseCache(str(repository_root / ".gateway-console" / "intelligence.db"))
    anomaly = UsageAnomalyDetector()
    cost_router = MarketCostAwareRouter()

    @app.get("/v1/product/intelligence/redact")
    async def intelligence_redact(text: str = "") -> dict[str, object]:
        result = redactor.redact(text)
        return {
            "text": result.text,
            "categories": list(result.categories),
            "count": result.count,
        }

    @app.post("/v1/product/intelligence/cache")
    async def intelligence_cache_put(body: dict[str, object]) -> dict[str, object]:
        try:
            key = cache.put(
                "default",
                body.get("request", {}),
                body.get("response"),
                int(body.get("ttl", 0)),
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"key": key, "status": "stored"}

    @app.post("/v1/product/intelligence/cache/lookup")
    async def intelligence_cache_get(body: dict[str, object]) -> dict[str, object]:
        value = cache.get("default", body.get("request", {}))
        if value is None:
            return {"hit": False}
        return {"hit": True, "value": value}

    @app.post("/v1/product/intelligence/anomaly")
    async def intelligence_anomaly(body: dict[str, object]) -> dict[str, object]:
        try:
            return anomaly.detect(
                [float(x) for x in body.get("history", [])],
                float(body.get("current", 0)),
                float(body.get("z_limit", 3.0)),
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/v1/product/intelligence/route")
    async def intelligence_route(body: dict[str, object]) -> dict[str, object]:
        try:
            return cost_router.choose(
                body.get("candidates", []),
                float(body.get("min_quality", 0.0)),
                int(body["max_latency_ms"]) if body.get("max_latency_ms") else None,
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/v1/product/intelligence/cache-stats")
    async def intelligence_cache_stats() -> dict[str, object]:
        """Aggregate cache hit/miss counts from the cost records.
        
        Returns per-model and per-route breakdown plus totals.
        """
        try:
            with cost_store._lock:
                # Totals (last 7 days)
                row = cost_store._conn.execute(
                    "SELECT COUNT(*), SUM(CASE WHEN cache_hit = 1 THEN 1 ELSE 0 END) "
                    "FROM cost_records WHERE timestamp >= ?",
                    (int(time.time()) - 7 * 86400,),
                ).fetchone()
                total = int(row[0] or 0)
                hits = int(row[1] or 0)

                # Per-model breakdown
                models: dict[str, dict[str, int]] = {}
                for r in cost_store._conn.execute(
                    "SELECT model, COUNT(*), SUM(CASE WHEN cache_hit = 1 THEN 1 ELSE 0 END) "
                    "FROM cost_records WHERE timestamp >= ? "
                    "GROUP BY model ORDER BY COUNT(*) DESC LIMIT 20",
                    (int(time.time()) - 7 * 86400,),
                ):
                    m_total = int(r[1] or 0)
                    m_hits = int(r[2] or 0)
                    models[str(r[0])] = {
                        "total": m_total,
                        "hits": m_hits,
                        "misses": m_total - m_hits,
                        "hit_rate": round(m_hits / m_total, 4) if m_total > 0 else 0.0,
                    }

                # Per-route breakdown
                routes: dict[str, dict[str, int]] = {}
                for r in cost_store._conn.execute(
                    "SELECT route, COUNT(*), SUM(CASE WHEN cache_hit = 1 THEN 1 ELSE 0 END) "
                    "FROM cost_records WHERE timestamp >= ? "
                    "GROUP BY route ORDER BY COUNT(*) DESC LIMIT 10",
                    (int(time.time()) - 7 * 86400,),
                ):
                    r_total = int(r[1] or 0)
                    r_hits = int(r[2] or 0)
                    routes[str(r[0] or "none")] = {
                        "total": r_total,
                        "hits": r_hits,
                        "misses": r_total - r_hits,
                        "hit_rate": round(r_hits / r_total, 4) if r_total > 0 else 0.0,
                    }

                # Tokens saved (prompt tokens that would have been sent)
                saved = cost_store._conn.execute(
                    "SELECT SUM(prompt_tokens) FROM cost_records "
                    "WHERE cache_hit = 1 AND timestamp >= ?",
                    (int(time.time()) - 7 * 86400,),
                ).fetchone()
                tokens_saved = int(saved[0] or 0)

                return {
                    "total_requests": total,
                    "cache_hits": hits,
                    "cache_misses": total - hits,
                    "hit_rate": round(hits / total, 4) if total > 0 else 0.0,
                    "tokens_saved": tokens_saved,
                    "by_model": models,
                    "by_route": routes,
                }
        except Exception as exc:
            raise HTTPException(500, str(exc)) from exc

    # Operations: prompt registry with immutable versions + deterministic
    # A/B assignment; quota classification for provider errors.
    prompts = PromptRegistry(str(repository_root / ".gateway-console" / "operations.db"))
    quota = QuotaDiagnostic()

    @app.get("/v1/product/prompts")
    async def prompts_list(name: str = "") -> dict[str, object]:
        if name:
            return {"name": name, "versions": prompts.list("default", name)}
        names = {
            row[0]
            for row in prompts.db.execute(
                "SELECT DISTINCT name FROM prompt_version WHERE tenant='default'"
            )
        }
        return {"names": sorted(names)}

    @app.post("/v1/product/prompts", status_code=201)
    async def prompts_create(body: dict[str, object]) -> dict[str, object]:
        try:
            return prompts.create(
                "default",
                str(body.get("name", "")),
                str(body.get("template", "")),
                body.get("metadata"),
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/v1/product/prompts/assign")
    async def prompts_assign(body: dict[str, object]) -> dict[str, object]:
        try:
            return prompts.assign(
                "default",
                str(body.get("name", "")),
                str(body.get("subject", "")),
                [int(x) for x in body.get("versions", [])],
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/v1/product/quota/classify")
    async def quota_classify(body: dict[str, object]) -> dict[str, object]:
        return quota.classify(
            int(body.get("status_code", 0)),
            str(body.get("code") or ""),
            str(body.get("message") or ""),
        )

    # Quality: rule-based output evaluation, release gates, batch manifests
    # and audit reports, with persisted runs in the cockpit DB.
    evaluator = RuleEvaluator()
    gate = ReleaseGate()
    batches = BatchManifest()
    audits = AuditReport()
    eval_store = EvaluationStore(
        str(repository_root / ".gateway-console" / "evaluations.db")
    )

    @app.post("/v1/product/quality/evaluate")
    async def quality_evaluate(body: dict[str, object]) -> dict[str, object]:
        try:
            result = evaluator.evaluate(
                str(body.get("output", "")), body.get("rules", {})
            )
            record = eval_store.record("default", str(body.get("name", "eval")), result)
            return {**record, "checks": result.checks, "passed": result.passed}
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/v1/product/quality/runs")
    async def quality_runs() -> dict[str, object]:
        return {"runs": eval_store.list("default")}

    @app.post("/v1/product/quality/release-gate")
    async def quality_release_gate(body: dict[str, object]) -> dict[str, object]:
        try:
            return gate.decide(
                [float(x) for x in body.get("scores", [])],
                float(body.get("minimum", 0.8)),
                float(body.get("max_regression", 0.05)),
                float(body["baseline"]) if body.get("baseline") is not None else None,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/v1/product/quality/batch")
    async def quality_batch(body: dict[str, object]) -> dict[str, object]:
        try:
            return batches.build(
                body.get("requests", []), float(body.get("discount", 0.5))
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/v1/product/quality/audit")
    async def quality_audit(body: dict[str, object]) -> dict[str, object]:
        try:
            return audits.create(
                body.get("findings", []), int(body.get("generated_at", 0))
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/v1/product/quality/audit/verify")
    async def quality_audit_verify(body: dict[str, object]) -> dict[str, object]:
        return {"valid": audits.verify(body)}

    # SLO monitoring over the last 24h of recorded requests.
    slo = SLOMonitor()

    @app.get("/v1/product/slo")
    async def product_slo() -> dict[str, object]:
        total = 0
        failed = 0
        try:
            for row in cost_store._conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END),0) "
                "FROM cost_records WHERE timestamp >= ?",
                (int(time.time()) - 86400,),
            ):
                total, failed = int(row[0]), int(row[1])
        except sqlite3.Error:
            total = failed = 0
        if total <= 0:
            return {
                "availability": None,
                "target": float(os.getenv("GATEWAY_SLO_TARGET", "0.99")),
                "burn_rate": None,
                "state": "no_data",
                "remaining_failures": None,
            }
        return slo.evaluate(total, failed, float(os.getenv("GATEWAY_SLO_TARGET", "0.99")))

    return app
