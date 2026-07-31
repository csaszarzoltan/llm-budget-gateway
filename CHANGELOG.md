# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
