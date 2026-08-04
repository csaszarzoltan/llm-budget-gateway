## [13.6.0] - 2026-08-04

### Features
- Added a tenant-isolated OpenTelemetry/OpenInference evidence plane for gateway, model, agent, tool, policy, and budget spans.
- Added deterministic OTLP-shaped and JSON Lines exports with local-only console endpoints.
- Added recursive sensitive-field redaction and idempotent SQLite persistence.
- Added the evidence plane to the React cockpit's Advanced workspace.

### Tests
- Added RED-GREEN-REFACTOR unit, boundary, secret-redaction, tenant-isolation, real SQLite I/O, deterministic export, and ASGI integration coverage.
- Completed 99 additional bounded development and verification iterations.

### Docs
- Updated README, API reference, feature manifest, version metadata, and iteration evidence.

## [13.5.0] - 2026-08-04

### Features
- Added a Production Replay and Change Impact Lab for semantic, cost, token, latency, tool and policy comparisons.
- Added Agent Runtime Governor 2.0 loop, intent-drift and irreversible-action approval gates.
- Added a SQLite-backed verified provider/model compatibility and pricing contract catalog with freshness-aware route eligibility.
- Added local-only console APIs and a responsive React Safety flow for replay results.

### Tests
- Added domain boundary, error, safety and real SQLite I/O coverage plus an ASGI integration flow.
- Rebuilt and tested the production React cockpit.

### Docs
- Documented the three research-ranked features, their rationale, endpoints and machine-readable feature manifest.

# Changelog

All notable changes to this project will be documented in this file.

### [13.2.2] - 2026-08-04

#### Fixed
- Added a deliberately bounded 250 px provider picker with an always-visible vertical scrollbar, touch overscroll containment, and accessible list semantics.
- Added a real Custom provider type instead of only documenting it.

#### Features
- Custom provider connections support a configurable model-list path, optional API key, authentication header and prefix, extra headers JSON, models-array field, and model-ID field.
- Custom model discovery normalizes results into alias-prefixed gateway model IDs.

#### Tests
- Added schema, real HTTP discovery, custom authentication/header, model parsing, and scroll-overflow regression coverage.


## [9.4.0] - 2026-08-01

### Added
- Resumable guided-workflow progress with completed-step state, direct navigation, and reset.
- Privacy-safe local usage counters containing event names and counts only.
- Status-specific runner recovery guidance and a bounded browser timeout.
- TDD acceptance coverage and a complete product/requirements/implementation report.
- **MCP server governance** (`mcp_governance` package) — versioned MCP server
  registry with tool inventory, per-tool `allow`/`deny`/`approval` policies
  (deny by default), per-tool soft/hard cost ceilings enforced against the
  shared cost ledger, a PII-redacted audit trail for every call attempt, SSRF
  and PII rules, four-eyes approval gates, and a policy engine
  (`before_call`/`after_call`) for gating tool calls.
- MCP governance REST API and dashboard — `create_mcp_governance_app()`
  serves `/v1/mcp/servers`, `/v1/mcp/policies`, `/v1/mcp/budgets`,
  `/v1/mcp/audit`, `/v1/mcp/approvals`, `/v1/mcp/report`, and the `/mcp`
  dashboard, authenticated with `GATEWAY_MCP_API_KEY` + `X-Tenant-Id`.
- Live tool discovery via the official MCP SDK (`MCPDiscoveryAdapter`,
  `mcp>=1.2,<2` runtime dependency, lazy-imported).

### Changed
- The runner prevents duplicate in-flight submissions and announces busy state.
- README and task-oriented console documentation now describe the 9.4 experience.

### Security
- Workflow progress and counters exclude tenant IDs, credentials, request bodies, prompts, results, and response content.
- MCP governance fails closed at every boundary: no API key → 503, wrong key /
  missing tenant → 401, unknown server/tool → 404, default policy deny,
  retired server and disabled tool blocked, SSRF-unsafe URLs blocked, tool
  arguments JSON-Schema validated (OWASP LLM06), approval-gated calls blocked
  until a human approves (four-eyes).
