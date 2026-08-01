"""Unified, accessible browser console for every gateway service and API."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from html import escape

from .console_workflows import step_preset, workflow_catalog


@dataclass(frozen=True)
class Center:
    """A discoverable application surface in the unified console."""

    slug: str
    name: str
    group: str
    port: int
    description: str
    factory: str
    api_prefix: str
    dashboard: str
    auth: str
    capabilities: tuple[str, ...]


CENTERS = (
    Center(
        "gateway",
        "Gateway",
        "Core",
        8000,
        "OpenAI-compatible proxy, models, streaming, embeddings, budgets and cost records.",
        "llm_budget_gateway.main:create_app",
        "/v1",
        "/",
        "Virtual bearer key",
        (
            "chat-completions",
            "completions",
            "embeddings",
            "models",
            "cost-estimates",
            "health",
        ),
    ),
    Center(
        "control",
        "Control Center",
        "Core",
        8001,
        "Workspace setup, spend, keys, budgets, policies, routes, alerts and activity.",
        "llm_budget_gateway.control_api:create_control_app",
        "/v1/admin",
        "/control",
        "Tenant and role headers",
        (
            "workspace",
            "keys",
            "budgets",
            "spend",
            "policies",
            "routes",
            "alerts",
            "activity",
        ),
    ),
    Center(
        "intelligence",
        "Intelligence",
        "FinOps",
        8002,
        "PII redaction, exact cache, signed webhooks, anomalies and cost-aware routing.",
        "llm_budget_gateway.market_api:create_market_app",
        "/v1/intelligence",
        "/intelligence",
        "Tenant header",
        ("redact", "cache", "cache-lookup", "webhooks-sign", "anomalies", "route"),
    ),
    Center(
        "operations",
        "Operations",
        "Operations",
        8003,
        "Prompt versions, bounded retries, quota diagnostics, model catalog and SLO monitoring.",
        "llm_budget_gateway.operations_api:create_operations_app",
        "/v1/operations",
        "/operations",
        "Bearer key and tenant",
        (
            "prompts",
            "prompt-assignment",
            "retry-decisions",
            "quota-diagnostics",
            "model-catalog",
            "slo",
        ),
    ),
    Center(
        "quality",
        "Quality",
        "Quality",
        8004,
        "Evaluations, release gates, trace resolution, batch planning and audit reports.",
        "llm_budget_gateway.evaluation_api:create_evaluation_app",
        "/v1/quality",
        "/quality",
        "Bearer key and tenant",
        (
            "evaluations",
            "release-gates",
            "trace-context",
            "batch-manifests",
            "audit-reports",
        ),
    ),
    Center(
        "security",
        "Security",
        "Security",
        8005,
        "Secret scanning, replay protection, provider compliance, change risk and posture.",
        "llm_budget_gateway.security_api:create_security_app",
        "/v1/security",
        "/security",
        "Bearer key and tenant",
        (
            "secret-scan",
            "replay-protection",
            "provider-compliance",
            "change-risk",
            "posture",
        ),
    ),
    Center(
        "resilience",
        "Resilience",
        "Operations",
        8006,
        "Adaptive concurrency, dead letters, maintenance, config doctor and incidents.",
        "llm_budget_gateway.resilience_api:create_resilience_app",
        "/v1/resilience",
        "/resilience",
        "Bearer key and tenant",
        (
            "concurrency",
            "dead-letters",
            "maintenance",
            "config-doctor",
            "incident-timeline",
        ),
    ),
    Center(
        "optimization",
        "Optimization",
        "FinOps",
        8007,
        "Prompt compression, savings, cache advice, forecasts and experiments.",
        "llm_budget_gateway.optimization_api:create_optimization_app",
        "/v1/optimization",
        "/optimization",
        "Bearer key and tenant",
        (
            "prompt-compression",
            "savings-attribution",
            "cache-policy",
            "budget-forecast",
            "experiments",
        ),
    ),
    Center(
        "collaboration",
        "Collaboration",
        "Administration",
        8008,
        "Project RBAC, invitations, key lifecycle, member budgets and delegation.",
        "llm_budget_gateway.collaboration_api:create_collaboration_app",
        "/v1/collaboration",
        "/collaboration",
        "Bearer key and tenant",
        (
            "members",
            "roles",
            "invitations",
            "key-lifecycle",
            "member-budgets",
            "delegated-approvals",
        ),
    ),
    Center(
        "platform",
        "Platform",
        "Platform",
        8009,
        "Catalog, allocation, quotas, SLOs, DLP, routing, releases, quality and adoption.",
        "llm_budget_gateway.platform_api:create_platform_app",
        "/v1/platform",
        "/platform",
        "Bearer key and tenant",
        (
            "prompt-catalog",
            "model-catalog",
            "usage-tags",
            "cost-allocation",
            "quota-plan",
            "alert-rule",
            "slo",
            "incident-digest",
            "retention",
            "dlp",
            "region-route",
            "provider-score",
            "canary-plan",
            "rollback",
            "feedback",
            "quality-drift",
            "dataset-curate",
            "export-manifest",
            "contract",
            "adoption-funnel",
        ),
    ),
    Center(
        "agentops",
        "AgentOps",
        "Agents",
        8010,
        "Tool registry, access, leases, replay, approvals, audit, cost, safety and residency.",
        "llm_budget_gateway.agentops_api:create_agentops_app",
        "/v1/agentops",
        "/agentops",
        "Bearer key and tenant",
        (
            "mcp-registry",
            "tool-access",
            "delegation-depth",
            "task-lease",
            "replay-protection",
            "session-affinity",
            "circuit-breaker",
            "semantic-cache",
            "redact",
            "injection-risk",
            "human-approval",
            "audit-chain",
            "trace-sampling",
            "task-cost",
            "token-density",
            "carbon",
            "change-risk",
            "support-triage",
            "locale",
            "residency",
        ),
    ),
    Center(
        "fleet",
        "Fleet Governance",
        "Agents",
        8011,
        "Agent identity, lifecycle, authorization, kill switches, economics and compliance.",
        "llm_budget_gateway.fleet_api:create_fleet_app",
        "/v1/fleet",
        "/fleet",
        "Bearer key and tenant",
        (
            "identity",
            "inventory",
            "lifecycle",
            "credential-expiry",
            "capability-grant",
            "platform-authorization",
            "kill-switch",
            "policy-simulation",
            "blast-radius",
            "responsibility",
            "evidence",
            "policy-coverage",
            "shadow-agents",
            "cost-ceiling",
            "runaway",
            "outcome-economics",
            "model-tier",
            "tool-costs",
            "data-readiness",
            "reproducibility",
            "compliance",
        ),
    ),
    Center(
        "assurance",
        "Assurance",
        "Governance",
        8012,
        "Risk, controls, quality, fairness, evidence, incidents, maturity and benefits.",
        "llm_budget_gateway.assurance_api:create_assurance_app",
        "/v1/assurance",
        "/assurance",
        "Bearer key and tenant",
        (
            "risk-tier",
            "control-test",
            "evaluation-gate",
            "calibration",
            "refusal-quality",
            "fairness-gap",
            "robustness",
            "hallucination-rate",
            "provenance",
            "change-approval",
            "incident-severity",
            "corrective-action",
            "vendor-risk",
            "data-quality",
            "drift-alert",
            "red-team-coverage",
            "evidence-freshness",
            "maturity",
            "assurance-report",
            "benefit-realization",
        ),
    ),
    Center(
        "delivery",
        "Delivery",
        "Delivery",
        8014,
        "Environment, drift, capacity, health, rollout, rollback, observability and releases.",
        "llm_budget_gateway.delivery_api:create_delivery_app",
        "/v1/delivery",
        "/docs",
        "Bearer key and tenant",
        (
            "environment-readiness",
            "configuration-drift",
            "capacity-plan",
            "dependency-health",
            "rollout-plan",
            "rollback-decision",
            "observability-coverage",
            "alert-routes",
            "runbook-coverage",
            "release-manifest",
        ),
    ),
    Center(
        "scale",
        "Scale",
        "Delivery",
        8015,
        "Topology, quorum, partitioning, consistency, failover, sharding, residency and recovery.",
        "llm_budget_gateway.scale_api:create_scale_app",
        "/v1/scale",
        "/docs",
        "Bearer key and tenant",
        (
            "storage-topology",
            "replication-quorum",
            "partition-plan",
            "consistency-policy",
            "failover-plan",
            "migration-readiness",
            "connection-pool",
            "tenant-shard",
            "residency-topology",
            "disaster-recovery",
        ),
    ),
)


def catalog() -> list[dict[str, object]]:
    """Return JSON-safe metadata for all console centers."""
    return [asdict(center) for center in CENTERS]


def render_console() -> str:
    """Render the complete dependency-free console application."""
    cards = "".join(
        f"<article class='center-card' data-center='{escape(c.slug)}' data-group='{escape(c.group)}' data-search='{escape((c.name + ' ' + c.description + ' ' + ' '.join(c.capabilities)).lower())}'>"
        f"<div class='card-top'><span class='icon' aria-hidden='true'>{escape(c.name[:2].upper())}</span><span class='status' data-port='{c.port}'><i></i>Not checked</span></div>"
        f"<p class='eyebrow'>{escape(c.group)}</p><h3>{escape(c.name)}</h3><p>{escape(c.description)}</p>"
        f"<div class='chips'>{''.join(f'<button class=chip data-action=run data-center={escape(c.slug)} data-capability={escape(x)}>{escape(x)}</button>' for x in c.capabilities[:6])}"
        f"<span class='more'>+{max(0, len(c.capabilities) - 6)} more</span></div>"
        f"<div class='card-actions'><button class='button primary' data-action='open-center' data-center='{escape(c.slug)}'>Open workspace</button>"
        f"<a class='button ghost' href='http://localhost:{c.port}/docs' target='_blank' rel='noreferrer'>API docs</a></div></article>"
        for c in CENTERS
    )
    workflows = workflow_catalog()
    workflow_json = json.dumps(workflows, separators=(",", ":"))
    preset_json = json.dumps(
        {
            f"{center}:{capability}": step_preset(center, capability)
            for workflow in workflows
            for center, capability in workflow["steps"]
        },
        separators=(",", ":"),
    )
    workflow_cards = "".join(
        f"<article class='workflow-card' data-workflow='{escape(str(w['id']))}'><div><p class='eyebrow'>{escape(' · '.join(w['roles']))}</p><h3>{escape(str(w['title']))}</h3><p>{escape(str(w['summary']))}</p></div><div class='card-actions'><button class='button primary' data-workflow-start='{escape(str(w['id']))}'>Start</button><button class='button ghost' aria-label='Favorite task: {escape(str(w['title']))}' data-favorite='{escape(str(w['id']))}'>☆ Favorite task</button></div></article>"
        for w in workflows
    )
    center_options = "".join(
        f"<option value='{escape(c.slug)}'>{escape(c.name)} :{c.port}</option>"
        for c in CENTERS
    )
    return rf"""<!doctype html>
