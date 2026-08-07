## [14.1.0] - 2026-08-07

### Features
- **Integrated intelligence into the proxy path** (formerly the separate Intelligence satellite service):
  - Exact-response cache with canonical keys + TTL (`X-Gateway-Cache: 1` header or `metadata.cache: true` opt-in; stores successful responses, serves identical requests from cache)
  - PII redaction for emails, cards, phones in user messages (`X-Gateway-Redact-Pii: 1` header or `metadata.redact_pii: true` opt-in; local regex, nothing leaves the gateway)
  - Cost-aware routing reorder (`metadata.cost_aware: true` opt-in; reorders eligible candidates by lowest cost among healthy models, falls back to fixed priority)
- **Operations prompt registry + quota classification** folded into cockpit product API (`/v1/product/prompts*`, `/v1/product/quota/classify`)
- **Quality evaluation + release gate + audit report** folded into cockpit product API (`/v1/product/quality/*`)
- **SLO monitor** over last 24h cost records (`/v1/product/slo`)
- **12 standalone satellite services removed** from default startup (control, intelligence, operations, quality, security, resilience, optimization, collaboration, platform, agentops, fleet, assurance) — only the proxy (8000) and cockpit (8013) run by default; `GATEWAY_ENABLE_SATELLITES=1` restores legacy set
- **Cockpit UI gains Intelligence, Prompts, Quality pages** in the nav, wired to the new endpoints with local workflows
- **Gemini thought_signature persistence** across gateway restarts (SQLite `thought_signatures` table, indexed by tool_call id + fn/args, survives restarts so Hermes replays work)
- **Timeout field max increased to 300s** (was 120); global `GATEWAY_PROVIDER_TIMEOUT=300` via systemd drop-in

### Fixes
- **ServiceManager default SERVICES** now only auto-starts the proxy; satellites are opt-in via env
- **Route timeout UI** max=300, backend clamp removed by `provider_timeout=300`
- **SLO endpoint** handles zero-request days gracefully (`no_data` state)
- **PII redaction** available via both cockpit API (GET `/v1/product/intelligence/redact`) and proxy path

### Docs
- Updated README (removed satellite references, added intelligence/proxy integration section)
- CHANGELOG entry

### Tests
- Added `test_intelligence_proxy.py` (cache hit, PII redaction, cost-aware routing) — 3 new tests
- All backend 79 tests pass (gateway_proxy + provider_direct + intelligence_proxy)
- UI build OK, vitest 10/10
- Live smoke: cache miss→hit, cost-aware reorders, SLO healthy, only ports 8000/8013 listening

## [14.0.0] - 2026-08-04

### Features
- Replaced the basic route modal/cards with a searchable operational inventory and full-screen Route Studio.
- Added visual ordered fallback nodes, target inspector, conditions, weighted traffic settings, budget ceilings, retries, timeouts and required capabilities.
- Added isolated draft saving, validation, simulation, publication and immutable version history.
- Added dependency inspection, archive, restore, duplicate and exact-name-confirmed permanent deletion.
- Added responsive and keyboard-accessible route reordering controls.

### Tests
- Added real SQLite lifecycle/version tests and ASGI integration tests for every new route endpoint.
- Added frontend source contracts and production build verification.

### Docs
- Added complete Route Studio workflows, lifecycle semantics and endpoint reference.

[... rest unchanged ...]