- MCP audit and approval records persist PII-redacted arguments; error bodies
  never echo internal state; the API key is compared with
  `secrets.compare_digest` (constant time).
- Demo approval data is seeded only when `MCP_GOVERNANCE_SEED_DEMO=1`; v1 is
  single-tenant (S16) — do not mount on a multi-tenant gateway without tenant
  columns and filters.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [9.3.0] - 2026-08-01

### Added
- Safe example input presets and contextual help for every guided workflow step.
- Visible example-data warning and non-automatic submission boundary.
- Unit and UI acceptance tests for preset completeness and sensitive-value safety.

## [9.2.0] - 2026-08-01

### Added
- Accessible guided workflow stepper with previous/next navigation and live progress.
- Additive workflow-detail API with an explicit 404 contract.
- Unit, UI acceptance, and API integration tests for multi-step journeys.

## [9.1.0] - 2026-08-01

### Added
- Task-oriented daily workflows and symptom/error-code search.
- Recent tasks, favorites, and non-secret console context persistence.
- Accessible inline JSON-object validation in the universal runner.
- Additive `GET /v1/console/workflows` endpoint and TDD acceptance coverage.

### Changed
- Updated console information architecture to prioritize frequent user jobs while preserving expert workspace access.

### Security
- Preferences exclude bearer keys, request bodies, responses, prompts, and secrets.

## [0.1.0] — 2026-07-31

### Features
- **OpenAI-compatible proxy core** (`src/llm_budget_gateway/gateway_proxy.py`,
  `main.py`) — `POST /v1/chat/completions`, `POST /v1/completions`,
  `POST /v1/embeddings`, `GET /v1/models`, `GET /health`; request lifecycle:
  auth → scope resolution → sync enforce → forward → cost record
- **Virtual key auth** — static key table (`GATEWAY_VIRTUAL_KEYS`,
  api_key → key id); unknown/missing key → `401`
- **Sync TPM/RPM ceilings** (`budget_enforcement.py`) — atomic counters
  checked pre-dispatch via `InMemoryCounterStore`; ceiling hit → `429`
- **Async dollar budgets** — per-scope `soft_limit` (alert only) and
  `hard_limit` (reject with `412`, Portkey convention) over rolling
  windows (`30s`/`30m`/`30h`/`30d`/`daily`/`monthly`), composite
  hierarchical scopes (`global > team > user > key`, all checked)
- **YAML budget configuration** — `budgets.yaml` loader with validation
  (`ValueError` on malformed YAML / unknown scope kind)
- **Cost tracking** (`cost_tracking.py`) — token × price math
  (`PriceMap` with litellm baseline + `GATEWAY_PRICING_OVERRIDES`,
  `CostCalculator`), SQLite WAL ledger (`CostStore`) with
  `cost_records` table + spend queries per scope
- **Automatic model fallback** (`model_fallback.py`) — typed chains
  (`GATEWAY_FALLBACK_CONFIGS`), error classification
  (rate_limit/timeout/server_error/content_policy/context_window),
  cooldowns, context pre-checks, `disable_fallbacks`; serving model
  reported and billed
- **Provider timeouts** — `GATEWAY_PROVIDER_TIMEOUT` bounds every
  upstream call and each stream chunk; hung upstream → clean `502`
- **SSE streaming** — `stream=true` responses drained, usage aggregated
  (`accumulate_usage`) and recorded at real cost, re-framed as OpenAI
  SSE (`data: <json>` + `data: [DONE]`)
- **Embeddings routing** — `input` bodies route to `litellm.aembedding`

### Fixes
- **Security hardening (review round 1)** — client body allow-list closes
  `api_key`/`api_base`/`base_url`/`headers` injection (SSRF +
  cost-bypass); fallback dispatch actually invokes chain candidates;
  stream usage aggregation kills `$0` cost records
- **Security hardening (review round 2)** — SSE serialization at the
  HTTP layer (raw chunk objects no longer crash `StreamingResponse`);
  per-chunk stream timeout; embeddings routed to `aembedding`; full
  virtual key redacted in auth-failure logs
