# LLM Budget Gateway API Reference

> **Version 14.2.0** — Covers all endpoints across three API surfaces: Gateway Proxy (port 8000),
> Admin Control API (control_api.py), and Product Console API (port 8013).

---

## Table of Contents

- [Authentication](#authentication)
- [1. Gateway Proxy (port 8000) — 9 endpoints](#1-gateway-proxy-port-8000)
  - [POST /v1/chat/completions](#post-v1chatcompletions)
  - [POST /v1/completions](#post-v1completions)
  - [POST /v1/embeddings](#post-v1embeddings)
  - [POST /v1/cost-estimates](#post-v1cost-estimates)
  - [GET /v1/observability/requests](#get-v1observabilityrequests)
  - [GET /v1/observability/summary](#get-v1observabilitysummary)
  - [GET /v1/models](#get-v1models)
  - [GET /v1/models/{model}/capabilities](#get-v1modelsmodelcapabilities)
  - [GET /health](#get-health)
- [2. Admin Control API (control_api.py) — 7 endpoints](#2-admin-control-api)
- [3. Product Console API (port 8013)](#3-product-console-api)
  - [3.1 System & Console Core (10)](#31-system--console-core)
  - [3.2 Safety & Governance (13)](#32-safety--governance)
  - [3.3 Observability & Tracing (11)](#33-observability--tracing)
  - [3.4 Supply Chain (2)](#34-supply-chain)
  - [3.5 Service Management (5)](#35-service-management)
  - [3.6 Product — Applications & Home (4)](#36-product--applications--home)
  - [3.7 Product — Routes (17)](#37-product--routes)
  - [3.8 Product — Providers (10)](#38-product--providers)
  - [3.9 Product — Customers & Usage (9)](#39-product--customers--usage)
  - [3.10 Product — Intelligence (6)](#310-product--intelligence)
  - [3.11 Product — Prompts & Quality (10)](#311-product--prompts--quality)
  - [3.12 Product — Data Import/Export (4)](#312-product--data-importexport)
  - [3.13 Product — Alerts, Keys, Budgets (5)](#313-product--alerts-keys-budgets)
  - [3.14 Product — Environments, Views, SLO (5)](#314-product--environments-views-slo)
  - [3.15 Admin Routes & Priority Routes (16)](#315-admin-routes--priority-routes)
  - [3.16 Alert Rules API (5)](#316-alert-rules-api)

---

## Authentication

| API Surface | Auth Method | Details |
|---|---|---|
| **Gateway Proxy** | `Authorization: Bearer <key>` | Virtual API key or application key (`gw_...`). Resolves budget scope, provider routing, and fallback chains. |
| **Admin Control API** | `X-Tenant-Id` + `X-Role` headers | Required on every request. `X-Role` must be `admin` for write operations. |
| **Product Console API** | Bearer token (product API) or `X-Console-Action: 1` (service management) | Product endpoints use the same bearer key as the gateway. Service management and safety evidence endpoints require `X-Console-Action: 1` and are restricted to `127.0.0.1` / `::1`. |

---

## 1. Gateway Proxy (port 8000)

The OpenAI-compatible proxy. All request bodies are JSON. Streaming responses use SSE (`text/event-stream`).

### POST /v1/chat/completions

OpenAI-compatible chat completions with budget enforcement, provider routing, fallback chains, and optional streaming.

**Headers:**
```
Authorization: Bearer <key>              # required
X-Gateway-Cache: 1                       # optional: exact-response cache
X-Gateway-Redact-Pii: 1                  # optional: PII redaction
X-Gateway-Cancel-On-Disconnect: 1        # optional: cancel upstream on client disconnect
```

**Request:**
```json
{
  "model": "hermes-default",
  "messages": [
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "Hello"}
  ],
  "stream": false,
  "temperature": 0.7,
  "max_tokens": 1024
}
```

**Response (200):**
```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "model": "hermes-default",
  "choices": [
    {"index": 0, "message": {"role": "assistant", "content": "Hi there!"}, "finish_reason": "stop"}
  ],
  "usage": {"prompt_tokens": 25, "completion_tokens": 5, "total_tokens": 30}
}
```

**Error (4xx/5xx):** OpenAI-format error object with `error.message`, `error.type`, `error.code`.

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer gw_abc123" \
  -H "Content-Type: application/json" \
  -d '{"model":"hermes-default","messages":[{"role":"user","content":"Hello"}]}'
```

### POST /v1/completions

OpenAI-compatible text completions (non-chat). Same auth and error format as chat completions.

**Request:**
```json
{
  "model": "hermes-default",
  "prompt": "The capital of France is",
  "max_tokens": 100
}
```

```bash
curl -X POST http://localhost:8000/v1/completions \
  -H "Authorization: Bearer gw_abc123" \
  -H "Content-Type: application/json" \
  -d '{"model":"hermes-default","prompt":"The capital of France is"}'
```

### POST /v1/embeddings

OpenAI-compatible embeddings.

**Request:**
```json
{
  "model": "text-embedding-3-small",
  "input": "The food was delicious and the waiter..."
}
```

**Response (200):**
```json
{
  "object": "list",
  "data": [{"object": "embedding", "embedding": [0.0023, -0.0091, ...], "index": 0}],
  "model": "text-embedding-3-small",
  "usage": {"prompt_tokens": 8, "total_tokens": 8}
}
```

```bash
curl -X POST http://localhost:8000/v1/embeddings \
  -H "Authorization: Bearer gw_abc123" \
  -H "Content-Type: application/json" \
  -d '{"model":"text-embedding-3-small","input":"Hello world"}'
```

### POST /v1/cost-estimates

Preflight cost estimation. Returns estimated cost without executing the request. Requires valid API key and known model.

**Request:**
```json
{
  "model": "hermes-default",
  "messages": [{"role": "user", "content": "Hello"}],
  "max_tokens": 1024
}
```

**Response (200):**
```json
{
  "model": "hermes-default",
  "estimated_cost_usd": 0.00012,
  "input_tokens": 10,
  "output_tokens": 50,
  "provider": "anthropic"
}
```

```bash
curl -X POST http://localhost:8000/v1/cost-estimates \
  -H "Authorization: Bearer gw_abc123" \
  -H "Content-Type: application/json" \
  -d '{"model":"hermes-default","messages":[{"role":"user","content":"Hello"}],"max_tokens":1024}'
```

### GET /v1/observability/requests

Query LLM request telemetry. Returns recorded entries with provider, model, token usage, cost, latency, trace_id, and status.

**Query Parameters:** `limit` (int, default 200), `model` (str), `status` (success|error|timeout|rate_limited), `provider` (litellm|direct), `since` (epoch seconds), `trace_id` (single-trace lookup).

**Response (200):**
```json
{
  "requests": [
    {
      "trace_id": "abc123", "model": "hermes-default", "provider": "direct",
      "status": "success", "prompt_tokens": 25, "completion_tokens": 10,
      "cost_usd": 0.00015, "latency_ms": 1200, "timestamp": 1692000000
    }
  ],
  "total": 1
}
```

```bash
curl http://localhost:8000/v1/observability/requests?limit=50\&status=error
```

### GET /v1/observability/summary

Aggregate telemetry summary (request count, token totals, cost).

**Query Parameters:** `since` (epoch seconds), `model` (str).

**Response (200):**
```json
{
  "total_requests": 1520, "total_tokens": 485000, "total_cost_usd": 12.34,
  "by_model": {"hermes-default": {"requests": 800, "tokens": 250000, "cost_usd": 8.50}},
  "by_status": {"success": 1480, "error": 40}
}
```

```bash
curl "http://localhost:8000/v1/observability/summary?since=1691900000"
```

### GET /v1/models

List available models (UI-managed route names, not raw provider models). Excludes archived routes.

**Response (200):**
```json
{
  "object": "list",
  "data": [
    {"id": "hermes-default", "object": "model", "context_length": 128000},
    {"id": "hermes-planner", "object": "model", "context_length": 64000}
  ]
}
```

```bash
curl http://localhost:8000/v1/models
```

### GET /v1/models/{model}/capabilities

Per-route model capabilities for client auto-configuration (context length, thinking, vision, tool calls, streaming, response cache, multi-target fallback).

**Response (200):**
```json
{
  "id": "hermes-default",
  "object": "model.capabilities",
  "context_length": 128000,
  "target_count": 2,
  "fallback": true,
  "streaming": true,
  "tool_calls": true,
  "thinking": true,
  "vision": false,
  "embeddings": false,
  "response_cache": true
}
```

```bash
curl http://localhost:8000/v1/models/hermes-default/capabilities
```

### GET /health

Health check endpoint.

**Response (200):**
```json
{"status": "ok"}
```

```bash
curl http://localhost:8000/health
```

---

## 2. Admin Control API

Tenant-safe control center for workspace configuration, key management, budget policies, and spend export.

**Required Headers (all endpoints):**
```
X-Tenant-Id: <tenant-id>    # required
X-Role: admin               # required (admin for writes)
```

### GET /control

HTML control center UI. Returns a self-contained HTML page with inline CSS/JS.

```bash
curl http://localhost:8013/control
```

### GET /v1/admin/dashboard

Admin dashboard summary for the given tenant.

**Response (200):**
```json
{
  "workspace": {"name": "default"},
  "key_count": 3,
  "budget_scopes": {"global": {"limit": 100.0, "used": 12.50}},
  "recent_spend": [...]
}
```

```bash
curl -H "X-Tenant-Id: acme" -H "X-Role: admin" \
  http://localhost:8013/v1/admin/dashboard
```

### POST /v1/admin/workspace

Configure the workspace name.

**Request:**
```json
{"name": "acme-production"}
```

**Response (200):** `{"status": "active"}`

```bash
curl -X POST -H "X-Tenant-Id: acme" -H "X-Role: admin" \
  -H "Content-Type: application/json" \
  -d '{"name":"acme-production"}' \
  http://localhost:8013/v1/admin/workspace
```

### POST /v1/admin/keys

Issue a new virtual API key. Returns the key once — it is stored only as a hash.

**Request:**
```json
{
  "label": "dev-team",
  "models": ["hermes-default", "hermes-planner"],
  "expires_at": "2026-12-31T23:59:59Z"
}
```

**Headers:** Optional `Idempotency-Key` for safe retries.

**Response (201):**
```json
{
  "key_id": "gw_abc123def456",
  "label": "dev-team",
  "models": ["hermes-default", "hermes-planner"],
  "expires_at": "2026-12-31T23:59:59Z",
  "created_at": 1692000000
}
```

```bash
curl -X POST -H "X-Tenant-Id: acme" -H "X-Role: admin" \
  -H "Content-Type: application/json" \
  -d '{"label":"dev-team","models":["hermes-default"]}' \
  http://localhost:8013/v1/admin/keys
```

### GET /v1/admin/keys

List all keys for the tenant (hashed, no secret material).

**Response (200):**
```json
{"items": [{"key_id": "gw_abc...", "label": "dev-team", "models": [...], "expires_at": "..."}]}
```

```bash
curl -H "X-Tenant-Id: acme" -H "X-Role: admin" http://localhost:8013/v1/admin/keys
```

### PUT /v1/admin/budgets/{scope}

Set a budget limit for a scope (e.g., `global`, `model:hermes-default`, `customer:acme`).

**Request:**
```json
{"limit": 50.0}
```

**Response (200):** Budget status object.

```bash
curl -X PUT -H "X-Tenant-Id: acme" -H "X-Role: admin" \
  -H "Content-Type: application/json" \
  -d '{"limit":50.0}' \
  http://localhost:8013/v1/admin/budgets/global
```

### GET /v1/admin/spend.csv

Export spend data as CSV (plaintext, `text/csv`).

**Response:** CSV with columns: tenant, model, tokens, cost, timestamp.

```bash
curl -H "X-Tenant-Id: acme" -H "X-Role: admin" \
  http://localhost:8013/v1/admin/spend.csv
```

---

## 3. Product Console API

The unified product cockpit on port 8013. Most endpoints are called from the React UI.
**Auth:** Bearer token for product endpoints. Service management and safety evidence endpoints require `X-Console-Action: 1` header and must be called from `127.0.0.1` or `::1`.

---

### 3.1 System & Console Core

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check → `{"status": "ok"}` |
| GET | `/` | Console root (redirects to `/cockpit` when cockpit_first, else renders HTML) |
| GET | `/console` | Managed console HTML page |
| GET | `/cockpit` | Production React AI Operations Cockpit (`index.html`) |
| GET | `/v1/system/status` | System status: service states, cockpit availability, failures |
| GET | `/v1/console/catalog` | Console capability catalog (centers, capability count) |
| GET | `/v1/console/workflows?q=` | Search task-oriented workflows (optional `q` filter) |
| GET | `/v1/console/workflows/{id}` | Get one workflow by stable ID |
| POST | `/v1/console/cockpit/summary` | Combine spend, quality, operations, governance into one summary |
| POST | `/v1/console/alerts/evaluate` | Evaluate all alert rules for a tenant and fire triggered ones |

```bash
# System status
curl http://localhost:8013/v1/system/status

# Console catalog
curl http://localhost:8013/v1/console/catalog

# Cockpit summary
curl -X POST http://localhost:8013/v1/console/cockpit/summary \
  -H "Content-Type: application/json" \
  -d '{"spend":{},"quality":{},"operations":{},"governance":{}}'

# Evaluate alerts
curl -X POST http://localhost:8013/v1/console/alerts/evaluate \
  -H "Content-Type: application/json" \
  -d '{"tenant":"default"}'
```

---

### 3.2 Safety & Governance

All safety endpoints require `127.0.0.1` / `::1` origin (local-only).

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/console/runaway/evaluate` | Runaway cost firewall: check if agent run may execute next step |
| POST | `/v1/console/releases/plan` | Validate provenance, backup, migration, regression, canary gates |
| POST | `/v1/console/releases/canary-decision` | Promote or roll back a canary from measured guardrails |
| POST | `/v1/console/autopilot/recommend` | Recommend bounded route improvement without mutating production |
| POST | `/v1/console/contracts` | Persist one measured provider/model capability contract |
| GET | `/v1/console/contracts/{provider_id}` | Return provider's fresh compatibility and pricing evidence |
| POST | `/v1/console/forms/generate` | Generate accessible control metadata from JSON Schema |
| POST | `/v1/console/compatibility/evaluate` | Import externally measured probes for offline scoring |
| POST | `/v1/console/compatibility/{provider_id}/run` | Execute non-destructive checks against a stored provider |
| GET | `/v1/console/compatibility/{provider_id}/history` | Return newest compatibility runs for a provider |
| POST | `/v1/console/governor/evaluate` | Detect loops, intent drift, and unapproved irreversible actions |
| POST | `/v1/console/simulate` | Simulate policy and route decisions without provider execution |
| POST | `/v1/console/production/migration-readiness` | Evaluate SQLite-to-Postgres migration evidence |

**Runaway evaluate request:**
```json
{
  "state": {"step_count": 5, "total_cost_usd": 0.05, "error_count": 0, "elapsed_ms": 12000},
  "limits": {"max_steps": 20, "max_cost_usd": 1.0, "max_errors": 3, "max_elapsed_ms": 300000}
}
```

**Runaway evaluate response:**
```json
{
  "allowed": true, "code": "OK", "explanation": "Within all limits",
  "next_action": "continue"
}
```

**Governor evaluate request:**
```json
{
  "intent": "fetch weather data",
  "loop_threshold": 3,
  "steps": [
    {"action": "http_call", "detail": "GET /api/weather", "approved": true},
    {"action": "file_write", "detail": "/tmp/weather.json", "approved": false}
  ],
  "approved_actions": ["http_call"]
}
```

**Simulate request:**
```json
{
  "request": {"model": "hermes-default", "messages": [...]},
  "policy": {"max_cost_usd": 0.01},
  "routes": [{"name": "hermes-default", "targets": [...]}],
  "minimum_quality": 0.8
}
```

```bash
# Runaway firewall
curl -X POST http://localhost:8013/v1/console/runaway/evaluate \
  -H "Content-Type: application/json" \
  -d '{"state":{"step_count":5,"total_cost_usd":0.05},"limits":{"max_steps":20,"max_cost_usd":1.0}}'

# Governor
curl -X POST http://localhost:8013/v1/console/governor/evaluate \
  -H "Content-Type: application/json" \
  -d '{"intent":"search","loop_threshold":3,"steps":[],"approved_actions":[]}'

# Simulate
curl -X POST http://localhost:8013/v1/console/simulate \
  -H "Content-Type: application/json" \
  -d '{"request":{},"policy":{},"routes":[],"minimum_quality":0}'

# Compatibility run
curl -X POST http://localhost:8013/v1/console/compatibility/prov-1/run

# Record contract
curl -X POST http://localhost:8013/v1/console/contracts \
  -H "Content-Type: application/json" \
  -d '{"provider_id":"openai","model":"gpt-4","supports_tools":true}'
```

---

### 3.3 Observability & Tracing

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/console/evidence/spans` | Persist one tenant-scoped OpenInference evidence span |
| GET | `/v1/console/evidence/traces/{trace_id}?tenant_id=` | Export one tenant trace as OTLP-shaped document |
| POST | `/v1/console/replay/compare` | Compare privacy-safe production evidence with candidate replay |
| POST | `/v1/console/replay/run` | Execute bounded candidate replay through local gateway |
| POST | `/v1/console/incidents/events` | Append one privacy-safe event to an incident timeline |
| GET | `/v1/console/incidents/from-request/{request_id}` | Build incident explanation from a real routing decision |
| GET | `/v1/console/incidents/{incident_id}` | Explain why an incident happened, impact, next fix |
| POST | `/v1/console/traces` | Append one privacy-safe trace span |
| GET | `/v1/console/traces` | List trace run summaries for a tenant (requires `X-Tenant-Id`) |
| GET | `/v1/console/traces/{run_id}` | Return one tenant-isolated nested trace (requires `X-Tenant-Id`) |
| POST | `/v1/console/outcomes/summary` | Calculate cost-to-outcome unit economics |

```bash
# Record evidence span
curl -X POST http://localhost:8013/v1/console/evidence/spans \
  -H "Content-Type: application/json" \
  -d '{"trace_id":"t1","span_id":"s1","name":"llm_call","tenant_id":"default"}'

# Get trace
curl -H "X-Tenant-Id: default" \
  http://localhost:8013/v1/console/traces/run-abc123

# Incident from request
curl http://localhost:8013/v1/console/incidents/from-request/req-123

# Replay compare
curl -X POST http://localhost:8013/v1/console/replay/compare \
  -H "Content-Type: application/json" \
  -d '{"baseline":{},"candidate":{}}'

# Outcome summary
curl -X POST http://localhost:8013/v1/console/outcomes/summary \
  -H "Content-Type: application/json" \
  -d '{"records":[{"cost_usd":0.01,"success":true,"task_type":"chat"}]}'
```

---

### 3.4 Supply Chain

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/console/supply-chain/sbom` | Deterministic SBOM for pinned Python and npm dependencies |
| POST | `/v1/console/supply-chain/upgrade-risk` | Assess a dependency diff before automated rollout |

```bash
curl http://localhost:8013/v1/console/supply-chain/sbom

curl -X POST http://localhost:8013/v1/console/supply-chain/upgrade-risk \
  -H "Content-Type: application/json" \
  -d '{"current":{"package":"fastapi","version":"0.141.0"},"proposed":{"version":"0.142.0"},"security_advisories":{}}'
```

---

### 3.5 Service Management

All service endpoints require `X-Console-Action: 1` header and local origin (`127.0.0.1`).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/console/services` | List all services with status (running, reachable, PID, port) |
| POST | `/v1/console/services/{slug}/start` | Start a single service by slug |
| POST | `/v1/console/services/{slug}/stop` | Stop a single service by slug |
| POST | `/v1/console/services/start-all` | Start all managed services |
| POST | `/v1/console/services/stop-all` | Stop all managed services |

```bash
# List services
curl -H "X-Console-Action: 1" http://localhost:8013/v1/console/services

# Start gateway
curl -X POST -H "X-Console-Action: 1" \
  http://localhost:8013/v1/console/services/gateway/start

# Stop all
curl -X POST -H "X-Console-Action: 1" \
  http://localhost:8013/v1/console/services/stop-all
```

---

### 3.6 Product — Applications & Home

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/product/home?role=` | Role-aware product home (activation, metrics, counts) |
| GET | `/v1/product/templates` | List route templates |
| GET | `/v1/product/applications` | List all applications |
| POST | `/v1/product/applications` | Create a new application |

```bash
# Product home
curl http://localhost:8013/v1/product/home?role=developer

# List applications
curl http://localhost:8013/v1/product/applications

# Create application
curl -X POST http://localhost:8013/v1/product/applications \
  -H "Content-Type: application/json" \
  -d '{"name":"my-app","default_route":"hermes-default"}'
```

---

### 3.7 Product — Routes

Full lifecycle: create, edit, version, validate, simulate, test, publish, rollback, archive, delete.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/product/routes` | List all routes |
| POST | `/v1/product/routes` | Create route with targets |
| PUT | `/v1/product/routes/{route_id}` | Update route targets |
| GET | `/v1/product/routes/{route_id}/dependencies` | Route dependency tree |
| GET | `/v1/product/routes/{route_id}/versions` | Route version history |
| POST | `/v1/product/routes/{route_id}/duplicate` | Duplicate route with new name |
| POST | `/v1/product/routes/{route_id}/archive` | Archive a route |
| POST | `/v1/product/routes/{route_id}/restore` | Restore an archived route |
| DELETE | `/v1/product/routes/{route_id}` | Delete route (requires confirmation body) |
| POST | `/v1/product/routes/{route_id}/validate` | Validate route configuration |
| POST | `/v1/product/routes/{route_id}/simulate` | Simulate with capabilities and budget |
| POST | `/v1/product/routes/{route_id}/publish` | Publish route (makes draft live) |
| POST | `/v1/product/routes/{route_id}/test` | Test route at a specific target index |
| GET | `/v1/product/routes/{route_id}/status` | Route circuit-breaker and cooldown status |
| DELETE | `/v1/product/routes/{route_id}/cooldowns?model=` | Clear model cooldowns for a route |
| POST | `/v1/product/routes/{route_id}/snapshots/{version}` | Snapshot a route version |
| POST | `/v1/product/routes/{route_id}/rollback/{version}` | Rollback to a snapshot |

**Create route request:**
```json
{
  "name": "hermes-default",
  "targets": [
    {
      "provider": "anthropic",
      "model": "claude-sonnet-4-20250514",
      "context_length": 200000,
      "weight": 1.0,
      "required_capabilities": []
    }
  ]
}
```

```bash
# Create route
curl -X POST http://localhost:8013/v1/product/routes \
  -H "Content-Type: application/json" \
  -d '{"name":"hermes-default","targets":[{"provider":"anthropic","model":"claude-sonnet-4-20250514","context_length":200000}]}'

# Publish route
curl -X POST http://localhost:8013/v1/product/routes/route-abc/publish

# Simulate route
curl -X POST http://localhost:8013/v1/product/routes/route-abc/simulate \
  -H "Content-Type: application/json" \
  -d '{"capabilities":["tool_calls"],"budget_remaining_usd":10.0}'

# Delete route (requires confirmation)
curl -X DELETE http://localhost:8013/v1/product/routes/route-abc \
  -H "Content-Type: application/json" \
  -d '{"confirmation":"route-abc"}'
```

---

### 3.8 Product — Providers

Provider connections (with encrypted credentials), provider discovery, and model catalogs.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/product/provider-types` | Provider-specific connection fields for setup wizard |
| GET | `/v1/product/provider-connections` | List named provider accounts (no secrets) |
| POST | `/v1/product/provider-connections` | Store encrypted credentials for a provider account |
| PUT | `/v1/product/provider-connections/{id}` | Update provider connection (empty api_key = keep stored) |
| POST | `/v1/product/provider-connections/{id}/sync-models` | Verify creds and download provider model catalog |
| GET | `/v1/product/discovered-models` | All discovered models with provider alias |
| GET | `/v1/product/provider-connections/{id}/models` | Models for one provider |
| GET | `/v1/product/providers` | List product providers |
| POST | `/v1/product/providers` | Create a provider entry |
| POST | `/v1/product/providers/{id}/check` | Record provider health check result |

**Create provider connection request:**
```json
{
  "name": "OpenAI",
  "slug": "openai",
  "base_url": "https://api.openai.com/v1",
  "api_key": "sk-...",
  "region": "us-east-1",
  "user_agent": null,
  "extra_body_json": ""
}
```

```bash
# List connections
curl http://localhost:8013/v1/product/provider-connections

# Create connection
curl -X POST http://localhost:8013/v1/product/provider-connections \
  -H "Content-Type: application/json" \
  -d '{"name":"OpenAI","slug":"openai","base_url":"https://api.openai.com/v1","api_key":"sk-..."}'

# Sync models
curl -X POST http://localhost:8013/v1/product/provider-connections/prov-1/sync-models

# Provider health check
curl -X POST http://localhost:8013/v1/product/providers/prov-1/check \
  -H "Content-Type: application/json" \
  -d '{"healthy":true,"latency_ms":120}'
```

---

### 3.9 Product — Customers & Usage

Cost attribution, customer budgets, daily spend charts, model breakdowns, CSV export.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/product/activity` | Activity log (recent routing decisions) |
| GET | `/v1/product/usage?days=&route=&period=&page=&page_size=` | Usage data (hourly/daily/monthly buckets) |
| GET | `/v1/product/customers` | List customers with MTD summary and budget |
| POST | `/v1/product/customers` | Create customer (409 on duplicate name) |
| GET | `/v1/product/customers/{id}` | Customer detail (summary + budget) |
| PUT | `/v1/product/customers/{id}/budget` | Set monthly budget (422 on non-positive) |
| GET | `/v1/product/customers/{id}/daily-spend?days=&granularity=` | Daily/weekly/monthly spend chart data |
| GET | `/v1/product/customers/{id}/models` | Breakdown by model (MTD, sorted by cost desc) |
| GET | `/v1/product/customers/{id}/export.csv` | CSV ledger export (one row per entry) |

```bash
# List customers
curl http://localhost:8013/v1/product/customers

# Create customer
curl -X POST http://localhost:8013/v1/product/customers \
  -H "Content-Type: application/json" \
  -d '{"name":"acme-team"}'

# Set budget
curl -X PUT http://localhost:8013/v1/product/customers/cust-1/budget \
  -H "Content-Type: application/json" \
  -d '{"monthly_limit_usd":500.0}'

# Daily spend
curl "http://localhost:8013/v1/product/customers/cust-1/daily-spend?days=30&granularity=day"

# Export CSV
curl http://localhost:8013/v1/product/customers/cust-1/export.csv
```

---

### 3.10 Product — Intelligence

PII redaction, exact-response cache, usage anomaly detection, cost-aware routing.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/product/intelligence/redact?text=` | Redact PII from text |
| POST | `/v1/product/intelligence/cache` | Store request/response in exact-response cache |
| POST | `/v1/product/intelligence/cache/lookup` | Lookup cached response by request |
| POST | `/v1/product/intelligence/anomaly` | Detect usage anomalies (z-score) |
| POST | `/v1/product/intelligence/route` | Cost-aware route selection among candidates |
| GET | `/v1/product/intelligence/cache-stats` | Cache hit/miss statistics (7-day window) |

```bash
# PII redaction
curl "http://localhost:8013/v1/product/intelligence/redact?text=Call+me+at+555-1234"

# Cache put
curl -X POST http://localhost:8013/v1/product/intelligence/cache \
  -H "Content-Type: application/json" \
  -d '{"request":{"model":"m","messages":[]},"response":{"content":"ok"},"ttl":3600}'

# Cache lookup
curl -X POST http://localhost:8013/v1/product/intelligence/cache/lookup \
  -H "Content-Type: application/json" \
  -d '{"request":{"model":"m","messages":[]}}'

# Anomaly detection
curl -X POST http://localhost:8013/v1/product/intelligence/anomaly \
  -H "Content-Type: application/json" \
  -d '{"history":[1.0,1.1,0.9,1.0,5.0],"current":5.0,"z_limit":3.0}'

# Cost-aware routing
curl -X POST http://localhost:8013/v1/product/intelligence/route \
  -H "Content-Type: application/json" \
  -d '{"candidates":[{"model":"m1","cost":0.01,"quality":0.9},{"model":"m2","cost":0.005,"quality":0.8}],"min_quality":0.8}'
```

---

### 3.11 Product — Prompts & Quality

Prompt registry with immutable versions, A/B assignment, output quality evaluation, release gates.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/product/prompts?name=` | List prompt names, or versions for a specific name |
| POST | `/v1/product/prompts` | Create new prompt version |
| POST | `/v1/product/prompts/assign` | Assign prompt versions to a subject (A/B) |
| POST | `/v1/product/quota/classify` | Classify a provider error as quota/retry/permanent |
| POST | `/v1/product/quality/evaluate` | Evaluate output against rules (recorded in eval store) |
| GET | `/v1/product/quality/runs` | List persisted quality evaluation runs |
| POST | `/v1/product/quality/release-gate` | Gate release on quality scores and regression limits |
| POST | `/v1/product/quality/batch` | Build batch manifest with discount pricing |
| POST | `/v1/product/quality/audit` | Create audit report from findings |
| POST | `/v1/product/quality/audit/verify` | Verify audit report integrity |

```bash
# Create prompt
curl -X POST http://localhost:8013/v1/product/prompts \
  -H "Content-Type: application/json" \
  -d '{"name":"greeting","template":"Say hello to {{name}}","metadata":{"version":"v1"}}'

# Assign to subject
curl -X POST http://localhost:8013/v1/product/prompts/assign \
  -H "Content-Type: application/json" \
  -d '{"name":"greeting","subject":"app-v2","versions":[1,2]}'

# Evaluate quality
curl -X POST http://localhost:8013/v1/product/quality/evaluate \
  -H "Content-Type: application/json" \
  -d '{"name":"test","output":"The answer is 42.","rules":{"required_words":["42"],"min_length":10}}'

# Release gate
curl -X POST http://localhost:8013/v1/product/quality/release-gate \
  -H "Content-Type: application/json" \
  -d '{"scores":[0.95,0.92,0.98],"minimum":0.9,"max_regression":0.05,"baseline":0.93}'

# Quota classify
curl -X POST http://localhost:8013/v1/product/quota/classify \
  -H "Content-Type: application/json" \
  -d '{"status_code":429,"code":"rate_limit_exceeded","message":"Too many requests"}'
```

---

### 3.12 Product — Data Import/Export

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/product/export` | Export entire product bundle (routes, providers, keys) |
| POST | `/v1/product/import` | Import product bundle |
| GET | `/v1/product/recommendations` | System recommendations for optimization |
| GET | `/v1/product/audit` | Audit trail of all product changes |

```bash
# Export bundle
curl http://localhost:8013/v1/product/export

# Import bundle
curl -X POST http://localhost:8013/v1/product/import \
  -H "Content-Type: application/json" \
  -d '{"routes":[],"providers":[],"keys":[]}'

# Recommendations
curl http://localhost:8013/v1/product/recommendations

# Audit trail
curl http://localhost:8013/v1/product/audit
```

---

### 3.13 Product — Alerts, Keys, Budgets

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/product/applications/{app_id}/keys/rotate` | Rotate API key for an application |
| POST | `/v1/product/keys/{key_id}/revoke` | Revoke an active key |
| PUT | `/v1/product/budgets/{scope}` | Set budget with limit and reset day |
| GET | `/v1/product/alerts` | List product alert rules |
| POST | `/v1/product/alerts` | Create product alert rule |

```bash
# Rotate key
curl -X POST http://localhost:8013/v1/product/applications/app-1/keys/rotate

# Revoke key
curl -X POST http://localhost:8013/v1/product/keys/key-1/revoke

# Set budget
curl -X PUT http://localhost:8013/v1/product/budgets/global \
  -H "Content-Type: application/json" \
  -d '{"limit_usd":100.0,"reset_day":1}'

# Create alert
curl -X POST http://localhost:8013/v1/product/alerts \
  -H "Content-Type: application/json" \
  -d '{"name":"high-spend","metric":"cost_usd","threshold":50.0}'
```

---

### 3.14 Product — Environments, Views, SLO

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/product/environments` | List environments |
| POST | `/v1/product/environments` | Create environment (name, base_url, default flag) |
| GET | `/v1/product/views?role=` | List views for a role |
| POST | `/v1/product/views` | Create view with filters |
| GET | `/v1/product/slo` | SLO monitoring (availability, burn rate, state) |

```bash
# Environments
curl http://localhost:8013/v1/product/environments
curl -X POST http://localhost:8013/v1/product/environments \
  -H "Content-Type: application/json" \
  -d '{"name":"staging","base_url":"https://staging.api.example.com","default":false}'

# Views
curl http://localhost:8013/v1/product/views?role=developer
curl -X POST http://localhost:8013/v1/product/views \
  -H "Content-Type: application/json" \
  -d '{"name":"my-view","role":"developer","filters":{"status":"active"}}'

# SLO
curl http://localhost:8013/v1/product/slo
```

**SLO response:**
```json
{
  "availability": 0.995,
  "target": 0.99,
  "burn_rate": 0.5,
  "state": "healthy",
  "remaining_failures": 3
}
```

---

### 3.15 Admin Routes & Priority Routes

Logical routing administration served by the console API (port 8013), not the control API.

#### Applications

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/admin/applications` | Create application |
| GET | `/v1/admin/applications` | List applications |

#### Routes

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/admin/routes` | Create route |
| GET | `/v1/admin/routes` | List all routes |
| GET | `/v1/admin/routes/{route_id}` | Get one route |
| PUT | `/v1/admin/routes/{route_id}` | Update route |
| POST | `/v1/admin/routes/{route_id}/publish` | Publish route |
| POST | `/v1/admin/routes/{route_id}/rollback` | Rollback to previous version |
| POST | `/v1/admin/routes/{route_id}/simulate` | Simulate with quality tier, cost, health, capabilities |
| GET | `/v1/admin/routes/{route_id}/activity` | Route activity log |

#### Priority Routes

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/admin/priority-routes` | Create priority route |
| GET | `/v1/admin/priority-routes` | List priority routes |
| GET | `/v1/admin/priority-routes/{route_id}` | Get one priority route |
| PUT | `/v1/admin/priority-routes/{route_id}` | Update priority route |
| POST | `/v1/admin/priority-routes/{route_id}/publish` | Publish priority route |
| POST | `/v1/admin/priority-routes/{route_id}/simulate` | Simulate priority route resolution |

**Admin route create:**
```json
{
  "name": "production-route",
  "targets": [
    {"model": "anthropic/claude-sonnet-4-20250514", "weight": 1.0, "context_length": 200000}
  ],
  "fallback": [],
  "policy": {"max_cost_usd": 0.05}
}
```

**Admin simulate request:**
```json
{
  "at": "2026-08-19T12:00:00",
  "quality_tier": "balanced",
  "estimated_cost": 0.01,
  "spend_by_model": {"gpt-4": 5.0},
  "health": {"anthropic": true},
  "region": "us-east-1",
  "capabilities": ["tool_calls", "vision"]
}
```

```bash
# Create admin route
curl -X POST http://localhost:8013/v1/admin/routes \
  -H "Content-Type: application/json" \
  -d '{"name":"prod-route","targets":[{"model":"anthropic/claude-sonnet-4-20250514","weight":1.0}]}'

# Publish
curl -X POST http://localhost:8013/v1/admin/routes/route-1/publish

# Simulate
curl -X POST http://localhost:8013/v1/admin/routes/route-1/simulate \
  -H "Content-Type: application/json" \
  -d '{"at":"2026-08-19T12:00:00","quality_tier":"balanced","estimated_cost":0.01,"spend_by_model":{},"health":{},"capabilities":[]}'

# Priority route
curl -X POST http://localhost:8013/v1/admin/priority-routes \
  -H "Content-Type: application/json" \
  -d '{"name":"urgent-route","targets":[{"model":"anthropic/claude-sonnet-4-20250514","weight":1.0}]}'

curl -X POST http://localhost:8013/v1/admin/priority-routes/prio-1/publish
```

---

### 3.16 Alert Rules API

REST API for alert rule CRUD and dispatch history. Mounted into the console at `/api/alerts`. These are the low-level alert notification rules (webhook/slack/telegram/email dispatchers), separate from the product-level alert summaries in 3.13.

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/alerts` | Create alert rule (validates channel config) |
| GET | `/api/alerts` | List all alert rules |
| GET | `/api/alerts/{rule_id}` | Get one alert rule |
| DELETE | `/api/alerts/{rule_id}` | Delete alert rule (204 on success) |
| GET | `/api/alerts/history?page=&page_size=&alert_rule_id=&channel=&delivery_status=` | Paginated dispatch logs |

**Channel config requirements:**

| Channel | Required Fields |
|---------|----------------|
| `webhook` | `url` |
| `slack` | `bot_token`, `channel` |
| `telegram` | `bot_token`, `chat_id` |
| `email` | `host`, `username`, `to_address` |

**Create rule request:**
```json
{
  "name": "high-spend-webhook",
  "threshold": 100.0,
  "channel": "webhook",
  "config": {"url": "https://hooks.example.com/alert", "secret": "whsec_..."},
  "cooldown_seconds": 300,
  "enabled": true
}
```

**Response (201):**
```json
{
  "id": "a1b2c3d4e5f6a7b8",
  "name": "high-spend-webhook",
  "threshold": 100.0,
  "channel": "webhook",
  "config": {"url": "https://hooks.example.com/alert"},
  "cooldown_seconds": 300,
  "enabled": true
}
```

**History response (200):**
```json
{
  "items": [
    {
      "id": 1,
      "alert_rule_id": "a1b2c3d4e5f6a7b8",
      "channel": "webhook",
      "delivery_status": "delivered",
      "response_code": 200,
      "error_message": null,
      "dispatched_at": 1692000000.0
    }
  ],
  "total": 1
}
```

```bash
# Create rule
curl -X POST http://localhost:8013/api/alerts \
  -H "Content-Type: application/json" \
  -d '{"name":"high-spend","threshold":100,"channel":"webhook","config":{"url":"https://hooks.example.com/alert"},"cooldown_seconds":300,"enabled":true}'

# List rules
curl http://localhost:8013/api/alerts

# Get rule
curl http://localhost:8013/api/alerts/a1b2c3d4e5f6a7b8

# Delete rule
curl -X DELETE http://localhost:8013/api/alerts/a1b2c3d4e5f6a7b8

# Dispatch history
curl "http://localhost:8013/api/alerts/history?page=1&page_size=20&channel=webhook"
```

---

## Endpoint Count Summary

| API Surface | File | Endpoints |
|---|---|---|
| Gateway Proxy | `src/llm_budget_gateway/main.py` | 9 |
| Admin Control | `src/llm_budget_gateway/control_api.py` | 7 |
| System & Console Core | `src/llm_budget_gateway/console_api.py` | 10 |
| Safety & Governance | `src/llm_budget_gateway/console_api.py` | 13 |
| Observability & Tracing | `src/llm_budget_gateway/console_api.py` | 11 |
| Supply Chain | `src/llm_budget_gateway/console_api.py` | 2 |
| Service Management | `src/llm_budget_gateway/console_api.py` | 5 |
| Product — Applications & Home | `src/llm_budget_gateway/console_api.py` | 4 |
| Product — Routes | `src/llm_budget_gateway/console_api.py` | 17 |
| Product — Providers | `src/llm_budget_gateway/console_api.py` | 10 |
| Product — Customers & Usage | `src/llm_budget_gateway/console_api.py` | 9 |
| Product — Intelligence | `src/llm_budget_gateway/console_api.py` | 6 |
| Product — Prompts & Quality | `src/llm_budget_gateway/console_api.py` | 10 |
| Product — Data Import/Export | `src/llm_budget_gateway/console_api.py` | 4 |
| Product — Alerts, Keys, Budgets | `src/llm_budget_gateway/console_api.py` | 5 |
| Product — Environments, Views, SLO | `src/llm_budget_gateway/console_api.py` | 5 |
| Admin Routes & Priority Routes | `src/llm_budget_gateway/console_api.py` | 16 |
| Alert Rules | `src/llm_budget_gateway/alert_api.py` | 5 |
| **Total** | | **~148** |
