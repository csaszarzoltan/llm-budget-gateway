# LLM Budget Gateway

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Tests: 220](https://img.shields.io/badge/tests-220%20passing-brightgreen.svg)]()
[![Version: 0.1.0](https://img.shields.io/badge/version-0.1.0-blue.svg)]()
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://opensource.org/licenses/MIT)

OpenAI-compatible LLM proxy with **budget enforcement** (sync TPM/RPM
ceilings, soft/hard dollar limits, hierarchical scopes) and
**automatic model fallback**. Your clients point their base URL at the
gateway; it authenticates, enforces budgets, and routes to 100+
providers through the LiteLLM SDK — while recording every request's
token usage and dollar cost in a SQLite ledger.

The enforcement engine is first-party code: the product is the gateway,
so the moat is owned. LiteLLM is used as a library (SDK, not proxy
server) and is the swap point for provider connectivity.

## Features

| Area | What you get |
|---|---|
| OpenAI-compatible API | `POST /v1/chat/completions`, `/v1/completions`, `/v1/embeddings`, `GET /v1/models`, `GET /health` — drop-in replacement for calling the provider directly |
| Virtual keys | Static key table (`GATEWAY_VIRTUAL_KEYS`): client sends a key, gateway resolves it to a budget scope; unknown key → `401` |
| Sync rate ceilings | TPM/RPM limits checked pre-dispatch from atomic counters → `429` (race-free hard ceiling) |
| Dollar budgets | Per-scope `soft_limit` (alert, never blocks) + `hard_limit` (reject with `412`, Portkey convention), rolling windows (`30s`…`monthly`) |
| Hierarchical scopes | `global > team > user > key`, all checked per request — one key can't blow the team budget; user/team via operator-configured headers |
| Cost tracking | Token × price (litellm baseline + `pricing_overrides`) persisted per request to SQLite (WAL) — including aggregated streaming usage |
| Automatic fallback | Typed chains per model, error classification, cooldowns, context pre-checks; serving model reported and billed |
| Provider timeouts | `GATEWAY_PROVIDER_TIMEOUT` bounds every upstream call and stream chunk → clean `502`, never a hung worker |
| Security | Client body allow-list (no `api_key`/`base_url` injection), key redaction in logs, no prompt content stored |

## Quick start

```bash
git clone https://github.com/csaszarzoltan/llm-budget-gateway.git
cd llm-budget-gateway
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

```bash
export GATEWAY_VIRTUAL_KEYS='{"sk-test-123":"key1"}'
export OPENAI_API_KEY="sk-..."              # provider creds come from env, never clients
cp examples/budgets.example.yaml budgets.yaml
.venv/bin/uvicorn llm_budget_gateway.main:create_app --factory --port 8000
```

```bash
curl -s http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-test-123" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello!"}]}'
```

The full request lifecycle — including the budget and fallback layers —
runs without a key or network:

```bash
.venv/bin/python examples/quickstart.py
```

## HTTP semantics

| Status | Meaning |
|---|---|
| `200` | Provider response (JSON, or SSE stream for `stream=true`) |
| `400` | Malformed request body (invalid JSON, or a non-object JSON value) |
| `401` | Missing / unknown virtual API key |
| `404` | Unknown model |
| `412` | Hard dollar budget exceeded (Portkey convention) |
| `429` | TPM/RPM ceiling exceeded |
| `502` | Upstream error, timeout, or fallback chain exhausted |

## Documentation

- [Getting Started](docs/getting-started.md) — install, configure, run, first request
- [Proxy Setup](docs/proxy-setup.md) — endpoints, env vars, HTTP semantics, security
- [Preflight Cost Estimation](docs/cost-estimation.md) — estimate upper-bound request cost without provider traffic
- [Cost Tracking](docs/cost-tracking.md) — pricing, ledger schema, spend queries
- [Budget Configuration](docs/budget-configuration.md) — scopes, limits, windows
- [Fallback Chains](docs/fallback-chains.md) — chains, classification, cooldowns

Runnable examples live in [`examples/`](examples/):
`quickstart.py` (full HTTP surface), `cost_tracking.py`,
`budget_enforcement.py`, `fallback_chains.py`, and the canonical
`budgets.example.yaml`.

## Development

```bash
.venv/bin/python -m pytest       # 220 tests pass
.venv/bin/python -m ruff check src tests examples
```

Requires Python 3.11+. Runtime deps are pinned in `pyproject.toml`
(FastAPI, uvicorn, pydantic v2, litellm <2, PyYAML).

## Roadmap

- **P1** — reserve-and-reconcile sync dollar caps, Redis `CounterStore`
  (multi-instance), alert webhooks, Postgres ledger
- **P2** — spend/admin API + dashboards, virtual key lifecycle, health-aware
  load balancing, data-policy routing

## License

MIT

## Control Center and governed operations

Version 0.2 adds a tenant-isolated control plane for the six product-research requirements: an accessible responsive dashboard, hashed virtual-key lifecycle with RBAC, atomic reserve-and-reconcile budgets, spend CSV and alerts, fail-closed policy routing, and health-aware routes with circuit breaking and bounded caching. Run `uvicorn llm_budget_gateway.control_api:create_control_app --factory --port 8001`, open `/control`, and call `/v1/admin/*` with `X-Tenant-Id` and `X-Role`. Mutations require operator, security, or admin roles. Key creation accepts `Idempotency-Key`; secrets are shown once. SQLite is the single-node Community backend; adapters for distributed stores remain the deployment boundary.

Tests: `PYTHONPATH=src python -m pytest -q tests/test_product_control_plane.py`.

## Product UI suite

The control center now defines six responsive, WCAG-oriented product pages: guided setup, spend explorer, key access, policy studio, route health and operations activity. The server-rendered view layer lives in `product_ui.py`, keeps business rules in `control_plane.py`, fails closed for unknown pages, removes secrets from rendered context, and exposes loading, empty, success/recovery and permission-aware states. Run `PYTHONPATH=src python -m pytest -q tests/test_ui_product_suite.py`.

## Governance and automation suite

Version 0.4 adds a tenant-scoped governance service covering approval-gated automation recommendations, deterministic compliance evidence packages, identity membership authorization, explainable FinOps anomaly forecasts, guarded reliability recovery, and retention/residency enforcement. The implementation is in `llm_budget_gateway.governance.GovernanceService`; it uses additive SQLite tables, redacts prompt/secret fields, requires explicit roles, and never applies automation without approval. Existing data-plane and control-plane APIs remain compatible.

Run `PYTHONPATH=src python -m pytest -q tests/test_governance_suite.py`.

## Enterprise control suite

Version 0.5 adds four-eyes approvals with expiry and idempotency, continuous control evidence with freshness reporting and deterministic integrity hashes, tenant-isolated SCIM provisioning and access reviews, explainable quality/cost/latency model selection, privacy export/delete cases with legal holds, and approval-gated agent tool execution with per-tool cost ceilings. The additive implementation lives in `llm_budget_gateway.enterprise_features.EnterprisePlatform`. Existing gateway, control-plane, governance, and UI contracts remain unchanged.

Run `PYTHONPATH=src python -m pytest -q tests/test_enterprise_features.py`.


### Gateway Intelligence 0.7

Five market-driven controls are available through `llm_budget_gateway.market_api`:

- local PII redaction for email, phone, and payment-card-like values;
- tenant-isolated exact response caching with TTL;
- HMAC-SHA256 signed alert envelopes;
- explainable cost anomaly detection;
- cost-aware model routing with health, quality, and latency constraints.

Run `uvicorn llm_budget_gateway.market_api:create_market_app --factory --port 8002`, open `/intelligence`, and use `/docs` for generated OpenAPI. Every intelligence API requires `X-Tenant-Id`. See `docs/feature-specifications.md`, `docs/api/intelligence-api.md`, and `docs/migration-0.7.md`.


## Gateway Operations 0.8

Version 0.8 adds five production operations capabilities: immutable tenant-scoped prompt versions with deterministic experiments, bounded retry decisions, actionable quota diagnostics, validated model catalog metadata, and SLO/error-budget monitoring. Set `GATEWAY_OPERATIONS_API_KEY`, run `uvicorn llm_budget_gateway.operations_api:create_operations_app --factory --port 8003`, open `/operations`, and use `/docs` for OpenAPI. See `docs/operations-suite.md`, `docs/api/operations-api.md`, and `docs/migration-0.8.md`.


## Gateway Quality 0.9

Version 0.9 adds deterministic evaluation runs, CI-ready release quality gates, privacy-safe trace context, batch manifest planning and integrity-protected audit reports. Set `GATEWAY_EVALUATION_API_KEY`, run `uvicorn llm_budget_gateway.evaluation_api:create_evaluation_app --factory --port 8004`, open `/quality`, and use `/docs` for OpenAPI. See `docs/quality-suite.md`, `docs/api/quality-api.md`, and `docs/migration-0.9.md`.


## Security Center 1.0

Five production security controls: local secret scanning, durable webhook replay protection, fail-closed provider compliance, change-risk approvals and posture scoring. Set `GATEWAY_SECURITY_API_KEY`, run `uvicorn llm_budget_gateway.security_api:create_security_app --factory --port 8005`, and open `/security` or `/docs`. See `docs/security-center.md`.


## Resilience Center 1.1
Five production recovery controls. See `docs/resilience-center.md`.


## Optimization Center 1.2
Five cost and performance controls. See `docs/optimization-center.md`.


## Collaboration Center 1.3
Project roles, one-time invitations, key lifecycle, member budgets and delegated approvals. See `docs/collaboration-center.md`.


## Platform Center 2.0

Version 2.0 adds twenty catalog, FinOps, reliability, compliance, quality, release, and adoption capabilities. Set `GATEWAY_PLATFORM_API_KEY`, run `uvicorn llm_budget_gateway.platform_api:create_platform_app --factory --port 8009`, and open `/platform` or `/docs`. See `docs/platform-center.md`.


## AgentOps Center 3.0
Twenty MCP, security, reliability, observability, FinOps, sustainability, support, localization, and residency controls. Configure `GATEWAY_AGENTOPS_API_KEY` and see `docs/agentops-center.md`.


## Assurance Center 5.0
Twenty continuous-assurance capabilities. Configure `GATEWAY_ASSURANCE_API_KEY`; see `docs/assurance-center.md`.