- **Boundary input validation (review round 3)** — malformed JSON /
  non-object request bodies now map to `400` instead of an unhandled
  `500` (or a misleading `404`); `stream`/`stream_options` stripped from
  embeddings requests (aembedding has no stream support → upstream 502)
- **Test-double leak prevention** — `401`/`502` bodies never contain the
  submitted API key or raw provider exception text (regression-pinned)
- **Counter memory bound** — `InMemoryCounterStore` window buckets are
  LRU-pruned past 10,000 entries

### Tests
- 192 pre-dev tests (82 gateway proxy + cost tracking, 110 budget
  enforcement + model fallback) written before implementation (TDD RED)
- 213 tests green at review approval; **220 passing, 0 failed** at
  release — ruff clean on `src/`, `tests/`, and `examples/`

### Docs
- Rewrote `README.md` — features table, badges, quick start, HTTP
  semantics table, docs/examples index, roadmap
- New `docs/index.md` — architecture diagram, module map, examples index
- New `docs/getting-started.md` — install, configure, run, first request
- New `docs/proxy-setup.md` — endpoints, env reference, request
  lifecycle, HTTP semantics, streaming/embeddings, security model
- New `docs/cost-tracking.md` — pricing, ledger schema, cost math,
  spend queries, privacy
- New `docs/budget-configuration.md` — scopes, YAML shape, windows,
  sync/async enforcement model (incl. documented async overshoot
  exposure), HTTP mapping
- New `docs/fallback-chains.md` — config, error classification,
  dispatch flow, cooldowns, streaming caveat
- New runnable examples: `examples/quickstart.py` (full HTTP surface
  against a fake provider), `examples/cost_tracking.py`,
  `examples/budget_enforcement.py`, `examples/fallback_chains.py`
- New `.env.example` — full environment reference

### [0.2.0] - 2026-07-31

#### Added
- Accessible responsive Control Center and versioned tenant-aware admin API.
- Hashed virtual-key issue, rotate, revoke, expiry, model scopes, RBAC and audit.
- Atomic budget reservations with idempotent reconciliation and spend exports/alerts.
- Fail-closed governance policy decisions without prompt retention.
- Health-aware deterministic routing, circuit breakers, recovery and TTL cache.

### [0.3.0] - 2026-07-31
- Added six complete responsive product UI page contracts, accessible state patterns, permission-aware actions, guided setup progress, spend forecasting and operational recovery views.

### [0.4.0] - 2026-07-31
- Added approval-gated automation and reliability recommendations.
- Added tenant identity membership authorization and deterministic evidence exports.
- Added explainable spend anomaly forecasts and privacy retention/residency enforcement.

### [0.5.0] - 2026-07-31
- Added four-eyes approval orchestration and continuous compliance evidence freshness.
- Added SCIM lifecycle, access reviews, quality-aware routing, privacy request cases, and governed agent tool runs.

## [0.6.0] - 2026-07-31

### Added
- Authenticated `POST /v1/cost-estimates` preflight API with explicit unknown-pricing state.
- Unit tests, user guide, and market-research decision record.


## [0.7.0] - 2026-07-31

### Added
- Local PII redaction API with category-safe audit metadata.
- Tenant-isolated exact response cache with canonical keys and TTL expiry.
- HMAC-SHA256 signed webhook envelopes and constant-time verification.
- Explainable spend anomaly detection and constrained cost-aware routing.
- Responsive light/dark Gateway Intelligence dashboard with accessible states.
- Multi-source research, five ADRs, OpenAPI guide, FAQ, migration, validation, GitLab, and contribution documentation.

### Changed
- Development dependencies now include pytest-cov and the package version is 0.7.0.

### Security
- PII processing remains local; cache records are tenant-scoped; signed events are tamper-evident.


## [0.8.0] - 2026-07-31

