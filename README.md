# LLM Budget Gateway

A local-first, OpenAI-compatible AI gateway and operations control plane with budget enforcement, logical routing, provider discovery, trace analytics, governance, and a React product cockpit.

## Requirements

- Python 3.11+
- `uv`
- Node.js 20+ and npm when rebuilding the cockpit

## Install

```bash
uv sync --extra dev --frozen
```

Clean release archives built by `scripts/build_release.py` include the verified cockpit in `ui/dist` and exclude runtime state. Raw development trees may require the rebuild below. To rebuild it:

```bash
cd ui
npm ci
npm test
npm run build
cd ..
```

## Run the complete local product

```bash
uv run gateway-system --no-browser
```

Open `http://127.0.0.1:8013/cockpit`. Remove `--no-browser` to open it automatically.

The launcher owns only child services it starts and exposes readiness at:

```text
GET http://127.0.0.1:8013/v1/system/status
```

## Run only the OpenAI-compatible gateway

```bash
export GATEWAY_VIRTUAL_KEYS='{"sk-test-123":"key1"}'
uv run uvicorn llm_budget_gateway.main:create_app --factory --host 127.0.0.1 --port 8000
```

Verify:

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/v1/models
```

Provider credentials are read from the server environment. Client request bodies cannot override provider keys, base URLs, or headers.

## Test and quality gates

```bash
uv run pytest -q
uv run ruff check src tests examples
cd ui && npm test && npm run build
```

## Primary product areas

- **Home:** health, activation, KPIs, attention, active routes, recent decisions
- **Applications:** application identities and stable route assignment
- **Routes:** logical aliases, immutable versions, simulation, publishing, fallback
- **Providers:** encrypted named connections and model discovery
- **Activity:** explainable routing decisions and trace evidence
- **Usage:** request, cost, latency, and success summaries
- **Safety:** Runaway Cost Firewall, Provider Compatibility Lab, and Explain-and-Fix Incident Timeline
- **Advanced:** services, nested traces, governance, security, supply chain, and API access

## Key documentation

- [Getting started](docs/getting-started.md)
- [GUI architecture](docs/gui-architecture-12.md)
- [Logical routing API](docs/logical-routing-api.md)
- [Provider connections](docs/provider-connections-13.2.md)
- [Proxy setup](docs/proxy-setup.md)
- [Budget configuration](docs/budget-configuration.md)
- [MCP governance](docs/mcp-governance.md)
- [Market research](research-findings.md)
- [Changelog](CHANGELOG.md)

## Security and deployment notes

- Runtime databases, environment files, logs, provider master keys, virtual environments, and Node modules are excluded from source control.
- SQLite is the default local/single-node store. Review the production-readiness APIs and documentation before multi-instance deployment.
- MCP governance v1 is intentionally single-tenant and must not be exposed as a multi-tenant service without tenant-partitioned persistence.

## License

MIT. See [LICENSE](LICENSE).


## Research-ranked safety workflows (13.4.0)

The validated market research prioritized three workflows because users repeatedly struggle with surprise agent bills, provider configuration failures, and operational ambiguity:

1. **Runaway Cost Firewall:** use `POST /v1/console/runaway/evaluate` to fail closed before an agent exceeds cost, token, tool-call, depth, time, retry, or emergency-stop boundaries.
2. **Provider Compatibility Lab:** open **Safety** or call `POST /v1/console/compatibility/{provider_id}/run`. The gateway uses the stored encrypted connection to execute non-destructive authentication, model discovery, chat, streaming, tool, structured-output, and embedding checks. Measured history is available from `GET /v1/console/compatibility/{provider_id}/history`. The older `/evaluate` endpoint is only for importing externally measured offline evidence.
3. **Explain-and-Fix Incident Timeline:** select a real recent routing decision in **Safety** or call `GET /v1/console/incidents/from-request/{request_id}`. The response derives route, model, outcome, latency, reason, and cost evidence from the product activity ledger. Manual evidence import remains available for external systems.

The complete Safety UI flow is responsive, keyboard accessible, includes loading/result/error states, and is built into `ui/dist` by `npm run build`. See [Safety Operations API](docs/api/safety-operations-api.md).



## Market-priority workflows (13.5.0)

Research identified three high-value jobs that now have dedicated product workflows:

1. **Production Replay and Change Impact Lab** compares privacy-safe production evidence with a candidate model or configuration and reports semantic similarity, cost, token, latency, tool and safety-policy deltas. Use `POST /v1/console/replay/compare` or open **Safety** in the cockpit.
2. **Agent Runtime Governor 2.0** detects repeated actions, intent drift and unapproved irreversible work before the next agent step. Use `POST /v1/console/governor/evaluate`.
3. **Verified Compatibility and Pricing Catalog** stores measured provider/model capability contracts and exposes a freshness-aware matrix through `POST /v1/console/contracts` and `GET /v1/console/contracts/{provider_id}`.

These workflows address the market-research findings that teams will pay for exact production replay, need enforcement before runaway agent spend occurs, and repeatedly lose time to provider capability and pricing uncertainty. All safety-evidence endpoints are local-only by default.


## OpenTelemetry evidence plane (13.6.0)

The local console now records gateway, model, agent, tool, policy, and budget evidence as tenant-isolated spans and exports OTLP-shaped documents with OpenInference semantic attributes. Sensitive prompt, output, authorization, password, token, and secret fields are redacted before SQLite persistence.

- `POST /v1/console/evidence/spans` records one validated span.
- `GET /v1/console/evidence/traces/{trace_id}?tenant_id=...` exports a portable trace.
- Canonical JSON Lines export is available through the `EvidencePlane` domain service for offline ingestion.

This addresses the market-research priority for telemetry portability and avoids locking operational evidence into a single observability vendor.


## Safe releases and outcome-aware autopilot (13.7.0)

The control plane now turns release and optimization evidence into fail-closed decisions:

- **Verified backup and recovery:** creates consistent SQLite backups, binds them to SHA-256 evidence, runs SQLite integrity checks, and refuses tampered restores.
- **Canary release gate:** requires provenance, backup, migration readiness, and full regression evidence before routing a bounded canary percentage.
- **Measured promote/rollback decision:** rolls back when error rate, p95 latency, or quality crosses a configured guardrail.
- **Outcome-aware autopilot:** recommends the lowest-cost measured candidate only when quality, success rate, and latency floors remain satisfied. It never applies a production change automatically and always requires approval.

Console endpoints are `POST /v1/console/releases/plan`, `POST /v1/console/releases/canary-decision`, and `POST /v1/console/autopilot/recommend`.


## Review remediation release (13.8.0)

- Production Replay now performs an explicit bounded HTTP execution through the fixed local gateway and compares measured candidate output, tokens, latency and estimated cost. Configure `GATEWAY_REPLAY_API_KEY` with an application key before starting the system. The UI requires a privacy-safe prompt, candidate model, prior output and visible cost preflight before execution.
- Live compatibility runs now populate the provider/model contract catalog. The eligibility endpoint fails closed on missing, stale, unsupported, wrong-region or unpriced contracts and returns a route-health score.
- Safe release, optimization and OpenTelemetry evidence controls are interactive cockpit workflows, not links to POST-only endpoints.
- Final distribution must be created with `uv run python scripts/build_release.py <output>`; archive tests reject databases, keys, logs, environments, caches and Node modules.


## Expanded provider catalog (13.9.0)

The connection wizard now includes ready-made, searchable presets for Z.AI, Z.AI Coding Plan, Xiaomi MiMo, Xiaomi MiMo Token Plan, DeepInfra, Together AI, Fireworks AI, Nebius Token Factory, SiliconFlow, Moonshot Kimi, MiniMax, Alibaba Cloud Model Studio, and Volcengine Ark.

For every preset, the official OpenAI-compatible protocol and current documented base URL are filled in automatically. The operator only needs to provide a connection name, slug, region, and the corresponding API or subscription key. Pay-as-you-go and coding-plan credentials are separate presets so a subscription key is not accidentally sent to the billable API endpoint.

See [Provider preset catalog](docs/provider-preset-catalog.md) for endpoint provenance and provider-specific cautions.

## Clean release packaging

Build the cockpit first, then create a publication-safe archive:

```bash
cd ui && npm ci && npm test && npm run build && cd ..
uv run python scripts/build_release.py dist/llm-budget-gateway.zip
```

The builder fails closed when `ui/dist` is missing and excludes virtual environments, Node modules, caches, databases, WAL/SHM files, logs, generated keys, and TypeScript build metadata.
