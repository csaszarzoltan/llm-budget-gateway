# MCP Governance API

REST surface of the MCP server governance module (`mcp_governance`),
served by `create_mcp_governance_app()` in
`src/llm_budget_gateway/mcp_governance/api.py`. The app is a standalone
FastAPI factory — it is not mounted inside the gateway's `create_app()`.

```bash
export GATEWAY_MCP_API_KEY='replace-with-a-strong-random-secret'
.venv/bin/uvicorn \
  llm_budget_gateway.mcp_governance.api:create_mcp_governance_app \
  --factory \
  --port 8016
```

OpenAPI: `http://localhost:8016/docs`. Dashboard: `http://localhost:8016/mcp`.

## Authentication

Every `/v1/mcp/*` route requires two headers; the `GET /mcp` dashboard is
unauthenticated.

| Header | Requirement |
|---|---|
| `Authorization` | `Bearer <key>`; the key comes from `GATEWAY_MCP_API_KEY` (or the factory's `api_key` argument) and is compared with `secrets.compare_digest` |
| `X-Tenant-Id` | present and non-empty (single-tenant scope in v1 — see the [governance guide](mcp-governance.md)) |

Failure modes:

- No key configured at all → `503 {"detail": "mcp API key is not configured"}`
- Missing/wrong key or missing `X-Tenant-Id` → `401 {"detail": "authentication and tenant are required"}`
- Unknown server/tool/policy/budget/approval id → `404`
- Duplicate registration / policy / budget → `409`
- Policy deny, SSRF block, retired server, disabled tool → `403`
- Approval required → `409 {"detail": "approval required", "approval_id": "<id>"}`
- Hard budget exceeded → `412`
- Discovery upstream failure → `502`
- Validation errors (bad body, bad filter value) → `422`

Generic `MCPGovernanceError` bodies return a fixed generic detail
(`bad request`, `forbidden`, `not found`, `conflict`, `invalid arguments`,
`upstream error`); the specific reason goes to the server log only.

## Endpoints

| Method | Path | Success | Errors |
|---|---|---|---|
| GET | `/mcp` | 200 HTML dashboard | — |
| POST | `/v1/mcp/servers` | 201 `MCPServer` | 401/503, 409, 422 |
| GET | `/v1/mcp/servers` | 200 `{object:"list", data:[MCPServer]}` | 401/503 |
| GET | `/v1/mcp/servers/{server_id}` | 200 `MCPServer` | 401/503, 404 |
| DELETE | `/v1/mcp/servers/{server_id}` | 200 `{server_id, status:"retired"}` | 401/503, 404 |
| GET | `/v1/mcp/servers/{server_id}/tools` | 200 `{object:"list", data:[ToolInfo]}` | 401/503, 404 |
| POST | `/v1/mcp/policies` | 201 `ToolPolicy` | 401/503, 409, 422 |
| GET | `/v1/mcp/policies` | 200 `{object:"list", data:[ToolPolicy]}` | 401/503 |
| DELETE | `/v1/mcp/policies/{policy_id}` | 204 | 401/503, 404 |
| POST | `/v1/mcp/budgets` | 201 `ToolBudget` | 401/503, 409, 422 |
| GET | `/v1/mcp/budgets` | 200 `{object:"list", data:[ToolBudget]}` | 401/503 |
| DELETE | `/v1/mcp/budgets/{budget_id}` | 204 | 401/503, 404 |
| GET | `/v1/mcp/audit` | 200 `AuditPage` | 401/503, 422 |
| GET | `/v1/mcp/approvals` | 200 `{object:"list", data:[ApprovalRequest]}` | 401/503 |
| POST | `/v1/mcp/approvals/{approval_id}/approve` | 200 `ApprovalRequest` | 401/503, 404, 409 |
| POST | `/v1/mcp/approvals/{approval_id}/reject` | 200 `ApprovalRequest` | 401/503, 404, 409 |
| GET | `/v1/mcp/report` | 200 posture snapshot | 401/503 |

List endpoints accept `limit` (default 100, max 500) and `offset`
(default 0); `/v1/mcp/audit` defaults to `limit=50` and also accepts
`caller`, `server_id`, `tool_name`, `decision`, `status`, `since`, and
`until` filters (epoch seconds). `/v1/mcp/policies` and
`/v1/mcp/budgets` accept `scope_kind`, `scope_key`, `server_id`, and
`tool_name` filters.

## Server registry

### POST `/v1/mcp/servers`

Register a server with its tool inventory. `server_id` is generated
server-side. `endpoint` is required when `transport != "stdio"`. Tool
names must be unique within the request.

```bash
curl -s http://localhost:8016/v1/mcp/servers \
  -H "Authorization: Bearer $GATEWAY_MCP_API_KEY" \
  -H "X-Tenant-Id: acme" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "github-mcp",
    "transport": "http",
    "endpoint": "https://mcp.example.com/mcp",
    "version": "1.0.0",
    "description": "GitHub tooling",
    "tools": [
      {"name": "create_issue", "description": "Create a GitHub issue",
       "input_schema": {"type": "object",
                        "properties": {"title": {"type": "string"}}},
       "enabled": true}
    ],
    "config": {"auth": "bearer"}
  }'
```

Response `201`:

```json
{
  "server_id": "ab12cd34ef56gh78",
  "name": "github-mcp",
  "transport": "http",
  "endpoint": "https://mcp.example.com/mcp",
  "version": "1.0.0",
  "description": "GitHub tooling",
  "status": "active",
  "config": {"auth": "bearer"},
  "created_at": 1785708000,
  "updated_at": 1785708000
}
```

Registering the same `name` + `version` again → `409`. Registering the
same name with a newer version succeeds and adds a versioned row.

### GET `/v1/mcp/servers`

```bash
curl -s http://localhost:8016/v1/mcp/servers \
  -H "Authorization: Bearer $GATEWAY_MCP_API_KEY" \
  -H "X-Tenant-Id: acme"
```

Response `200` (one row per name — the highest version, active only):

```json
{
  "object": "list",
  "data": [{"server_id": "ab12cd34ef56gh78", "name": "github-mcp",
            "transport": "http", "endpoint": "https://mcp.example.com/mcp",
            "version": "1.1.0", "description": "GitHub tooling",
            "status": "active", "config": {}, "created_at": 1785708600,
            "updated_at": 1785708600}]
}
```

### GET `/v1/mcp/servers/{server_id}` / DELETE `/v1/mcp/servers/{server_id}`

`GET` returns the server by id (any status; unknown → 404). `DELETE`
retires it (idempotent) and returns `{"server_id": "...", "status": "retired"}`.

### GET `/v1/mcp/servers/{server_id}/tools`

Returns the tool inventory for the server (unknown server → 404, never a
silent empty list):

```json
{
  "object": "list",
  "data": [{"name": "create_issue", "description": "Create a GitHub issue",
            "input_schema": {"type": "object",
                             "properties": {"title": {"type": "string"}}},
            "enabled": true}]
}
```

## Per-tool policies

### POST `/v1/mcp/policies`

`scope_kind` ∈ `user|team|project|global`; `effect` ∈ `allow|deny|approval`.
`tool_name` may only be set together with `server_id` (else 422). The
duplicate 4-tuple `(scope_kind, scope_key, server_id, tool_name)` → 409.

```bash
curl -s http://localhost:8016/v1/mcp/policies \
  -H "Authorization: Bearer $GATEWAY_MCP_API_KEY" \
  -H "X-Tenant-Id: acme" \
  -H "Content-Type: application/json" \
  -d '{
    "scope_kind": "user",
    "scope_key": "alice",
    "server_id": "ab12cd34ef56gh78",
    "tool_name": "create_issue",
    "effect": "allow",
    "description": "Alice may create issues"
  }'
```

Response `201` adds `policy_id` and `created_at`. `DELETE
/v1/mcp/policies/{policy_id}` is a no-op 204 when the policy does not
exist. Resolution semantics (most specific scope/tool selector wins, deny
> approval > allow on ties, default deny) are described in the
[governance guide](mcp-governance.md).

## Per-tool budgets

### POST `/v1/mcp/budgets`

At least one of `soft_limit` / `hard_limit` must be set; limits must be
finite and ≥ 0; `window` ∈ `30s|30m|30h|30d|daily|monthly|<n><s|m|h|d>`
(default `30d`). Same selector rules and 409 as policies.

```bash
curl -s http://localhost:8016/v1/mcp/budgets \
  -H "Authorization: Bearer $GATEWAY_MCP_API_KEY" \
  -H "X-Tenant-Id: acme" \
  -H "Content-Type: application/json" \
  -d '{
    "scope_kind": "user",
    "scope_key": "alice",
    "server_id": "ab12cd34ef56gh78",
    "tool_name": "create_issue",
    "soft_limit": 4.0,
    "hard_limit": 5.0,
    "window": "30d"
  }'
```

Response `201` adds `budget_id` and `created_at`. `DELETE
/v1/mcp/budgets/{budget_id}` is a no-op 204 when missing. Hard-limit
breaches surface as `412` at engine gate time; spend is attributed to the
ledger with `tool_name="<server_id>:<tool_name>"`.

## Audit trail

### GET `/v1/mcp/audit`

Query filters: `caller`, `server_id`, `tool_name`, `decision`, `status`,
`since`, `until`, `limit` (default 50, max 500), `offset`. Invalid
`decision`/`status` values → 422. Newest first.

```bash
curl -s "http://localhost:8016/v1/mcp/audit?caller=alice&decision=denied&limit=5" \
  -H "Authorization: Bearer $GATEWAY_MCP_API_KEY" \
  -H "X-Tenant-Id: acme"
```

Response `200`:

```json
{
  "object": "list",
  "data": [{
    "event_id": "a1b2c3d4e5f6a7b8",
    "server_id": "ab12cd34ef56gh78",
    "tool_name": "create_issue",
    "caller": "alice",
    "scope_kind": "user",
    "scope_key": "alice",
    "args": {"title": "Fix the bug"},
    "decision": "denied",
    "status": "blocked",
    "reason": "denied by policy 90e89d42ddc7ef28",
    "cost": 0.0,
    "latency_ms": 0,
    "timestamp": 1785708000,
    "redacted": true,
    "approval_id": null,
    "request_id": "req_9f8e7d"
  }],
  "limit": 5,
  "offset": 0,
  "total": 1
}
```

`args` is always the PII-redacted copy. Decision/status semantics are
tabulated in the [governance guide](mcp-governance.md).

## Approvals

### GET `/v1/mcp/approvals`

Lists approval requests, newest first; optional `status` and `caller`
filters. Statuses: `pending|approved|rejected|consumed|expired`.

### POST `/v1/mcp/approvals/{approval_id}/approve` and `/reject`

Body: `{"actor": "<who decided>"}` (defaults to `"admin"`). `approve`:
`pending → approved`; `reject`: `pending → rejected`. A non-`pending`
request → 409. Approved requests are single-use: the engine consumes them
atomically at the next matching call.

```bash
curl -s http://localhost:8016/v1/mcp/approvals/d2ef06d3064ce1a3/approve \
  -H "Authorization: Bearer $GATEWAY_MCP_API_KEY" \
  -H "X-Tenant-Id: acme" \
  -H "Content-Type: application/json" \
  -d '{"actor": "bob"}'
```

Response `200` is the updated `ApprovalRequest` with
`status: "approved"`, `decided_by: "bob"`, `decided_at` set.

## Governance report

### GET `/v1/mcp/report`

Deterministic posture snapshot over the last 24h
(`since_epoch = now - 86400`):

```json
{
  "total_servers": 1,
  "active_servers": 1,
  "retired_servers": 0,
  "total_tools": 2,
  "tools_with_policy": 2,
  "tools_with_budget": 0,
  "pending_approvals": 0,
  "ssrf_blocks_24h": 0,
  "pii_redactions_24h": 1,
  "budget_breaches_24h": 0,
  "risk_tier": "medium"
}
```

`risk_tier` is `high` when approvals or SSRF blocks are pending, `medium`
when coverage is incomplete or no servers are registered, else `low`.

## See also

- [MCP Governance guide](mcp-governance.md) — components, engine flow,
  security model, configuration.
- [Architecture & API contract](architecture/mcp-governance.md) — the
  normative design with full request/response contracts and status
  mapping.
- [MCP governance example](examples/mcp_governance.py) — runnable offline
  walkthrough of the REST surface via `httpx.ASGITransport`.