### Added
- Immutable tenant-isolated prompt registry with deterministic A/B assignment.
- Bounded full-jitter retry decisions with attempt, delay and elapsed-time ceilings.
- Actionable quota diagnostics for financial, token, request and availability failures.
- Validated model catalog metadata for pricing, context, capabilities and regions.
- SLO availability and error-budget burn monitoring.
- Authenticated Operations API and responsive light/dark WCAG-oriented dashboard.
- Market research, specifications, five ADRs, OpenAPI guide, migration and GitLab delivery record.

### Changed
- Package version increased to 0.8.0.

### Security
- Operations endpoints fail closed when `GATEWAY_OPERATIONS_API_KEY` is absent.
- Prompt metadata removes secret and authorization fields before persistence.


## [0.9.0] - 2026-07-31

### Added
- Deterministic rule-based evaluation runs with tenant-isolated immutable history.
- CI-ready release quality and regression gates.
- Privacy-safe trace and session identifier resolution.
- Single-model batch manifest validation and discounted cost planning.
- Schema-versioned, redacted, integrity-protected audit reports.
- Authenticated Quality API and responsive light/dark accessible dashboard.
- Research, feature specifications, five ADRs, API, migration and GitLab documentation.

### Changed
- Package version increased to 0.9.0.

### Security
- Quality endpoints fail closed when `GATEWAY_EVALUATION_API_KEY` is absent.
- Audit exports strip prompt, authorization and secret fields and redact embedded keys.


## [1.0.0] - 2026-07-31

### Added
- Local secret detection and redaction.
- Durable webhook replay protection.
- Fail-closed provider compliance.
- Change risk scoring and security posture grades.
- Authenticated responsive Security Center.

### Security
- Endpoints fail closed without an API key; secret values are not persisted.


## [1.1.0] - 2026-07-31
### Added
- Adaptive concurrency, dead-letter replay, maintenance windows, config doctor, incident timelines and accessible Resilience Center.
### Security
- APIs fail closed without `GATEWAY_RESILIENCE_API_KEY`; dead letters remove sensitive fields.


## [1.2.0] - 2026-07-31
### Added
- Prompt compression, savings attribution, privacy-aware cache advice, budget forecasting, quality-safe optimization experiments, and accessible Optimization Center.
### Security
- APIs fail closed without `GATEWAY_OPTIMIZATION_API_KEY`.


## [1.3.0] - 2026-07-31
### Added
- Project-scoped RBAC, one-time invitations, key lifecycle guidance, member budget/key caps, delegated approvals, and accessible Collaboration Center.
### Security
- APIs fail closed without `GATEWAY_COLLABORATION_API_KEY`; invitation plaintext is never persisted.


## [2.0.0] - 2026-07-31

### Added
- Twenty Platform Center capabilities covering catalogs, tags, allocation, quotas, alerts, SLOs, incidents, retention, DLP, routing, scorecards, canaries, rollback, feedback, drift, datasets, integrity, contracts, and adoption.
- Authenticated generic Platform API and responsive accessible light/dark dashboard.
- Twenty ADRs, research, roadmap, validation, migration, API, and GitLab documentation.

### Security
- Platform endpoints fail closed without `GATEWAY_PLATFORM_API_KEY`.
- DLP does not return detected secret values and export manifests reject unsafe paths.


## [3.0.0] - 2026-07-31
### Added
- Twenty AgentOps capabilities, authenticated API, and accessible responsive dashboard.
- Research, roadmap, validation, migration, API, GitLab, and twenty ADR documents.
### Security
- Replay verification, tool policy, redaction, injection scoring, approval gates, audit chains, and residency enforcement.


## [5.0.0] - 2026-07-31
### Added
- Twenty assurance capabilities, authenticated API, responsive accessible dashboard, research, roadmap, validation, migration, API, GitLab, and ADR documentation.

### [6.0.0] - 2026-07-31

#### Added
- Ten deterministic Delivery Center capabilities, authenticated API, tests, user guide, API guide, and migration guide.

#### Security
- Calls fail closed without `GATEWAY_DELIVERY_API_KEY`; environment checks return names only.

### [7.0.0] - 2026-07-31

