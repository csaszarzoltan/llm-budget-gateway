# LLM Budget Gateway

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
![Version 9.7.0](https://img.shields.io/badge/version-9.4.0-blue.svg)
![Tests 800 passing](https://img.shields.io/badge/tests-800%20passing-brightgreen.svg)
![New Scale modules 97% coverage](https://img.shields.io/badge/scale%20coverage-97%25-brightgreen.svg)
[![License MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://opensource.org/licenses/MIT)

**LLM Budget Gateway** is an OpenAI-compatible gateway and governance platform for controlling LLM cost, reliability, security, quality, privacy, agent operations, release delivery, and multi-instance scale.

Applications continue to send OpenAI-shaped requests. The gateway authenticates virtual keys, resolves tenant and budget scopes, enforces limits, selects or falls back between models, forwards through the LiteLLM SDK, and records usage and cost without persisting prompt or response content.

**Current package version:** `9.7.0`

> [!IMPORTANT]
> The built-in SQLite repositories are intended for development and single-node deployments. Multi-instance production deployments must use transactional shared repository adapters. Scale Center provides planning and validation decisions, but it does not provision Postgres, Redis, or other infrastructure.

## Why this project exists

LLM applications need more than provider connectivity. Teams also need predictable spend, reliable fallback behavior, safe credentials, tenant isolation, quality gates, policy enforcement, operational evidence, and controlled production rollout. This project keeps those controls in first-party Python domain services while using LiteLLM only as the provider SDK boundary.

Primary users include:

- AI application developers integrating OpenAI-compatible clients
- Platform engineers operating models, providers, prompts, and routing
- SRE and operations teams managing reliability and incidents
- FinOps teams controlling budgets, attribution, and optimization
- Security, privacy, governance, compliance, and audit teams
- Quality and release engineers validating changes before production
- Enterprise administrators managing tenants, projects, users, keys, and agents

## Capabilities

### OpenAI-compatible data plane

- `POST /v1/chat/completions`
- `POST /v1/completions`
- `POST /v1/embeddings`
- `GET /v1/models`
- `GET /health`
- SSE streaming with usage aggregation
- Provider timeouts for the initial call and each stream chunk
- OpenAI-shaped success and error responses

### Cost and budget controls

- Virtual API keys mapped to internal key identities
- Hierarchical scopes: `global > team > user > key`
- Atomic pre-dispatch RPM and TPM ceilings
- Soft and hard dollar limits over configurable windows
- Preflight cost estimation without a provider call
- LiteLLM pricing plus operator-defined pricing overrides
- SQLite WAL cost ledger and per-scope spend queries
- Atomic reserve-and-reconcile controls in the Control Center
- Spend exports, forecasts, anomaly analysis, and optimization tools

### Routing and resilience

- Ordered model fallback chains
- Rate-limit, timeout, server-error, policy, and context classification
- Cooldowns and context-window prechecks
- Health-aware routing and circuit breakers
- Bounded retry decisions
- Adaptive concurrency
- Dead-letter storage and exactly-once replay controls
- Maintenance windows and incident timelines
- Canary planning, rollback guardrails, and dependency health decisions

### Security, privacy, and compliance

- Client request-body allow-list that drops provider credentials and URLs
- Redaction of virtual keys and provider errors
- Local PII and secret detection
- Durable replay protection
- Signed webhook envelopes
- Fail-closed provider compliance and residency policies
- Data retention, legal hold, privacy export, and deletion workflows
- Four-eyes approvals and evidence integrity hashes
- No prompt or response content in the cost ledger
- MCP server governance: registry, per-tool allow/deny/approval policies,
  per-tool cost ceilings, SSRF + PII rules, and a redacted audit trail

### Quality, governance, and assurance

- Deterministic evaluations and release quality gates
- Privacy-safe trace and session resolution
- Batch manifest planning
- Integrity-protected audit reports
- Prompt registries, experiments, and immutable history
- Risk, fairness, robustness, hallucination, drift, provenance, and maturity checks
- Continuous evidence freshness and corrective-action tracking
- Approval-gated automation and governed tool runs

### Collaboration, platform, and agent operations

- Project-scoped RBAC and one-time invitations
- Key lifecycle guidance and member-level budget controls
- Delegated approvals that prevent self-approval
- Catalogs, tags, cost allocation, quota planning, SLOs, DLP, and region routing
- Model and provider scorecards
- Agent identity, inventory, lifecycle, capability grants, and kill switches
- Tool-access policies, delegation-depth limits, leases, audit chains, and cost meters
- Fleet-wide shadow-agent, runaway-workflow, and compliance controls

### Delivery Center 6.0

Ten deterministic delivery controls cover:

- environment readiness,
- configuration drift,
- RPM and TPM capacity planning,
- required and optional dependency health,
- canary rollout planning,
- rollback decisions,
- observability coverage,
- signed alert-route validation,
- runbook coverage,
- integrity-protected release manifests.

See [Delivery Center](docs/delivery-center.md) and [Delivery API](docs/api/delivery-api.md).

### Scale Center 7.0

Ten deterministic multi-instance controls cover:

- storage topology,
- replication quorum,
- workload partitioning,
- consistency policy,
- regional failover,
- shared-store migration readiness,
- connection-pool sizing,
- privacy-safe tenant sharding,
- residency-aware storage topology,
- RPO and RTO evaluation.

See [Scale Center](docs/scale-center.md) and [Scale API](docs/api/scale-api.md).


## Unified Gateway Console 7.1

A responsive, keyboard-accessible console now provides one searchable catalog for all 15 workspaces and every registered capability. It includes a command palette, service-health checks, links to every dashboard and OpenAPI surface, and a universal JSON API runner with cURL generation.

```bash
uvicorn llm_budget_gateway.console_api:create_console_app --factory --port 8013
```

Open `http://localhost:8013/console`. See [Unified Gateway Console](docs/unified-console.md).

## Architecture

The project separates transport, domain policy, persistence, and provider connectivity:

```text
OpenAI-compatible clients
          |
          v
GatewayProxy
  auth -> model validation -> scope resolution -> sync limits
       -> dollar budget check -> fallback dispatch -> cost record
          |
          v
FallbackManager -> LiteLLM SDK -> upstream providers
          |
          v
CostTracker -> CostCalculator -> SQLite WAL ledger

Administrative and product APIs
          |
          v
FastAPI adapters -> deterministic domain services -> SQLite repositories

Delivery and Scale APIs
          |
          v
Stateless planning and validation services
```

Important dependency rules:

- `main.py` wires the core data plane.
- `gateway_proxy.py` owns the request lifecycle and provider boundary.
- `budget_enforcement.py`, `cost_tracking.py`, and `model_fallback.py` contain core policy and accounting logic.
- `*_suite.py` modules contain transport-independent domain services.
- `*_api.py` modules authenticate tenant requests and translate predictable input errors to HTTP responses.
- Browser dashboards are presentation layers and do not contain credentials.
- New product centers are additive. Existing APIs remain compatible unless a migration guide explicitly states otherwise.

## Repository layout

The canonical project has no `work/` or enclosing `project/` directory:

```text
llm-budget-gateway/
├── src/llm_budget_gateway/   # Application and domain modules
├── tests/                    # Unit, integration, API, UI, and regression tests
├── docs/                     # User, API, architecture, research, and migration docs
├── examples/                 # Network-independent runnable examples
├── README.md
├── CHANGELOG.md
├── CONTRIBUTING.md
├── pyproject.toml
├── .gitignore
└── git-history.bundle
```

Generated or local-only paths should not be committed:

```text
.venv/
.pytest_cache/
.ruff_cache/
__pycache__/
*.pyc
build/
dist/
htmlcov/
.coverage
*.db
*.db-wal
*.db-shm
```

## Requirements

- Python 3.11 or newer
- Runtime packages declared in `pyproject.toml`:
  - FastAPI
  - Uvicorn
  - Pydantic and pydantic-settings
  - LiteLLM 1.40 or newer and lower than 2.0
  - PyYAML
  - MCP SDK 1.2 or newer and lower than 2.0 (live tool discovery)
- Development extras:
  - Pytest
  - pytest-asyncio
  - pytest-mock
  - pytest-cov
  - Ruff

## Quick start

### 1. Create a virtual environment

```bash
git clone https://github.com/csaszarzoltan/llm-budget-gateway.git
cd llm-budget-gateway
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

### 2. Configure the core gateway

```bash
export GATEWAY_VIRTUAL_KEYS='{"sk-test-123":"key1"}'
export OPENAI_API_KEY='replace-with-provider-key'
cp examples/budgets.example.yaml budgets.yaml
```

Provider credentials come from the gateway process environment. They are never accepted from client request bodies.

### 3. Start the gateway

```bash
.venv/bin/uvicorn \
  llm_budget_gateway.main:create_app \
  --factory \
  --port 8000
```

### 4. Verify health

```bash
curl -s http://localhost:8000/health
```

Expected response:

```json
{"status":"ok"}
```

### 5. Send a chat request

```bash
curl -s http://localhost:8000/v1/chat/completions \
  -H 'Authorization: Bearer sk-test-123' \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### 6. Run the offline example

The complete HTTP surface can be exercised against a fake provider without network access or a provider key:

```bash
.venv/bin/python examples/quickstart.py
```

## Core configuration

All gateway settings use the `GATEWAY_` prefix.

| Variable | Default | Purpose |
|---|---:|---|
| `GATEWAY_DATABASE_URL` | `sqlite:///./gateway.db` | Cost-ledger location |
| `GATEWAY_BUDGET_CONFIG_PATH` | `budgets.yaml` | Hierarchical budget configuration |
| `GATEWAY_VIRTUAL_KEYS` | `{}` | Client key to internal key-ID mapping |
| `GATEWAY_USER_HEADER_MAPPINGS` | `{}` | Explicit user and team scope headers |
| `GATEWAY_PRICING_OVERRIDES` | `{}` | Operator-defined model prices |
| `GATEWAY_FALLBACK_CONFIGS` | `[]` | Ordered per-model fallback chains |
| `GATEWAY_PROVIDER_TIMEOUT` | `60.0` | Provider and stream-chunk timeout in seconds |

JSON-valued environment variables must contain valid JSON. See [Proxy Setup](docs/proxy-setup.md), [Budget Configuration](docs/budget-configuration.md), and `.env.example` when present.

## Service map

The core gateway and product centers are independent FastAPI app factories. Run only the services required for your deployment.

| Port | Service | App factory | Authentication and configuration | Documentation |
|---:|---|---|---|---|
| 8000 | Core gateway | `llm_budget_gateway.main:create_app` | Virtual key from `GATEWAY_VIRTUAL_KEYS` | [Proxy Setup](docs/proxy-setup.md) |
| 8001 | Control Center | `llm_budget_gateway.control_api:create_control_app` | `X-Tenant-Id`, `X-Role` | [Control Center](docs/control-center.md) |
| 8002 | Intelligence | `llm_budget_gateway.market_api:create_market_app` | `X-Tenant-Id` and app configuration | [Intelligence API](docs/api/intelligence-api.md) |
| 8003 | Operations | `llm_budget_gateway.operations_api:create_operations_app` | `GATEWAY_OPERATIONS_API_KEY` | [Operations API](docs/api/operations-api.md) |
| 8004 | Quality | `llm_budget_gateway.evaluation_api:create_evaluation_app` | `GATEWAY_EVALUATION_API_KEY` | [Quality API](docs/api/quality-api.md) |
| 8005 | Security | `llm_budget_gateway.security_api:create_security_app` | `GATEWAY_SECURITY_API_KEY` | [Security API](docs/api/security-api.md) |
| 8006 | Resilience | `llm_budget_gateway.resilience_api:create_resilience_app` | `GATEWAY_RESILIENCE_API_KEY` | [Resilience API](docs/api/resilience-api.md) |
| 8007 | Optimization | `llm_budget_gateway.optimization_api:create_optimization_app` | `GATEWAY_OPTIMIZATION_API_KEY` | [Optimization API](docs/api/optimization-api.md) |
| 8008 | Collaboration | `llm_budget_gateway.collaboration_api:create_collaboration_app` | `GATEWAY_COLLABORATION_API_KEY` | [Collaboration API](docs/api/collaboration-api.md) |
| 8009 | Platform | `llm_budget_gateway.platform_api:create_platform_app` | `GATEWAY_PLATFORM_API_KEY` | [Platform API](docs/api/platform-api.md) |
| 8010 | AgentOps | `llm_budget_gateway.agentops_api:create_agentops_app` | `GATEWAY_AGENTOPS_API_KEY` | [AgentOps API](docs/api/agentops-api.md) |
| 8011 | Fleet Governance | `llm_budget_gateway.fleet_api:create_fleet_app` | `GATEWAY_FLEET_API_KEY` | [Fleet API](docs/api/fleet-api.md) |
| 8013 | Unified Console | `llm_budget_gateway.console_api:create_console_app` | Presentation-only; credentials remain in browser session | [Unified Console](docs/unified-console.md) |
| 8012 | Assurance | `llm_budget_gateway.assurance_api:create_assurance_app` | `GATEWAY_ASSURANCE_API_KEY` | [Assurance API](docs/api/assurance-api.md) |
| 8014 | Delivery | `llm_budget_gateway.delivery_api:create_delivery_app` | `GATEWAY_DELIVERY_API_KEY` | [Delivery API](docs/api/delivery-api.md) |
| 8015 | Scale | `llm_budget_gateway.scale_api:create_scale_app` | `GATEWAY_SCALE_API_KEY` | [Scale API](docs/api/scale-api.md) |
| 8016 | MCP Governance | `llm_budget_gateway.mcp_governance.api:create_mcp_governance_app` | `GATEWAY_MCP_API_KEY` | [MCP Governance](docs/mcp-governance.md) |

Protected product APIs normally require both `Authorization: Bearer <key>` and `X-Tenant-Id`. An absent server-side API key returns `503`, invalid authentication returns `401`, unknown capabilities return `404`, and invalid payloads return `422`.

## Core HTTP semantics

| Status | Meaning |
|---:|---|
| `200` | Provider response, JSON result, or SSE stream |
| `400` | Malformed JSON or a non-object request body |
| `401` | Missing or unknown virtual API key |
| `404` | Unknown model |
| `412` | Hard dollar budget exceeded |
| `429` | RPM or TPM ceiling exceeded |
| `502` | Provider failure, timeout, or exhausted fallback chain |

Gateway error bodies follow the OpenAI error shape:

```json
{
  "error": {
    "message": "...",
    "type": "invalid_request_error",
    "code": 429
  }
}
```

## Security model

- Client bodies are allow-listed before provider dispatch.
- Client-supplied `api_key`, `api_base`, `base_url`, and `headers` are dropped.
- Provider credentials and endpoints come from trusted server configuration.
- Authentication failures redact submitted keys in logs.
- Provider exception details are not returned to clients.
- Prompt and response content are not written to the cost ledger.
- Tenant and scope headers are trusted only when explicitly configured.
- Product APIs fail closed when their server-side API key is absent.
- Secret and PII controls return categories and counts rather than detected values.
- Invitation plaintext, secret values, and authorization fields are removed before persistence where applicable.

## Persistence and production deployment

SQLite is used to keep development and Community-mode deployments self-contained. SQLite-backed modules include cost records and several product-center stores.

For multiple gateway instances:

1. Select a transactional shared store appropriate to the workload.
2. Require strong consistency for budgets, reservations, keys, invitations, approvals, and replay protection.
3. Back up all existing stores.
4. Validate the target schema.
5. Rehearse the migration.
6. Run targeted tests and the full regression suite.
7. Verify rollback before traffic cutover.
8. Confirm residency, replication quorum, connection-pool, RPO, and RTO requirements.

Use [Scale Center](docs/scale-center.md), [Migration to 7.0](docs/migration-7.0.md), and the earlier migration guides for release-specific requirements.

## Testing and development

### Targeted tests

```bash
.venv/bin/python -m pytest -q tests/test_scale_suite.py tests/test_scale_api.py
```

### Complete regression suite

```bash
.venv/bin/python -m pytest -q
```

Current validated result:

```text
800 passed
```

### Coverage

```bash
.venv/bin/python -m pytest --cov=llm_budget_gateway
```

The newly added Scale Center modules have 97% statement coverage, and `scale_api.py` has 100% coverage.

### Lint

```bash
.venv/bin/python -m ruff check src tests examples
```

### Build

```bash
uv build
```

The validated release process builds both a source distribution and a Python wheel.

See [CONTRIBUTING.md](CONTRIBUTING.md) for branch, typing, testing, security, and documentation requirements.

## Runnable examples

| Example | Purpose |
|---|---|
| [`examples/quickstart.py`](examples/quickstart.py) | Core HTTP routes, status codes, streaming, embeddings, and ledger inspection against a fake provider |
| [`examples/cost_tracking.py`](examples/cost_tracking.py) | Pricing, cost calculation, SQLite persistence, spend queries, and streaming aggregation |
| [`examples/budget_enforcement.py`](examples/budget_enforcement.py) | Budget windows, YAML loading, hierarchical scopes, RPM/TPM limits, and dollar budgets |
| [`examples/fallback_chains.py`](examples/fallback_chains.py) | Error classification, cooldowns, fallback dispatch, and context checks |
| [`examples/mcp_governance.py`](examples/mcp_governance.py) | MCP server registry, per-tool policies/budgets, audit trail, SSRF + PII rules, engine, and REST API |
| [`examples/budgets.example.yaml`](examples/budgets.example.yaml) | Canonical budget configuration template |

## Documentation index

### Start here

- [Documentation home](docs/index.md)
- [Getting Started](docs/getting-started.md)
- [Proxy Setup](docs/proxy-setup.md)
- [FAQ](docs/faq.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

### Core gateway

- [Preflight Cost Estimation](docs/cost-estimation.md)
- [Cost Tracking](docs/cost-tracking.md)
- [Budget Configuration](docs/budget-configuration.md)
- [Fallback Chains](docs/fallback-chains.md)

### Product and governance centers

- [Control Center](docs/control-center.md)
- [Product UI](docs/product-ui.md)
- [Gateway Intelligence feature specifications](docs/feature-specifications.md)
- [Operations Suite](docs/operations-suite.md)
- [Quality Suite](docs/quality-suite.md)
- [Security Center](docs/security-center.md)
- [Resilience Center](docs/resilience-center.md)
- [Optimization Center](docs/optimization-center.md)
- [Collaboration Center](docs/collaboration-center.md)
- [Platform Center](docs/platform-center.md)
- [AgentOps Center](docs/agentops-center.md)
- [Fleet Governance](docs/fleet-governance.md)
- [Assurance Center](docs/assurance-center.md)
- [Delivery Center](docs/delivery-center.md)
- [Scale Center](docs/scale-center.md)
- [Governance Suite](docs/governance-suite.md)
- [Enterprise Control Suite](docs/enterprise-control-suite.md)
- [MCP Governance](docs/mcp-governance.md)

### API guides

- [Intelligence API](docs/api/intelligence-api.md)
- [Operations API](docs/api/operations-api.md)
- [Quality API](docs/api/quality-api.md)
- [Security API](docs/api/security-api.md)
- [Resilience API](docs/api/resilience-api.md)
- [Optimization API](docs/api/optimization-api.md)
- [Collaboration API](docs/api/collaboration-api.md)
- [Platform API](docs/api/platform-api.md)
- [AgentOps API](docs/api/agentops-api.md)
- [Fleet API](docs/api/fleet-api.md)
- [Assurance API](docs/api/assurance-api.md)
- [Delivery API](docs/api/delivery-api.md)
- [Scale API](docs/api/scale-api.md)
- [MCP Governance API](docs/api/mcp-governance-api.md)

### Research, validation, and delivery records

- [Market Research](docs/market-research.md)
- [Requirements Research](docs/research-2026-07-31.md)
- Development-cycle records are under `docs/development-cycles-*.md`.
- GitLab issue and release records are under `docs/gitlab/`.

### Migration guides

Migration guides are available from `docs/migration-0.7.md` through [`docs/migration-7.0.md`](docs/migration-7.0.md). Releases are designed to be additive unless a guide explicitly states otherwise.

## Versioning and release history

The package version is `9.7.0`. Historical center versions in documentation describe the release in which a capability family was introduced. They are all included in the current package.

Major milestones include:

- Core gateway, budgets, cost tracking, fallback, streaming, and embeddings
- Control Center and accessible product UI
- Governance and enterprise workflows
- Intelligence, Operations, Quality, Security, Resilience, and Optimization centers
- Collaboration, Platform, AgentOps, Fleet Governance, and Assurance centers
- Delivery Center 6.0
- Scale Center 7.0

For detailed changes, see [CHANGELOG.md](CHANGELOG.md).

## Current roadmap

### Implemented foundations

- OpenAI-compatible proxy and provider SDK boundary
- Budget, cost, fallback, caching, routing, security, quality, governance, and assurance controls
- Administrative dashboards and authenticated product APIs
- Delivery and multi-instance planning controls

### Next production priorities

- Concrete Postgres and Redis repository adapters behind existing interfaces
- Multi-instance integration tests using shared transactional stores
- Automated schema migration tooling and operational runbooks
- Managed notifications and alert delivery workers
- Metrics export and external observability integrations
- Deployment manifests and container-orchestration examples

Roadmap items must preserve current public contracts, fail closed at privilege and policy boundaries, and include targeted plus full regression testing.

## License

The project identifies its license as MIT. See the repository licensing metadata and add a root `LICENSE` file before external redistribution if your distribution process requires the full license text in the source archive.

## Activation Center 8.0

Ten privacy-safe onboarding controls cover setup progress, environment templates, provider readiness, ports, configuration, first requests, starter budgets, persona profiles, diagnostics, and activation gates. See [docs/activation-center.md](docs/activation-center.md).

## Product Adoption Center 9.0

Ten privacy-safe product-validation controls cover funnels, retention, feature adoption, experiments, feedback themes, pricing signals, staged rollout, success thresholds, and integrity-protected reports. See [docs/adoption-center.md](docs/adoption-center.md).


## Task-oriented console 9.1

The Unified Console now starts with six daily workflows for activation, spend investigation, 412/429/502 recovery, key rotation, release preparation, and security review. It remembers non-secret tenant/workspace context, recent tasks, and favorites locally, and validates JSON request bodies before sending. Existing workspaces and APIs remain available. See [Task-oriented Unified Console](docs/task-oriented-console.md).


## Guided workflow stepper 9.2

Daily workflow cards now open a guided multi-step runner. The console shows progress, the current workspace/capability, and previous/next controls while preserving the existing expert runner. Individual workflow definitions are also available from `GET /v1/console/workflows/{workflow_id}`.


## Guided input presets 9.3

Each guided workflow step now loads a safe, non-secret example JSON object and a short explanation. Examples are visibly labeled and are never submitted automatically. Users must review and replace example values before sending.


## Resumable daily workflows 9.4

The Unified Console now preserves non-secret workflow completion state locally, resumes at the last active step, supports direct accessible step navigation, and shows completed progress on task cards. Successful guided requests complete their current step. The API runner also prevents duplicate in-flight submission, times out stalled browser calls after 30 seconds, and provides next-action guidance for common HTTP failures. See [Task-oriented Unified Console](docs/task-oriented-console.md) and the [9.4 implementation report](docs/implementation-report-9.4.md).

## MCP server governance 9.4

Tool-level governance for MCP servers: a versioned server registry with tool inventory, per-tool `allow`/`deny`/`approval` policies (deny by default), per-tool soft/hard cost ceilings enforced against the shared cost ledger, a PII-redacted audit trail for every call attempt, SSRF and PII rules, and a policy engine that gates calls before execution and records outcomes after. Served as a standalone FastAPI app (`GATEWAY_MCP_API_KEY`) with a responsive `/mcp` dashboard; the engine API (`before_call` / `after_call`) is the integration point for an MCP proxy adapter.

```bash
export GATEWAY_MCP_API_KEY='replace-with-a-strong-random-secret'
.venv/bin/uvicorn \
  llm_budget_gateway.mcp_governance.api:create_mcp_governance_app \
  --factory \
  --port 8016
```

See [MCP Governance](docs/mcp-governance.md), the [MCP Governance API](docs/api/mcp-governance-api.md), and the runnable [governance example](examples/mcp_governance.py).

## AI Operations Cockpit 9.7

Version 9.7 implements the three P0 priorities validated in `research-findings.md`: one live graphical cockpit, a pre-step Agent Runaway Firewall, and schema-generated guided forms. These address fragmented operations, runaway agent spend, and raw-JSON onboarding friction.

### Main user flow

1. Install and start the console.
2. Build the React UI once.
3. Open `/cockpit`, enter a run's current cost, and select **Evaluate next step**.
4. The firewall returns a clear allow or pause decision, the exact ceiling involved, and a safe next action. Loading, validation, network-error, allowed, and blocked states are all represented.

```bash
uv sync --extra dev
cd ui && npm ci && npm run build && cd ..
uv run uvicorn llm_budget_gateway.console_api:create_console_app --factory --host 127.0.0.1 --port 8013
```

Open [http://127.0.0.1:8013/cockpit](http://127.0.0.1:8013/cockpit). The expert console remains at `/console`.

### Features built from market research

- **Unified AI Operations Cockpit:** summarizes spend, quality, incidents, approvals, and policy coverage so operators do not hunt across services.
- **Agent Runaway Firewall:** blocks the next agent step at configured cost, token, tool, depth, duration, retry, or emergency-stop boundaries and explains the decision.
- **Schema-generated guided forms:** converts JSON Schema to accessible control metadata while marking secret-like fields as non-persistent.

See `docs/priority-features-api.md` for the implemented endpoint contracts and `FEATURES-DONE.md` for machine-readable delivery status.

## Agent trace and outcome intelligence 9.7

Version 9.7 continues the priority roadmap from `research-findings.md` with the first two P1 differentiators:

- **End-to-end agent trace explorer:** tenant-isolated nested spans connect agent planning, tool calls, duration, status and cost without retaining prompt or response content.
- **Cost-to-outcome analytics:** reports total cost, cost per successful outcome, quality-weighted cost, and breakdowns by feature, project, model and tool.

Use `POST /v1/console/traces` to append a span, then retrieve a run through `GET /v1/console/traces/{run_id}` with `X-Tenant-Id`. Use `POST /v1/console/outcomes/summary` to calculate privacy-safe unit economics. Exact contracts are documented in `docs/priority-features-api.md`.

## Verified software supply chain 9.7

Version 9.7 implements the remaining high-value research recommendation for signed, reproducible distribution:

- **Deterministic SBOM:** `GET /v1/console/supply-chain/sbom` inventories pinned Python and npm packages in CycloneDX-compatible JSON.
- **Artifact provenance:** `ProvenanceService` binds release bytes to builder and source identity through SHA-256 in an in-toto/SLSA-style statement and verifies artifacts offline.
- **Upgrade risk gate:** `POST /v1/console/supply-chain/upgrade-risk` identifies major, removed, unpinned, and advisory-affected changes and requires review before automatic rollout.

The implementation uses only local files and standard-library cryptographic hashing. It does not upload dependencies, source code, or release artifacts.
