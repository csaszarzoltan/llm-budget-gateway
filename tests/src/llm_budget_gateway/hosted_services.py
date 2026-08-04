"""Homepage-enabled wrappers for every independently hosted FastAPI service."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from html import escape
from importlib import import_module
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse


@dataclass(frozen=True)
class HostedService:
    """Metadata required to construct one service landing page."""

    slug: str
    name: str
    factory: str
    port: int
    description: str
    dashboard: str | None = None


SERVICES = {
    "gateway": HostedService(
        "gateway",
        "Gateway",
        "llm_budget_gateway.main:create_app",
        8000,
        "OpenAI-compatible proxy, models, streaming, embeddings, budgets and cost tracking.",
    ),
    "control": HostedService(
        "control",
        "Control Center",
        "llm_budget_gateway.control_api:create_control_app",
        8001,
        "Workspace setup, keys, budgets, spend, policies, routes and operational activity.",
        "/control",
    ),
    "intelligence": HostedService(
        "intelligence",
        "Intelligence",
        "llm_budget_gateway.market_api:create_market_app",
        8002,
        "PII controls, response caching, signed webhooks, anomaly detection and cost-aware routing.",
    ),
    "operations": HostedService(
        "operations",
        "Operations",
        "llm_budget_gateway.operations_api:create_operations_app",
        8003,
        "Prompt versions, bounded retries, quota diagnostics, model catalog and SLO monitoring.",
        "/operations",
    ),
    "quality": HostedService(
        "quality",
        "Quality",
        "llm_budget_gateway.evaluation_api:create_evaluation_app",
        8004,
        "Evaluations, release gates, trace context, batch planning and audit reports.",
        "/quality",
    ),
    "security": HostedService(
        "security",
        "Security",
        "llm_budget_gateway.security_api:create_security_app",
        8005,
        "Secret scanning, replay protection, provider compliance, change risk and security posture.",
        "/security",
    ),
    "resilience": HostedService(
        "resilience",
        "Resilience",
        "llm_budget_gateway.resilience_api:create_resilience_app",
        8006,
        "Adaptive concurrency, dead letters, maintenance windows, configuration diagnosis and incidents.",
        "/resilience",
    ),
    "optimization": HostedService(
        "optimization",
        "Optimization",
        "llm_budget_gateway.optimization_api:create_optimization_app",
        8007,
        "Prompt compression, savings attribution, cache advice, forecasts and experiments.",
        "/optimization",
    ),
    "collaboration": HostedService(
        "collaboration",
        "Collaboration",
        "llm_budget_gateway.collaboration_api:create_collaboration_app",
        8008,
        "Project roles, invitations, key lifecycle, member budgets and delegated approvals.",
        "/collaboration",
    ),
    "platform": HostedService(
        "platform",
        "Platform",
        "llm_budget_gateway.platform_api:create_platform_app",
        8009,
        "Catalogs, allocation, quotas, SLOs, DLP, routing, releases, quality and adoption.",
        "/platform",
    ),
    "agentops": HostedService(
        "agentops",
        "AgentOps",
        "llm_budget_gateway.agentops_api:create_agentops_app",
        8010,
        "Tool access, leases, replay controls, approvals, audit, cost, safety and residency.",
        "/agentops",
    ),
    "fleet": HostedService(
        "fleet",
        "Fleet Governance",
        "llm_budget_gateway.fleet_api:create_fleet_app",
        8011,
        "Agent identity, lifecycle, authorization, kill switches, economics and compliance.",
        "/fleet",
    ),
    "assurance": HostedService(
        "assurance",
        "Assurance",
        "llm_budget_gateway.assurance_api:create_assurance_app",
        8012,
        "Risk, controls, quality, fairness, evidence, incidents, maturity and benefits.",
        "/assurance",
    ),
    "delivery": HostedService(
        "delivery",
        "Delivery",
        "llm_budget_gateway.delivery_api:create_delivery_app",
        8014,
        "Environment, drift, capacity, health, rollout, rollback, observability and releases.",
    ),
    "scale": HostedService(
        "scale",
        "Scale",
        "llm_budget_gateway.scale_api:create_scale_app",
        8015,
        "Topology, quorum, partitioning, consistency, failover, sharding, residency and recovery.",
    ),
}


def _resolve_factory(path: str) -> Callable[..., FastAPI]:
    module_name, name = path.split(":", 1)
    factory = getattr(import_module(module_name), name)
    if not callable(factory):
        raise TypeError(f"factory is not callable: {path}")
    return factory


def _has_root_route(app: FastAPI) -> bool:
    return any(getattr(route, "path", None) == "/" for route in app.routes)


def _homepage(service: HostedService) -> str:
    actions = []
    if service.dashboard:
        actions.append(
            f"<a class='primary' href='{escape(service.dashboard)}'>Open dashboard</a>"
        )
    actions.extend(
        (
            "<a href='/docs'>OpenAPI documentation</a>",
            "<a href='/openapi.json'>OpenAPI JSON</a>",
            "<a href='/health'>Health check</a>",
            "<a href='http://127.0.0.1:8013/' rel='noopener noreferrer'>Unified Console</a>",
        )
    )
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{escape(service.name)} | LLM Budget Gateway</title><style>:root{{color-scheme:light dark;--bg:#f5f7fb;--card:#fff;--ink:#172033;--muted:#647089;--line:#dce2ec;--brand:#2f5bea}}@media(prefers-color-scheme:dark){{:root{{--bg:#0b1020;--card:#121a2c;--ink:#f4f7ff;--muted:#a7b1c7;--line:#2a3650;--brand:#7d9cff}}}}*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui;padding:24px}}main{{width:min(760px,100%);padding:clamp(24px,6vw,54px);background:var(--card);border:1px solid var(--line);border-radius:24px;box-shadow:0 20px 60px #0002}}.mark{{display:grid;place-items:center;width:52px;height:52px;border-radius:15px;background:var(--brand);color:white;font-weight:800}}.eyebrow{{color:var(--brand);text-transform:uppercase;letter-spacing:.12em;font-size:12px;font-weight:700;margin-top:24px}}h1{{font-size:clamp(34px,7vw,58px);line-height:1.05;margin:.15em 0}}p{{color:var(--muted)}}.status{{display:inline-flex;align-items:center;gap:8px;padding:7px 11px;border-radius:999px;background:color-mix(in srgb,var(--brand) 12%,transparent)}}.status i{{width:9px;height:9px;border-radius:50%;background:#16a06c}}nav{{display:flex;flex-wrap:wrap;gap:10px;margin-top:28px}}a{{padding:10px 14px;border:1px solid var(--line);border-radius:11px;color:var(--ink);text-decoration:none}}a:hover{{border-color:var(--brand);color:var(--brand)}}a.primary{{background:var(--brand);border-color:var(--brand);color:white}}footer{{margin-top:34px;padding-top:18px;border-top:1px solid var(--line);font-size:13px;color:var(--muted)}}:focus-visible{{outline:3px solid var(--brand);outline-offset:3px}}</style></head><body><main><div class='mark'>{escape(service.name[:2].upper())}</div><p class='eyebrow'>LLM Budget Gateway service</p><h1>{escape(service.name)}</h1><p>{escape(service.description)}</p><p class='status'><i></i>Service is running on port {service.port}</p><nav aria-label='Service actions'>{"".join(actions)}</nav><footer>Managed locally by Unified Console 7.3. Existing APIs and dashboards remain unchanged.</footer></main></body></html>"""