#### Added
- Ten Scale Center capabilities, fail-closed tenant-authenticated API, unit/API tests, user guide, API guide, and migration guide.

#### Security
- Scale endpoints fail closed without `GATEWAY_SCALE_API_KEY`; tenant shard results use SHA-256 fingerprints instead of plaintext tenant identifiers.

### [7.1.0] - 2026-07-31

#### Added
- Unified responsive browser console covering all 15 workspaces and registered capabilities.
- Global search, command palette, workspace filters, health checks, light/dark themes and a universal API runner with cURL generation.
- Machine-readable console catalog, accessibility contracts, UI/API tests and user documentation.

#### Security
- The console embeds no credentials or third-party assets; bearer keys are kept in browser session storage only.

### [8.0.0] - 2026-07-31

#### Added
- Ten Activation Center capabilities, authenticated API, tests, user guide, API guide, and migration guide.

#### Security
- Credential readiness exposes names only; diagnostic bundles drop unapproved fields; API fails closed without a server key.

### [9.0.0] - 2026-07-31

#### Added
- Ten Product Adoption Center capabilities, authenticated API, tests, user guide, API guide, and migration guide.

#### Security
- Feedback accepts bounded categories only; reports contain aggregate metrics and deterministic hashes; API fails closed without a key.

## [9.5.0] - 2026-08-04

### Features
- Added a responsive React 19 and TypeScript AI Operations Cockpit that unifies spend, quality, incidents, approvals, policy coverage, and recommended actions.
- Added an explainable Agent Runaway Firewall with cost, token, tool-call, delegation-depth, elapsed-time, retry, and emergency-stop gates.
- Added SQLite run reservation and reconciliation for real local persistence.
- Added JSON-Schema-generated guided form metadata with sensitive-field handling.
- Added `/v1/console/cockpit/summary`, `/v1/console/runaway/evaluate`, `/v1/console/forms/generate`, and `/cockpit`.

### Tests
- Added research-priority unit, boundary, error, SQLite I/O integration, HTTP integration, and production UI-serving coverage.

### Docs
- Updated README, API documentation, and the machine-readable `FEATURES-DONE.md` manifest.

## [9.6.0] - 2026-08-04

### Features
- Added tenant-isolated nested agent traces with parent-child spans, duration, status, tool/model context, and cost attribution.
- Added privacy-safe trace ingestion that removes prompt, response, authorization, secret, and API-key metadata.
- Added cost-to-outcome analytics across feature, project, model, and tool with cost per success and quality-weighted cost.
- Added `POST /v1/console/traces`, `GET /v1/console/traces/{run_id}`, and `POST /v1/console/outcomes/summary`.

### Tests
- Added SQLite persistence, tenant isolation, graph integrity, duplicate, missing-parent, validation, analytics, and real HTTP integration tests.

### Docs
- Updated README, priority API reference, frontend navigation, and `FEATURES-DONE.md` for the completed P1 research priorities.

## [9.7.0] - 2026-08-04

### Features
- Added deterministic CycloneDX-compatible SBOM generation for exactly pinned Python and npm dependencies.
- Added SHA-256-bound in-toto/SLSA-style provenance creation and offline artifact verification.
- Added a dependency upgrade-risk gate for major, minor, patch, removed, unpinned, and security-advisory changes.
- Added `GET /v1/console/supply-chain/sbom` and `POST /v1/console/supply-chain/upgrade-risk`.
- Added supply-chain navigation and positioning to the React cockpit.

### Tests
- Added deterministic SBOM, pin enforcement, artifact tamper, unsafe path, semantic-version boundary, advisory, and real HTTP integration coverage.

### Docs
- Updated README, API reference, frontend, and the machine-readable feature manifest.

## [10.0.0] - 2026-08-04

### Features
- Added strict tenant-keyed persistence with canonical migration exports.
- Added transactional, idempotent shared counters suitable for a shared SQL adapter and hard limit enforcement.
- Added fail-closed SQLite-to-Postgres migration readiness and zero-data-loss execution steps.
- Added signed reverse-proxy SSO claim verification with tenant, expiry, and role enforcement.
- Added an explainable policy and routing simulator covering budget, residency, tools, health, quality, and cost.
- Added a graphical trace preview and live simulator flow to the React cockpit.
- Added `/v1/console/simulate` and `/v1/console/production/migration-readiness`.