<html lang='en' data-theme='light'>
<head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>LLM Budget Gateway Console</title>
<style>
:root{{--bg:#f5f7fb;--panel:#fff;--panel2:#eef2f8;--text:#172033;--muted:#647089;--line:#dce2ec;--accent:#2f5bea;--accent2:#193ca8;--good:#16845b;--warn:#ad6300;--bad:#c43d4b;--shadow:0 14px 38px #25345b18;--radius:18px}}
[data-theme=dark]{{--bg:#0b1020;--panel:#121a2c;--panel2:#1a2439;--text:#f4f7ff;--muted:#a7b1c7;--line:#2a3650;--accent:#7d9cff;--accent2:#a8bcff;--good:#54d49b;--warn:#ffbd5c;--bad:#ff7f8b;--shadow:0 18px 45px #0006;color-scheme:dark}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,sans-serif}}button,input,select,textarea{{font:inherit}}button,a{{-webkit-tap-highlight-color:transparent}}:focus-visible{{outline:3px solid #7da0ff;outline-offset:2px}}.skip{{position:fixed;left:-999px;top:8px;z-index:50;background:var(--panel);padding:10px 14px;border-radius:10px}}.skip:focus{{left:8px}}
.shell{{display:grid;grid-template-columns:260px minmax(0,1fr);min-height:100vh}}.sidebar{{position:sticky;top:0;height:100vh;padding:20px 14px;background:var(--panel);border-right:1px solid var(--line);overflow:auto}}.brand{{display:flex;gap:11px;align-items:center;padding:4px 8px 20px}}.logo{{display:grid;place-items:center;width:38px;height:38px;border-radius:12px;background:linear-gradient(140deg,var(--accent),#823cf2);color:white;font-weight:800}}.brand strong{{display:block}}.brand small{{color:var(--muted)}}.nav-label{{margin:20px 10px 7px;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.12em}}.nav button{{display:flex;align-items:center;gap:10px;width:100%;padding:10px 12px;border:0;border-radius:11px;background:transparent;color:var(--text);cursor:pointer;text-align:left}}.nav button:hover,.nav button.active{{background:var(--panel2);color:var(--accent2)}}.nav b{{margin-left:auto;background:var(--panel2);padding:1px 7px;border-radius:999px;font-size:11px}}.sidebar-footer{{margin-top:24px;padding:12px;background:var(--panel2);border-radius:14px;color:var(--muted);font-size:12px}}
.main{{min-width:0}}.topbar{{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:12px;padding:14px clamp(16px,3vw,36px);background:color-mix(in srgb,var(--bg) 90%,transparent);backdrop-filter:blur(16px);border-bottom:1px solid var(--line)}}.menu{{display:none}}.search{{position:relative;flex:1;max-width:720px}}.search input{{width:100%;padding:11px 42px 11px 42px;border:1px solid var(--line);border-radius:13px;background:var(--panel);color:var(--text)}}.search span{{position:absolute;left:14px;top:10px;color:var(--muted)}}.kbd{{position:absolute;right:10px;top:8px;padding:2px 7px;border:1px solid var(--line);border-radius:6px;color:var(--muted);font-size:11px}}.icon-button{{width:42px;height:42px;border:1px solid var(--line);border-radius:12px;background:var(--panel);color:var(--text);cursor:pointer}}.avatar{{display:grid;place-items:center;background:var(--accent);color:white;font-weight:700}}
.content{{padding:clamp(20px,3vw,38px);max-width:1600px;margin:auto}}.hero{{display:grid;grid-template-columns:1.5fr .7fr;gap:20px;align-items:stretch;margin-bottom:24px}}.hero-main,.health-card{{padding:clamp(22px,4vw,38px);border:1px solid var(--line);border-radius:24px;background:var(--panel);box-shadow:var(--shadow)}}.eyebrow{{margin:0 0 6px;color:var(--accent);font-weight:700;font-size:12px;text-transform:uppercase;letter-spacing:.1em}}h1{{margin:.15em 0;font-size:clamp(30px,5vw,52px);line-height:1.05;letter-spacing:-.035em}}h2{{margin:0;font-size:24px}}h3{{margin:5px 0 8px;font-size:19px}}p{{color:var(--muted)}}.hero-actions,.card-actions,.toolbar{{display:flex;gap:10px;flex-wrap:wrap}}.button{{display:inline-flex;justify-content:center;align-items:center;min-height:42px;padding:9px 14px;border:1px solid var(--line);border-radius:11px;background:var(--panel);color:var(--text);text-decoration:none;cursor:pointer}}.button.primary{{border-color:var(--accent);background:var(--accent);color:white}}.button.primary:hover{{background:var(--accent2)}}.button.ghost:hover{{background:var(--panel2)}}.health-card strong{{font-size:34px}}.health-row{{display:flex;justify-content:space-between;margin-top:15px;color:var(--muted)}}
.workflow-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-bottom:28px}}.workflow-card{{display:flex;flex-direction:column;justify-content:space-between;min-height:220px;padding:18px;border:1px solid var(--line);border-radius:16px;background:var(--panel)}}.workflow-card.favorite{{border-color:var(--accent);box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 20%,transparent)}}.recent-list{{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 22px}}.workflow-guide{{display:none;margin:16px 0;padding:14px;border:1px solid var(--line);border-radius:14px;background:var(--panel2)}}.workflow-guide.open{{display:block}}.workflow-steps{{display:grid;gap:7px;margin:10px 0}}.workflow-step{{padding:9px;border-left:4px solid var(--line);background:var(--panel)}}.workflow-step.current{{border-color:var(--accent)}}.field-error{{display:none;margin:6px 0 0;color:var(--bad);font-size:12px}}.field-error.visible{{display:block}}.invalid{{border-color:var(--bad)!important}}.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:28px}}.metric{{padding:17px;border:1px solid var(--line);border-radius:16px;background:var(--panel)}}.metric span{{color:var(--muted)}}.metric strong{{display:block;font-size:25px;margin-top:3px}}.section-head{{display:flex;justify-content:space-between;align-items:end;gap:14px;margin:28px 0 14px}}.filters{{display:flex;gap:7px;flex-wrap:wrap}}.filter{{border:1px solid var(--line);border-radius:999px;padding:7px 11px;background:var(--panel);color:var(--text);cursor:pointer}}.filter.active{{background:var(--text);color:var(--bg)}}.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}}.center-card{{display:flex;flex-direction:column;min-height:320px;padding:20px;border:1px solid var(--line);border-radius:19px;background:var(--panel);box-shadow:0 8px 22px #24365b0c}}.center-card:hover{{transform:translateY(-2px);box-shadow:var(--shadow);transition:.18s}}.card-top{{display:flex;justify-content:space-between;align-items:center}}.icon{{display:grid;place-items:center;width:42px;height:42px;border-radius:13px;background:var(--panel2);color:var(--accent2);font-weight:800}}.status{{font-size:12px;color:var(--muted)}}.status i{{display:inline-block;width:8px;height:8px;margin-right:6px;border-radius:50%;background:#9aa4b5}}.status.ok i{{background:var(--good)}}.status.bad i{{background:var(--bad)}}.chips{{display:flex;gap:6px;flex-wrap:wrap;margin:4px 0 18px}}.chip{{padding:5px 8px;border:0;border-radius:8px;background:var(--panel2);color:var(--muted);font-size:11px;cursor:pointer}}.chip:hover{{color:var(--accent2)}}.more{{padding:5px 2px;color:var(--muted);font-size:11px}}.card-actions{{margin-top:auto}}
.drawer{{position:fixed;inset:0 0 0 auto;z-index:40;width:min(620px,100%);padding:22px;background:var(--panel);border-left:1px solid var(--line);box-shadow:-24px 0 60px #0003;transform:translateX(105%);transition:.24s;overflow:auto}}.drawer.open{{transform:none}}.drawer-head{{display:flex;justify-content:space-between;gap:12px;align-items:start}}.drawer-close{{border:0;background:transparent;color:var(--text);font-size:25px;cursor:pointer}}.form-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}label{{display:block;color:var(--muted);font-size:12px}}input,select,textarea{{width:100%;margin-top:5px;padding:10px;border:1px solid var(--line);border-radius:10px;background:var(--bg);color:var(--text)}}textarea{{min-height:230px;font-family:ui-monospace,SFMono-Regular,monospace;font-size:13px;resize:vertical}}.result{{min-height:160px;padding:14px;white-space:pre-wrap;overflow:auto;border-radius:12px;background:#09101f;color:#d9e6ff;font:12px/1.55 ui-monospace,monospace}}.notice{{padding:11px 13px;border-left:4px solid var(--warn);background:var(--panel2);color:var(--muted);border-radius:8px}}.command{{position:fixed;inset:0;z-index:60;display:none;place-items:start center;padding-top:12vh;background:#07102088;backdrop-filter:blur(4px)}}.command.open{{display:grid}}.command-box{{width:min(680px,92vw);max-height:70vh;padding:12px;background:var(--panel);border:1px solid var(--line);border-radius:18px;box-shadow:0 30px 80px #0006;overflow:auto}}.command-box input{{margin:0 0 8px}}.command-item{{display:block;width:100%;padding:11px;border:0;border-radius:10px;background:transparent;color:var(--text);text-align:left;cursor:pointer}}.command-item:hover{{background:var(--panel2)}}.empty{{display:none;padding:40px;text-align:center;color:var(--muted)}}.toast{{position:fixed;right:20px;bottom:20px;z-index:80;padding:12px 16px;border-radius:12px;background:var(--text);color:var(--bg);box-shadow:var(--shadow)}}
@media(max-width:1100px){{.grid,.workflow-grid{{grid-template-columns:repeat(2,1fr)}}.hero{{grid-template-columns:1fr}}.metrics{{grid-template-columns:repeat(2,1fr)}}}}@media(max-width:760px){{.shell{{grid-template-columns:1fr}}.sidebar{{position:fixed;z-index:50;transform:translateX(-105%);transition:.2s;width:280px}}.sidebar.open{{transform:none}}.menu{{display:block}}.grid,.workflow-grid{{grid-template-columns:1fr}}.metrics{{grid-template-columns:1fr 1fr}}.topbar{{padding:10px}}.kbd{{display:none}}.form-grid{{grid-template-columns:1fr}}}}@media(max-width:460px){{.metrics{{grid-template-columns:1fr}}.hero-actions .button,.card-actions .button{{flex:1}}}}@media(prefers-reduced-motion:reduce){{*{{scroll-behavior:auto!important;transition:none!important;animation:none!important}}}}
</style></head>
<body><a href='#main' class='skip'>Skip to content</a>
<div class='shell'><aside class='sidebar' id='sidebar'><div class='brand'><div class='logo'>LG</div><div><strong>Gateway Console</strong><small>Platform 9.1</small></div></div><nav class='nav' aria-label='Primary'><button class='active' data-nav='overview'>Overview</button><button data-nav='all'>All workspaces <b>{len(CENTERS)}</b></button><button data-nav='Core'>Core</button><button data-nav='FinOps'>FinOps</button><button data-nav='Operations'>Operations</button><button data-nav='Quality'>Quality</button><button data-nav='Security'>Security</button><button data-nav='Administration'>Administration</button><button data-nav='Platform'>Platform</button><button data-nav='Agents'>Agents</button><button data-nav='Governance'>Governance</button><button data-nav='Delivery'>Delivery</button></nav><p class='nav-label'>Tools</p><nav class='nav'><button id='openRunner'>Universal API runner</button><button id='checkAll'>Check service health</button><button id='openCommand'>Command palette</button></nav><div class='sidebar-footer'><strong>Privacy by default</strong><br>Secrets stay in browser session storage and are sent only to the selected local service.</div></aside>
<div class='main'><header class='topbar'><button class='icon-button menu' id='menu' aria-label='Open navigation'>☰</button><div class='search'><span>⌕</span><input id='search' type='search' placeholder='Search every workspace and capability' aria-label='Search'><kbd class='kbd'>⌘ K</kbd></div><button class='icon-button' id='theme' aria-label='Toggle color theme'>◐</button><button class='icon-button avatar' aria-label='User profile'>OP</button></header>
<main class='content' id='main'><section class='hero'><div class='hero-main'><p class='eyebrow'>Unified operations experience</p><h1>One console for every gateway capability.</h1><p>Discover, configure and invoke the complete data plane, FinOps, security, quality, agent, delivery and scale portfolio without switching mental models.</p><div class='hero-actions'><button class='button primary' id='heroRunner'>Run a capability</button><button class='button ghost' id='heroHealth'>Check platform health</button></div></div><aside class='health-card'><p class='eyebrow'>Platform readiness</p><strong id='healthScore'>Not checked</strong><p id='healthText'>Run a health check to inspect all local services.</p><div class='health-row'><span>Services</span><b>{len(CENTERS)}</b></div><div class='health-row'><span>Capabilities</span><b>{sum(len(c.capabilities) for c in CENTERS)}</b></div></aside></section>
<section aria-labelledby='workflowsTitle'><div class='section-head'><div><p class='eyebrow'>Daily workflows</p><h2 id='workflowsTitle'>Start with the job, not the service</h2></div></div><div class='workflow-grid' id='workflowGrid'>{workflow_cards}</div><div><p class='eyebrow'>Recent tasks</p><div class='recent-list' id='recentTasks' aria-live='polite'><span class='notice'>No recent tasks in this browser session.</span></div></div></section>
<section class='metrics' aria-label='Platform summary'><div class='metric'><span>Workspaces</span><strong>{len(CENTERS)}</strong></div><div class='metric'><span>Capabilities</span><strong>{sum(len(c.capabilities) for c in CENTERS)}</strong></div><div class='metric'><span>Validated tests</span><strong>395+</strong></div><div class='metric'><span>Current package</span><strong>9.1.0</strong></div></section>
<section><div class='section-head'><div><p class='eyebrow'>Workspace catalog</p><h2>Everything your platform needs</h2></div><div class='filters' id='filters'><button class='filter active' data-group='all'>All</button>{"".join(f"<button class='filter' data-group='{escape(g)}'>{escape(g)}</button>" for g in sorted({c.group for c in CENTERS}))}</div></div><div class='grid' id='grid'>{cards}</div><div class='empty' id='empty'>No capability matches your search.</div></section></main></div></div>
<aside class='drawer' id='drawer' aria-label='Universal API runner' aria-hidden='true'><div class='drawer-head'><div><p class='eyebrow'>Universal API runner</p><h2 id='runnerTitle'>Invoke any capability</h2></div><button class='drawer-close' id='drawerClose' aria-label='Close'>×</button></div><section class='workflow-guide' id='workflowGuide' aria-labelledby='workflowGuideTitle'><p class='eyebrow'>Guided workflow</p><h3 id='workflowGuideTitle'>Workflow</h3><p id='workflowProgress' aria-live='polite'></p><p id='stepHelp'></p><p class='notice' id='presetNotice'>Review and replace example values before sending.</p><div class='workflow-steps' id='workflowSteps'></div><div class='toolbar'><button class='button' id='workflowPrevious'>Previous step</button><button class='button primary' id='workflowNext'>Next step</button></div></section><p class='notice'>API keys are stored in session storage only. Confirm that the selected service allows browser requests from this console origin.</p><div class='form-grid'><label>Workspace<select id='runnerCenter'>{center_options}</select></label><label>Capability or custom path<input id='runnerCapability' placeholder='capability-name'></label><label>Method<select id='runnerMethod'><option>POST</option><option>GET</option><option>PUT</option><option>DELETE</option></select></label><label>Tenant ID<input id='runnerTenant' value='tenant-local'></label><label>Base URL<input id='runnerBase'></label><label>Bearer key<input id='runnerKey' type='password' autocomplete='off' placeholder='Session only'></label></div><label>JSON request body<textarea id='runnerBody' spellcheck='false' aria-describedby='runnerBodyError'>{{}}</textarea><span class='field-error' role='alert' id='runnerBodyError'>Enter a valid JSON object before sending.</span></label><div class='toolbar'><button class='button primary' id='sendRequest'>Send request</button><button class='button' id='copyCurl'>Copy cURL</button><button class='button' id='openDocs'>Open API docs</button></div><h3>Result</h3><pre class='result' id='result' aria-live='polite'>Waiting for a request.</pre></aside>
<div class='command' id='command' role='dialog' aria-modal='true' aria-label='Command palette'><div class='command-box'><input id='commandSearch' placeholder='Type a workspace or capability' aria-label='Command search'><div id='commandResults'></div></div></div><div class='toast' id='toast' hidden role='status' aria-live='polite'></div>
<script>
const workflows={workflow_json};
const stepPresets={preset_json};
const centers={{{",".join(f'"{c.slug}":{{name:"{escape(c.name)}",port:{c.port},prefix:"{c.api_prefix}",dashboard:"{c.dashboard}",caps:{list(c.capabilities)!r}}}' for c in CENTERS)}}};
let activeGroup='all'; const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const contextKey='gateway-console-context',recentKey='gateway-console-recents',favoriteKey='gateway-console-favorites';
function storedJSON(key,fallback){{try{{return JSON.parse(localStorage.getItem(key))??fallback}}catch{{return JSON.parse(fallback)}}}}
function rememberContext(){{localStorage.setItem(contextKey,JSON.stringify({{tenant:$('#runnerTenant').value,center:$('#runnerCenter').value}}))}}
function addRecent(slug,cap){{const next=[{{slug,cap,at:Date.now()}},...storedJSON(recentKey,'[]').filter(x=>x.slug!==slug||x.cap!==cap)].slice(0,5);localStorage.setItem(recentKey,JSON.stringify(next));restoreRecentTasks()}}
function restoreRecentTasks(){{const root=$('#recentTasks'),items=storedJSON(recentKey,'[]');root.innerHTML=items.length?'':`<span class='notice'>No recent tasks in this browser session.</span>`;items.forEach(x=>{{if(!centers[x.slug])return;const b=document.createElement('button');b.className='button';b.textContent=`${{centers[x.slug].name}} · ${{x.cap||'workspace'}}`;b.onclick=()=>openRunner(x.slug,x.cap);root.appendChild(b)}})}}
function restoreFavorites(){{const favorites=new Set(storedJSON(favoriteKey,'[]'));$$('[data-favorite]').forEach(b=>{{const on=favorites.has(b.dataset.favorite);b.textContent=on?'★ Favorited':'☆ Favorite task';b.closest('.workflow-card').classList.toggle('favorite',on);b.setAttribute('aria-pressed',String(on))}})}}
function validateRequestBody(){{const area=$('#runnerBody'),error=$('#runnerBodyError');try{{const value=JSON.parse(area.value||'{{}}');if(value===null||Array.isArray(value)||typeof value!=='object')throw Error();area.classList.remove('invalid');error.classList.remove('visible');return value}}catch{{area.classList.add('invalid');error.classList.add('visible');area.focus();return null}}}}
function toast(text){{const t=$('#toast');t.textContent=text;t.hidden=false;clearTimeout(t.timer);t.timer=setTimeout(()=>t.hidden=true,2200)}}
function filterCards(){{const q=$('#search').value.toLowerCase().trim();let visible=0;$$('.center-card').forEach(card=>{{const okGroup=activeGroup==='all'||card.dataset.group===activeGroup;const okSearch=!q||card.dataset.search.includes(q);card.hidden=!(okGroup&&okSearch);if(!card.hidden)visible++}});$('#empty').style.display=visible?'none':'block'}}
$('#search').addEventListener('input',filterCards);$$('.filter').forEach(b=>b.onclick=()=>{{$$('.filter').forEach(x=>x.classList.remove('active'));b.classList.add('active');activeGroup=b.dataset.group;filterCards()}});$$('[data-nav]').forEach(b=>b.onclick=()=>{{$$('[data-nav]').forEach(x=>x.classList.remove('active'));b.classList.add('active');activeGroup=['overview','all'].includes(b.dataset.nav)?'all':b.dataset.nav;$$('.filter').forEach(x=>x.classList.toggle('active',x.dataset.group===activeGroup));filterCards();$('#main').scrollIntoView();$('#sidebar').classList.remove('open')}});
function updateRunner(slug,cap=''){{const c=centers[slug];$('#runnerCenter').value=slug;$('#runnerCapability').value=cap;$('#runnerBase').value=`http://localhost:${{c.port}}`;$('#runnerTitle').textContent=cap?`${{c.name}} · ${{cap}}`:c.name;const keyName='key:'+slug;$('#runnerKey').value=sessionStorage.getItem(keyName)||''}}
let activeWorkflow=null,activeWorkflowIndex=0;
function applyStepPreset(step){{const preset=stepPresets[`${{step[0]}}:${{step[1]}}`];if(!preset)return;$('#runnerBody').value=JSON.stringify(preset.body,null,2);$('#stepHelp').textContent=preset.description;$('#presetNotice').textContent=preset.notice;$('#runnerBody').classList.remove('invalid');$('#runnerBodyError').classList.remove('visible');toast('Example input loaded')}}
function openWorkflowStep(index){{if(!activeWorkflow)return;activeWorkflowIndex=Math.max(0,Math.min(index,activeWorkflow.steps.length-1));const step=activeWorkflow.steps[activeWorkflowIndex];updateRunner(step[0],step[1]);applyStepPreset(step);$('#workflowProgress').textContent=`Step ${{activeWorkflowIndex+1}} of ${{activeWorkflow.steps.length}}`;$('#workflowSteps').innerHTML=activeWorkflow.steps.map((x,i)=>`<div class='workflow-step ${{i===activeWorkflowIndex?'current':''}}'><strong>${{i+1}}. ${{centers[x[0]].name}}</strong><br><small>${{x[1]}}</small></div>`).join('');$('#workflowPrevious').disabled=activeWorkflowIndex===0;$('#workflowNext').disabled=activeWorkflowIndex===activeWorkflow.steps.length-1;addRecent(step[0],step[1]);rememberContext()}}
function startWorkflow(id){{activeWorkflow=workflows.find(x=>x.id===id);if(!activeWorkflow)return;$('#workflowGuide').classList.add('open');$('#workflowGuideTitle').textContent=activeWorkflow.title;openRunner(activeWorkflow.steps[0][0],activeWorkflow.steps[0][1]);openWorkflowStep(0)}}
function openRunner(slug='gateway',cap=''){{updateRunner(slug,cap);addRecent(slug,cap);rememberContext();$('#drawer').classList.add('open');$('#drawer').setAttribute('aria-hidden','false');setTimeout(()=>$('#runnerCapability').focus(),50)}}
function closeRunner(){{$('#drawer').classList.remove('open');$('#drawer').setAttribute('aria-hidden','true')}}
$('#runnerCenter').onchange=e=>{{updateRunner(e.target.value,'');rememberContext()}};const runnerTenant=$('#runnerTenant');runnerTenant.addEventListener('input',rememberContext);$('#drawerClose').onclick=closeRunner;$('#openRunner').onclick=()=>openRunner();$('#heroRunner').onclick=()=>openRunner();$$('[data-action=run]').forEach(b=>b.onclick=()=>openRunner(b.dataset.center,b.dataset.capability));$$('[data-action=open-center]').forEach(b=>b.onclick=()=>{{const c=centers[b.dataset.center];window.open(`http://localhost:${{c.port}}${{c.dashboard}}`,'_blank')}});
function endpoint(){{const c=centers[$('#runnerCenter').value],cap=$('#runnerCapability').value.trim(),base=$('#runnerBase').value.replace(/\/$/,'');if(cap.startsWith('/'))return base+cap;if($('#runnerCenter').value==='gateway'){{const map={{'chat-completions':'/v1/chat/completions','completions':'/v1/completions','embeddings':'/v1/embeddings','models':'/v1/models','cost-estimates':'/v1/cost-estimates','health':'/health'}};return base+(map[cap]||'/v1/'+cap)}}return `${{base}}${{c.prefix}}/${{cap}}`}}
function headers(){{const h={{'Content-Type':'application/json'}},key=$('#runnerKey').value,tenant=$('#runnerTenant').value;if(key)h.Authorization='Bearer '+key;if(tenant)h['X-Tenant-Id']=tenant;return h}}
$('#sendRequest').onclick=async()=>{{const method=$('#runnerMethod').value,url=endpoint(),opts={{method,headers:headers()}};const parsed=validateRequestBody();if(!['GET','HEAD'].includes(method)&&parsed===null)return;rememberContext();sessionStorage.setItem('key:'+$('#runnerCenter').value,$('#runnerKey').value);try{{if(!['GET','HEAD'].includes(method))opts.body=JSON.stringify(parsed);$('#result').textContent='Sending '+method+' '+url+' …';const r=await fetch(url,opts);const text=await r.text();let body=text;try{{body=JSON.stringify(JSON.parse(text),null,2)}}catch{{}}$('#result').textContent=`HTTP ${{r.status}} ${{r.statusText}}\n\n${{body}}`}}catch(e){{$('#result').textContent='Request failed\n\n'+e.message+'\n\nCheck that the service is running and permits this console origin.'}}}};
$('#copyCurl').onclick=async()=>{{const h=headers();let cmd=`curl -s -X ${{$('#runnerMethod').value}} '${{endpoint()}}'`;Object.entries(h).forEach(([k,v])=>cmd+=` \\\n  -H '${{k}}: ${{v.replaceAll("'","'\\''")}}'`);if(!['GET','HEAD'].includes($('#runnerMethod').value))cmd+=` \\\n  -d '${{$('#runnerBody').value.replaceAll("'","'\\''")}}'`;await navigator.clipboard.writeText(cmd);toast('cURL copied')}};$('#openDocs').onclick=()=>window.open($('#runnerBase').value.replace(/\/$/,'')+'/docs','_blank');
async function checkAll(){{let ok=0;$('#healthScore').textContent='Checking…';await Promise.all($$('.status').map(async node=>{{try{{const r=await fetch(`http://localhost:${{node.dataset.port}}/health`,{{signal:AbortSignal.timeout(1500)}});if(r.ok){{ok++;node.className='status ok';node.innerHTML='<i></i>Healthy'}}else throw Error()}}catch{{node.className='status bad';node.innerHTML='<i></i>Unavailable'}}}}));$('#healthScore').textContent=`${{ok}} / {len(CENTERS)} healthy`;$('#healthText').textContent=ok===0?'No local service answered. Start the required app factories first.':ok==={len(CENTERS)}?'Every service is reachable.':'Some services are optional or not running.'}}$('#checkAll').onclick=checkAll;$('#heroHealth').onclick=checkAll;
function commandItems(q=''){{const needle=q.toLowerCase(),items=[];Object.entries(centers).forEach(([slug,c])=>{{items.push({{text:c.name,sub:'Open workspace',run:()=>openRunner(slug)}});c.caps.forEach(cap=>items.push({{text:cap,sub:c.name,run:()=>openRunner(slug,cap)}}))}});return items.filter(x=>(x.text+' '+x.sub).toLowerCase().includes(needle)).slice(0,40)}}function renderCommands(){{const box=$('#commandResults');box.innerHTML='';commandItems($('#commandSearch').value).forEach(x=>{{const b=document.createElement('button');b.className='command-item';b.innerHTML=`<strong>${{x.text}}</strong><br><small>${{x.sub}}</small>`;b.onclick=()=>{{x.run();closeCommand()}};box.appendChild(b)}})}}function openCommand(){{$('#command').classList.add('open');$('#commandSearch').value='';renderCommands();setTimeout(()=>$('#commandSearch').focus(),20)}}function closeCommand(){{$('#command').classList.remove('open')}}$('#openCommand').onclick=openCommand;$('#commandSearch').oninput=renderCommands;$('#command').onclick=e=>{{if(e.target===$('#command'))closeCommand()}};
$$('[data-workflow-start]').forEach(b=>b.onclick=()=>startWorkflow(b.dataset.workflowStart));$('#workflowPrevious').onclick=()=>openWorkflowStep(activeWorkflowIndex-1);$('#workflowNext').onclick=()=>openWorkflowStep(activeWorkflowIndex+1);$$('[data-favorite]').forEach(b=>b.onclick=()=>{{const items=new Set(storedJSON(favoriteKey,'[]'));items.has(b.dataset.favorite)?items.delete(b.dataset.favorite):items.add(b.dataset.favorite);localStorage.setItem(favoriteKey,JSON.stringify([...items]));restoreFavorites()}});const savedContext=storedJSON(contextKey,'{{}}');if(savedContext.tenant)$('#runnerTenant').value=savedContext.tenant;restoreRecentTasks();restoreFavorites();
$('#theme').onclick=()=>{{const r=document.documentElement;r.dataset.theme=r.dataset.theme==='dark'?'light':'dark';localStorage.setItem('gateway-theme',r.dataset.theme)}};document.documentElement.dataset.theme=localStorage.getItem('gateway-theme')||'light';$('#menu').onclick=()=>$('#sidebar').classList.toggle('open');document.addEventListener('keydown',e=>{{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){{e.preventDefault();openCommand()}}if(e.key==='Escape'){{closeCommand();closeRunner()}}}});
</script></body></html>"""