def create_hosted_app(slug: str, **factory_kwargs: Any) -> FastAPI:
    """Create the original service and add a root homepage when it has none."""
    try:
        service = SERVICES[slug]
    except KeyError as exc:
        raise ValueError(f"unknown hosted service: {slug}") from exc
    app = _resolve_factory(service.factory)(**factory_kwargs)
    if not isinstance(app, FastAPI):
        raise TypeError(f"factory did not return FastAPI: {service.factory}")
    if not _has_root_route(app):
        page = _homepage(service)

        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        async def service_homepage() -> str:
            return page

    return app


def create_gateway_app() -> FastAPI:
    return create_hosted_app("gateway")


def create_control_app() -> FastAPI:
    return create_hosted_app("control")


def create_intelligence_app() -> FastAPI:
    return create_hosted_app("intelligence")


def create_operations_app() -> FastAPI:
    return create_hosted_app("operations")


def create_quality_app() -> FastAPI:
    return create_hosted_app("quality")


def create_security_app() -> FastAPI:
    return create_hosted_app("security")


def create_resilience_app() -> FastAPI:
    return create_hosted_app("resilience")


def create_optimization_app() -> FastAPI:
    return create_hosted_app("optimization")


def create_collaboration_app() -> FastAPI:
    return create_hosted_app("collaboration")


def create_platform_app() -> FastAPI:
    return create_hosted_app("platform")


def create_agentops_app() -> FastAPI:
    return create_hosted_app("agentops")


def create_fleet_app() -> FastAPI:
    return create_hosted_app("fleet")


def create_assurance_app() -> FastAPI:
    return create_hosted_app("assurance")


def create_delivery_app() -> FastAPI:
    return create_hosted_app("delivery")


def create_scale_app() -> FastAPI:
    return create_hosted_app("scale")