### Tests
- Added tenant isolation, canonical export, atomic reservation, idempotency, migration evidence, identity signature, expiration, role, route-selection, rejection, and real HTTP integration tests.

### Docs
- Declared completion of every feature recommended by the market research roadmap and updated the API reference and machine-readable manifest.

## [10.1.0] - 2026-08-04

### Features
- Added tenant-scoped `GET /v1/console/traces` run summaries.
- Added a production React trace-explorer flow with tenant input, loading, empty, result, and error states.
- Added deep links from run summaries to nested trace evidence.

### Tests
- Added real SQLite and ASGI integration coverage for run ordering, tenant isolation, nested trace retrieval, missing tenant validation, and production UI serving.

### Docs
- Updated README, API documentation, frontend version, and the machine-readable feature manifest.

## [12.0.0] - 2026-08-04

### Features
- Rebuilt the complete product experience around Home, Applications, Routes, Providers, Activity, and Usage.
- Added a role-adaptive, state-derived home dashboard with gateway endpoint, activation checklist, attention feed, traffic KPIs, active routes, and explainable recent decisions.
- Added application, provider/model catalog, route template, immutable route version, route testing, publish, activity, and cost aggregation product APIs.
- Added developer, operator, FinOps, and security home emphasis.
- Moved traces, policies, security, services, supply chain, and raw API access under Advanced.
- Added full responsive light and dark visual systems with mobile navigation and actionable empty states.

### Tests
- Added real SQLite and ASGI integration coverage for the complete product activation flow, provider health, route templates, timezone selection, versions, role views, activity, and usage.

### Docs
- Added GUI architecture and user-flow documentation, updated README, API documentation, version metadata, and FEATURES-DONE.md.

## [13.0.0] - 2026-08-04

### Features
- Added application key rotation and revocation.
- Added scoped budget headroom and spend accounting.
- Added alert rules for cost, error rate, latency, and fallback rate.
- Added a multi-environment registry and default environment selection.
- Added role-scoped saved views.
- Added provider health and latency evidence.
- Added route snapshots and rollback.
- Added non-destructive resource archival.
- Added non-secret configuration export and validated import.
- Added actionable recommendations and audit logging.
- Exposed every capability through product APIs and Advanced console links.

### Tests
- Added one focused test per autonomous iteration, validation edge coverage, and an end-to-end ASGI contract test spanning the extension API.

## [13.1.0] - 2026-08-04

### Features
- Added the `gateway-system` one-command launcher.
- Made the product cockpit the default landing page.
- Automatically starts all registered local gateway services and waits for their existing readiness checks.
- Opens the cockpit in the default browser for interactive local startup.
- Added a headless factory entry point and `/v1/system/status` endpoint.
- Keeps the expert console available at `/console` without requiring it for routine startup.
- Stops all child services owned by the launcher during graceful shutdown.

### Tests
- Added cockpit redirect, automatic service lifecycle, startup plan, readiness status, and partial-failure regression tests.

## [13.2.0] - 2026-08-04

### Features
- Added named provider-account connections with one encrypted credential set per connection.
- Added provider-adaptive schemas for OpenAI, Anthropic, Gemini, Azure OpenAI, OpenAI-compatible endpoints and Vertex AI.
- Added a four-step provider wizard: choose provider, enter connection details, save securely, verify and download models.
- Added provider-native model discovery and normalized model catalog storage.
- Added alias-prefixed gateway model IDs and route model selection from the live discovered catalog.
- Added persistent provider storage to cockpit-first startup.
- Added model resynchronization, masked credential state, last-sync information and actionable provider errors.

### Security
- Provider secrets are encrypted with AES-256-GCM and a dedicated local master key with restrictive file permissions.
- Provider APIs never return plaintext secrets and configuration export remains secret-free.

