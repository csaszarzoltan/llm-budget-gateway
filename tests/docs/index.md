# LLM Budget Gateway — Documentation

OpenAI-compatible LLM gateway with budget enforcement (sync TPM/RPM
ceilings, soft/hard dollar limits, hierarchical scopes) and automatic
model fallback. Providers are reached through the LiteLLM SDK — the
gateway owns the enforcement, not the provider connectivity.

| Guide | What it covers |
|---|---|
| [Getting Started](getting-started.md) | Install, configure, run, first request |
| [Proxy Setup](proxy-setup.md) | Endpoints, environment config, HTTP semantics, security |
| [Preflight Cost Estimation](cost-estimation.md) | Predict request cost before dispatch |
| [Cost Tracking](cost-tracking.md) | Pricing baseline + overrides, SQLite ledger, spend queries |
| [Budget Configuration](budget-configuration.md) | YAML scopes, soft/hard limits, TPM/RPM ceilings, windows |
| [Fallback Chains](fallback-chains.md) | Typed chains, error classification, cooldowns, context pre-checks |

## Architecture

`create_app()` (in `src/llm_budget_gateway/main.py`) wires the modules
in dependency order and mounts the OpenAI-compatible routes:

```
Settings ──► PriceMap ──► CostCalculator ──► CostTracker ──► SQLite (WAL)
                        (pricing)         (math)          (ledger)
                                                              ▲
Budget YAML ──► BudgetEnforcer ──────────┐                    │
               (sync TPM/RPM + async $)  │  spend_since / record
                                          ▼                    │
GATEWAY_FALLBACK_CONFIGS ──► FallbackManager ──► GatewayProxy ─┘
                            (chains, cooldowns)  (auth · scopes · forward)
                                                       │ litellm SDK
                                                   upstream providers
```

Import direction is acyclic: `main → gateway_proxy → {model_fallback,
budget_enforcement, cost_tracking}`, with `budget_enforcement →
cost_tracking` (type-only).

## Module map

| Module | Responsibility |
|---|---|
| `gateway_proxy.py` | Request lifecycle: auth → scopes → sync enforce → forward → cost record; `forward()` isolates all litellm calls |
| `cost_tracking.py` | Token × price math (`PriceMap`, `CostCalculator`), SQLite WAL ledger (`CostStore`), async facade (`CostTracker`) |
| `budget_enforcement.py` | `BudgetScope`/`BudgetConfig`, sync TPM/RPM ceilings (429), async hard dollar budgets (412), soft alerts, YAML loader |
| `model_fallback.py` | `FallbackConfig`/`FallbackManager`: typed chains, error classification, cooldowns, context pre-checks, `dispatch()` |
| `main.py` | `create_app()` factory: wires everything and mounts `/v1/*` + `/health` |

## Examples

All examples run standalone (no API key, no network) with the repo's
virtualenv and print their own output:

| Example | Demonstrates |
|---|---|
| `examples/quickstart.py` | Full HTTP surface against a fake provider: 200/401/404/412/429/502, SSE streaming, embeddings, cost ledger |
| `examples/cost_tracking.py` | Pricing baseline + overrides, cost math, SQLite ledger persistence, streaming usage aggregation |
| `examples/budget_enforcement.py` | Windows, YAML loading, TPM/RPM ceilings, hard/soft budgets, composite scopes |
| `examples/fallback_chains.py` | Error classification, cooldowns, dispatch fallback, context pre-checks |
| `examples/budgets.example.yaml` | Canonical budget configuration shape (copy to `budgets.yaml`) |

- [Control Center](control-center.md) - dashboard, admin API, RBAC, reservations, policy, alerts and routing.
- [Product UI suite](product-ui.md) - six accessible operational workspaces and recovery patterns.
- [Governance and automation suite](governance-suite.md) - identity, evidence, FinOps, guarded recovery and privacy controls.
- [Enterprise control suite](enterprise-control-suite.md) - approvals, evidence, SCIM, model routing, privacy cases and tool governance.

- [Operations Suite](operations-suite.md) - prompt versions, retries, quotas, catalogs and SLOs.
- [Operations API](api/operations-api.md) - authentication and endpoint guide.
- [Operations Research](research/operations-research-2026-07-31.md) - evidence and RICE prioritization.

- [Quality Suite](quality-suite.md) - evaluations, gates, traces, batches and audit reports.
- [Quality API](api/quality-api.md) - authentication and OpenAPI usage.
- [Quality Research](research/quality-research-2026-07-31.md) - evidence and RICE decisions.

- [Security Center](security-center.md)
- [Security API](api/security-api.md)
- [Security Research](research/security-research-2026-07-31.md)

- [Resilience Center](resilience-center.md)

- [Optimization Center](optimization-center.md)

- [Collaboration Center](collaboration-center.md)

- [Platform Center](platform-center.md)
- [Platform API](api/platform-api.md)
- [Platform Research](research/platform-research-2026-07-31.md)

- [AgentOps Center](agentops-center.md)
- [AgentOps API](api/agentops-api.md)

- [Assurance Center](assurance-center.md)
- [Assurance API](api/assurance-api.md)

- [Delivery Center](delivery-center.md) - executable production readiness and release controls.
- [Delivery API](api/delivery-api.md) - authentication, capabilities, and errors.

- [Scale Center](scale-center.md) - multi-instance topology, migration, residency, and recovery controls.
- [Scale API](api/scale-api.md) - capabilities, authentication, and errors.

- [Unified Gateway Console](unified-console.md) - searchable UI, health checks, command palette and universal API runner.

- [MCP Governance](mcp-governance.md) - MCP server registry, per-tool policies and budgets, audit trail, SSRF/PII rules, engine.
- [MCP Governance API](api/mcp-governance-api.md) - authentication, endpoints, request/response examples, errors.

- [Activation Center](activation-center.md) - guided setup and fail-closed activation controls.
- [Activation API](api/activation-api.md) - capability and authentication reference.

- [Product Adoption Center](adoption-center.md) - product validation and rollout decisions.
- [Product Adoption API](api/adoption-api.md) - authenticated capability reference.

- [Task-oriented Console 9.1](task-oriented-console.md) - daily workflows, favorites, recents, context, and validation.
