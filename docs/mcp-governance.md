# MCP Server Governance

Tool-level governance for [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)
servers: a server registry, per-tool allow/deny/approval policies,
per-tool cost ceilings, a PII-redacted audit trail, and a policy engine
that gates every tool call **before** execution and records the outcome
**after** it. Everything lives in the `mcp_governance` package
(`src/llm_budget_gateway/mcp_governance/`), shipped in version 9.4.0.

The design is normative in
[docs/architecture/mcp-governance.md](architecture/mcp-governance.md);
this guide is the operator-facing companion. For the exact REST contracts
see the [MCP Governance API reference](api/mcp-governance-api.md).

## What it gives you

- **MCP server registry** — register, list, version, and retire MCP
  servers together with their tool inventory (name, transport, version,
  tools, per-tool JSON Schema input schemas).
- **Per-tool access control** — `allow` / `deny` / `approval` policies
  scoped per user, team, project, or `global`, with the most specific
  policy winning.
- **Per-tool budgets** — soft (alert only) and hard (blocking) dollar
  ceilings per tool call, reusing the existing budget-enforcement engine
  and the shared cost ledger.
- **Audit trail** — every tool-call attempt logged with caller, tool,
  PII-redacted args, decision, cost, latency, and timestamp.
- **Reusable rules** — SSRF guard on tool URLs, deterministic PII
  redaction, and four-eyes approval gates.
- **Policy engine** — `before_call` / `after_call` API that an MCP proxy
  adapter calls around real tool execution.
- **REST API + dashboard** — `create_mcp_governance_app()` serves
  `/v1/mcp/*` endpoints and a responsive `/mcp` dashboard.

## Fail-closed posture

The module is **deny by default**. Any gate that does not explicitly allow
a call blocks it:

- the default policy effect is `deny` (no policy matches → blocked),
- an unregistered server or tool is rejected (404),
- a retired server and a disabled tool are rejected (403),
- tool arguments that fail the registered `input_schema` are rejected (422),
- an SSRF verdict that is not clearly safe is a block (403),
- a call over its hard budget is rejected (412),
- an approval-gated tool call is blocked until a human approves (409).

## Quick start

### 1. Run the standalone app

The governance app is an independent FastAPI factory; it is **not**
mounted inside the gateway's `create_app()` (out of scope for v1 — an MCP
proxy adapter calls the engine around execution).

```bash
export GATEWAY_MCP_API_KEY='replace-with-a-strong-random-secret'
.venv/bin/uvicorn \
  llm_budget_gateway.mcp_governance.api:create_mcp_governance_app \
  --factory \
  --port 8016
```

Open `http://localhost:8016/mcp` for the dashboard or
`http://localhost:8016/docs` for the OpenAPI UI.

Every `/v1/mcp/*` request requires `Authorization: Bearer <key>` **and**
a non-empty `X-Tenant-Id` header. If no key is configured the app returns
`503` (fail closed); a missing/wrong key or tenant returns `401`.

### 2. Register a server

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
       "enabled": true},
      {"name": "delete_repo", "description": "Delete a repository",
       "input_schema": {"type": "object",
                        "properties": {"owner": {"type": "string"}}},
       "enabled": true}
    ]
  }'
