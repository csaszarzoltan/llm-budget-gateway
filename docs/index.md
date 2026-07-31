# LLM Budget Gateway — Documentation

OpenAI-compatible LLM gateway with budget enforcement (sync TPM/RPM
ceilings, soft/hard dollar limits, hierarchical scopes) and automatic
model fallback. Providers are reached through the LiteLLM SDK — the
gateway owns the enforcement, not the provider connectivity.

| Guide | What it covers |
|---|---|
| [Getting Started](getting-started.md) | Install, configure, run, first request |
| [Proxy Setup](proxy-setup.md) | Endpoints, environment config, HTTP semantics, security |
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