### Tests
- Added duplicate provider-type aliases, adaptive schemas, encrypted round-trip, OpenAI-compatible, Anthropic and Gemini discovery, authentication errors, HTTP wizard flow, home activation and global model catalog coverage.

## [13.2.3] - 2026-08-04

### Fixed
- Restored logical routing on the OpenAI-compatible data plane, including application-key authentication, published alias resolution, transient-status fallback, decision headers, and model-spend attribution.
- Restored logical and priority route administration APIs and their injectable SQLite stores in the unified console factory.
- Made console theme selection apply before first paint with system dark-mode fallback and explicit pressed state.
- Made the default service manager resolve the repository root independently of the caller working directory.
- Prevented default in-memory console construction from creating a provider master key inside the repository.
- Replaced the hotfix-only root README with complete install, run, test, UI, and security instructions.
- Removed runtime databases, encryption keys, WAL/SHM files, generated metadata, and hotfix backup files from the release package.
- Added frontend source-contract tests and ensured the production cockpit bundle is included.

### Validation
- Python: 898 passed.
- Frontend: 3 passed.
- Ruff: clean.
- Vite production build: successful.
- Twenty-three autonomous targeted, regression, build, smoke, and hygiene iterations recorded in `fix-iterations.md`.

## [13.3.0] - 2026-08-04

### Features
- Added a Provider Compatibility Lab that scores unique capability probes and produces specific repair actions for authentication, discovery, streaming, tools, structured output, embeddings, and vision.
- Added a privacy-safe Explain-and-Fix Incident Timeline with ordered SQLite evidence, impact, root explanation, and outcome-specific remediation.
- Promoted the existing Runaway Cost Firewall into a dedicated Safety workflow alongside provider readiness and incident repair.
- Added three versioned console endpoints for compatibility evaluation and incident event/explanation flows.

### Tests
- Added TDD unit, boundary, error, secret-redaction, OpenAPI, real SQLite persistence, and ASGI integration coverage.
- Added a frontend source contract for the complete Safety user journey and rebuilt the production Vite bundle.

### Docs
- Updated README, API reference, changelog, and the machine-readable feature manifest with the research pain points and implemented differentiators.

## [13.3.1] - 2026-08-04

### Features
- Added SQLite-backed provider compatibility run history with bounded provider-scoped retrieval.
- Added `GET /v1/console/compatibility/{provider_id}/history` and persisted every successful compatibility evaluation.

### Tests
- Added RED-GREEN-REFACTOR coverage for real SQLite history persistence, validation boundaries, and ASGI history retrieval.
- Completed 100 bounded autonomous verification iterations spanning tests, lint, OpenAPI, integrity, documentation, and packaging checks.

### Docs
- Updated the README, Safety Operations API, and `FEATURES-DONE.md` to document compatibility trends.

## [13.4.0] - 2026-08-04

### Features
- Replaced the demonstration compatibility flow with live, non-destructive checks against stored provider connections for authentication, discovery, chat, streaming, tools, structured output, and embeddings.
- Added request-derived incident explanations using real product activity evidence for route, model, outcome, latency, reason, and cost.
- Added an interactive Safety workspace for measured provider selection, real request selection, firewall decisions, emergency-stop previews, results, and repair actions.
- Added a fail-closed clean-release builder and an MIT license.

### Security and Fixes
- Restricted compatibility and incident evidence workflows to local callers.
- Fixed authentication failure classification so probe ordering cannot downgrade a blocking failure.
- Expanded incident redaction for token aliases, API-key aliases, AWS access keys, JWTs, and nested values.
- Included the production cockpit bundle and excluded runtime databases, WAL/SHM files, encryption keys, logs, caches, build metadata, virtual environments, and Node modules from release archives.

### Tests
- Added real HTTP transport integration tests for provider compatibility using stored encrypted credentials.
- Added real SQLite/product-activity integration tests for request-derived incidents.
- Added a jsdom-rendered React navigation/accessibility smoke test instead of relying only on source-text assertions.
- Added fail-closed release packaging tests.