```

### 3. Run the offline example

The complete public API — registry, policies, budgets, audit, rules,
engine, and REST — is exercised network-free:

```bash
.venv/bin/python examples/mcp_governance.py
```

## Components

### Server registry (`MCPRegistry`)

Servers and their tools are stored in SQLite (`mcp_servers`,
`mcp_tools`). Each registration creates a `server_id`
(`secrets.token_hex(8)`); the `(name, version)` pair must be unique (a
second registration of the same name+version → 409). Registering the same
name with a higher version adds a versioned row — `list_servers()` returns
the highest version per name, `list_versions()` all of them newest first.
Retiring a server sets `status="retired"` (idempotent) and hides it from
the default listing; retired servers are fail-closed at the engine.

Transports: `stdio` (endpoint optional, launch command goes in
`config["command"]`), `sse`, `http`, `websocket` (endpoint required for
all non-`stdio`).

The `MCPDiscoveryAdapter` populates the tool inventory from a live MCP
server via the official SDK (`ClientSession.list_tools()`) over
http/sse/stdio/websocket — connection failures surface as
`MCPDiscoveryError` (502). The `mcp` SDK is lazy-imported, so module
import never requires it.

### Per-tool policies (`ToolPolicyStore`, `PolicyEvaluator`)

Policies are `allow` / `deny` / `approval` effects keyed by:

- `scope_kind` + `scope_key` — `user:alice`, `team:eng`, `project:core`,
  or `global:default`,
- `server_id` — `None` means any server (wildcard),
- `tool_name` — `None` means any tool on the selected server; may only be
  set together with `server_id` (else 422).

Resolution (`PolicyEvaluator.decide`) is deterministic:

1. candidates = policies whose scope matches the caller's scopes and
   whose server/tool selector matches the call,
2. score = scope specificity (user 3 > team 2 > project 1 > global 0)
   × 10 + tool-selector specificity (exact tool 3 > server-wide 2 >
   wildcard 1),
3. highest score wins; ties break deny > approval > allow,
4. no candidates → the store's `default_effect` (`deny` by default).

### Per-tool budgets (`ToolBudgetStore`, `ToolBudgetService`)

Budgets are soft/hard dollar ceilings (`soft_limit` alerts only,
`hard_limit` blocks when spend ≥ limit) over a window
(`30s`/`30m`/`30h`/`30d`/`daily`/`monthly`/`<n><s|m|h|d>`). Matching is
by scope identity + tool selector, the same shape as policies. Spend is
queried from the shared cost ledger, which records every completed
tool call with `tool_name="<server_id>:<tool_name>"` and `project`
attribution — so per-tool spend appears in existing budget reporting.

- `check()` raises `BudgetExceededError` (HTTP 412) when spend ≥ hard limit,
- `soft_exceeded()` returns the scopes past their soft limit (never raises),
- `record_usage()` is called by the engine's `after_call` for every
  completed call.

### Audit trail (`AuditStore`)

Append-only (replace-on-same-id) event log with indexed filters
(`caller`, `server_id`, `tool_name`, `decision`, `status`, `since`,
`until`, paginated, newest first). The engine writes exactly one row per
attempt:

| Outcome | decision | status | cost |
|---|---|---|---|
| allowed, call succeeded | `allowed` | `completed` | actual |
| allowed, tool raised | `allowed` | `failed` | actual |
| one-time approval consumed, succeeded | `approved` | `completed` | actual |
| policy denied | `denied` | `blocked` | 0 |
| approval required (pending) | `approval_required` | `blocked` | 0 |
| SSRF / policy violation | `denied` | `blocked` | 0 |
| budget ceiling exceeded | `denied` | `blocked` | 0 |
| server retired / tool disabled | `denied` | `blocked` | 0 |

Stored args are always the PII-redacted copy.

### Rules (`SSRFGuard`, `PIIRedactor`, `ApprovalGate`)

- **SSRF guard** — recursively inspects URL-bearing fields (`url`,
  `endpoint`, `webhook`, `callback_url`, plus `uri`/`target`/`link`/`href`
  aliases). Blocks non-http(s) schemes, IP literals in
  loopback/link-local/private/reserved/multicast space, and hostnames
  that resolve to any blocked address (allowlist override supported).
  Deterministic `reason` strings (`ssrf: private address 10.0.0.5`, …).
  The async `acheck()` keeps DNS off the event loop.
- **PII redaction** — deterministic masking of emails, SSNs, credit
  cards, `sk-*`/`sk-ant-*`/`AIza*`/`xai-*` API keys, JWTs, bearer tokens,
  AWS access keys, and phone numbers into `[REDACTED:<name>]`; deep-copies
  nested dicts/lists, never touches dict keys.
- **Approval gates** — four-eyes flow: a call gated by an `approval`
  policy creates a pending `ApprovalRequest` (args redacted, `args_hash`
  = sha256 of canonical JSON) and blocks with `approval_id` (409); a human
  approves/rejects via the REST API or `ApprovalGate`; the approved
  request is consumed **once** (atomic find+consume in one transaction —
  concurrent callers cannot double-consume) and expires after
  `ttl_seconds` (default 1 hour).

### Policy engine (`MCPPolicyEngine`)

`before_call(caller, scopes, server_id, tool_name, args)` gates a call in
strict order:

1. server must exist (404),
2. retired server → deny (403),
3. tool must exist (404),
4. disabled tool → deny (403),
5. args validated against the registered `input_schema` (422),
6. policy decision — deny → 403; approval → consume an approved request
   or create one (409); allow → proceed,
7. SSRF guard on args (403),
8. budget check (412).

Every block writes its audit row first. On success it returns a
`CallContext`; the caller runs the tool, then calls
`after_call(ctx, status, cost, latency_ms)` to append the outcome event
and — for `status="completed"` — record usage in the cost ledger.

### Governance report (`MCPGovernanceReport`)

`build()` produces a deterministic posture snapshot — total/active/
retired servers, tools with policy / with budget coverage, pending
approvals, 24h SSRF blocks / PII redactions / budget breaches, and a
`risk_tier` (`high` when approvals or SSRF blocks are pending, `medium`
when coverage is incomplete, else `low`). `assess()` turns it into
`{risk_tier, gaps[], recommendation}` for the Assurance Center.

## Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `GATEWAY_MCP_API_KEY` | unset | Bearer key for every `/v1/mcp/*` route; unset → 503 (fail closed) |
| `GATEWAY_DATABASE_URL` | unset | Cost-ledger location for the budget service (falls back to an in-memory ledger) |
| `MCP_GOVERNANCE_SEED_DEMO` | unset | `1` seeds the demo approval `aprv1` (test fixture only) |

The governance stores use SQLite via `open_mcp_db()`; the app owns an
in-memory DB unless a connection is passed to the factory.

## Security model

- Fail closed at every boundary: no key → 503, wrong key / missing tenant
  → 401, unknown server/tool → 404, default policy deny.
- API key compared with `secrets.compare_digest` (constant time).
- Error bodies never echo internal state (searched ids, reasons) — the
  detail goes to the server log only (S5). The one deliberate exception:
  `approval_id`, which the caller needs to act on an approval.
- Tool args are JSON-Schema validated but **not** sanitized for injection
  patterns (S8, accepted risk — the governance layer is a gate and args
  are forwarded to MCP servers, never echoed back to the LLM).
- Single-tenant scope in v1 (S16): `X-Tenant-Id` is enforced but the
  tables are not tenant-partitioned. Do **not** mount this app on a
  multi-tenant gateway without adding tenant columns and filters.
- Demo data is gated behind `MCP_GOVERNANCE_SEED_DEMO=1` (S9); production
  fresh starts have zero phantom approvals.
- Audit and approval args are persisted PII-redacted; secrets are never
  stored.

## See also

- [MCP Governance API reference](api/mcp-governance-api.md) — endpoints,
  auth, request/response examples, error mapping.
- [Architecture & API contract](architecture/mcp-governance.md) — the
  normative design (schemas, persistence, engine, security decisions).
- [MCP governance example](examples/mcp_governance.py) — runnable offline
  walkthrough of every component and the REST surface.
