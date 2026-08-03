# MCP Governance Module — Architecture & API Contract

**Project:** LLM Budget Gateway
**Version:** 9.4.0 (feature: MCP Server Governance)
**Status:** Approved design — normative for tests and implementation
**Author:** code-architect (kanban task t_5024dc56)
**Date:** 2026-08-02

This document is the single source of truth for the `mcp_governance/` module.
It defines the exact package layout, class/function signatures, data schemas,
REST contracts, persistence schema, and integration points so that the
pre-tester can write interface and behavioral tests **without ambiguity**, and
the implementer can satisfy them without guessing. All signatures below are
normative: names, parameter order, keyword-only markers, type hints, defaults,
and exception behavior.

---

## 1. Goals & scope

Add tool-level governance for MCP (Model Context Protocol) servers to the LLM
Budget Gateway:

1. **MCP server registry** — register, list, version, and retire MCP servers
   with their tool inventory (name, transport, version, tools).
2. **Per-tool access control** — allow / deny / approval policies scoped per
   user, team, project (and a `global` default scope).
3. **Per-tool budgets** — cost ceilings per tool call, reusing the existing
   budget-enforcement machinery (`BudgetExceededError`, window semantics, cost
   ledger).
4. **Audit trail** — every tool-call attempt logged with caller, tool, args
   (PII-redacted), decision, cost, latency, timestamp.
5. **Policy engine** — reusable rules: SSRF guard on tool URLs, PII redaction,
   approval gates (four-eyes).
6. **REST API** — `POST /v1/mcp/servers`, `GET /v1/mcp/servers/{id}/tools`,
   `POST /v1/mcp/policies`, `GET /v1/mcp/audit`, plus the CRUD companions and
   approval/report endpoints.
7. **Integration** — Assurance Center (governance report), cost tracking
   ledger (tool-call cost attribution), approval-gated automation.

**Out of scope (v1):** executing tool calls through the gateway (the module
governs calls; an MCP proxy adapter invokes `MCPPolicyEngine.before_call` /
`after_call` around real execution), tenant-partitioned data (the
`X-Tenant-Id` header is required for auth but data is not tenant-scoped, same
convention as the fleet/assurance apps), and a Redis counter store.

**Fail-closed posture:** the default policy effect is `deny`; an unregistered
tool call is rejected; a retired server is rejected; an unknown SSRF verdict
is a block; an approval-gated tool call is blocked until a human approves.

**Security decisions (security review S1-S16):**

- **S16 — single-tenant scope.** `X-Tenant-Id` is enforced (missing -> 401)
  but none of the six governance tables carry a tenant column, so data is
  intentionally single-tenant in v1 (same convention as fleet/assurance).
  Mounting this app on a multi-tenant gateway REQUIRES adding tenant columns
  + filters first — until then it is a cross-tenant IDOR surface.
- **S8 — accepted risk: no arg injection sanitization.** Tool args are
  JSON-Schema validated against the registered `input_schema` (S2) but no
  injection-pattern sanitization is applied. Rationale: the governance layer
  is a gate, and args are forwarded to MCP servers — never echoed back to the
  LLM — so OWASP LLM01 (indirect prompt injection) does not apply here.
- **S9 — demo data gated.** The demo approval `aprv1` is seeded only when
  `MCP_GOVERNANCE_SEED_DEMO=1` (test fixture); production fresh starts have
  zero phantom approvals.

---

## 2. Module layout

New package inside the existing `src/` layout:

```
src/llm_budget_gateway/mcp_governance/
├── __init__.py        # public API re-exports (see §2.1)
├── exceptions.py      # MCPGovernanceError hierarchy, each with status_code
├── schemas.py         # pydantic v2 request/response models (fully functional in RED phase)
├── db.py              # open_mcp_db() connection helper
├── registry.py        # MCPRegistry: servers + tool inventory (SQLite)
├── policy.py          # ToolPolicyStore + PolicyEvaluator
├── budgets.py         # ToolBudgetStore + ToolBudgetService
├── audit.py           # AuditStore
├── rules.py           # SSRFGuard, PIIRedactor, ApprovalStore, ApprovalGate
├── engine.py          # MCPPolicyEngine + CallContext
├── discovery.py       # MCPDiscoveryAdapter (live tool inventory via MCP SDK, lazy import)
├── integration.py     # MCPGovernanceReport, ApprovalNotifier (Assurance Center hook)
└── api.py             # create_mcp_governance_app() — FastAPI REST + dashboard
```

Import rules (acyclic):

- `exceptions.py` imports nothing from the package.
- `schemas.py` imports nothing from the package.
- `db.py` imports nothing from the package.
- `registry.py`, `policy.py`, `budgets.py`, `audit.py`, `rules.py` import from
  `exceptions.py` and `schemas.py` only.
- `engine.py` imports from all of the above.
- `api.py` imports from `engine.py`, `integration.py`, and all stores.
- `discovery.py` imports `schemas.py` only (plus a **lazy** `import mcp` inside
  its method — module import must never require the MCP SDK).
- `integration.py` imports `schemas.py` only.

### 2.1 Public exports (`__init__.py`)

```python
from .api import create_mcp_governance_app
from .audit import AuditStore
from .budgets import ToolBudgetService, ToolBudgetStore
from .db import open_mcp_db
from .discovery import MCPDiscoveryAdapter
from .engine import MCPPolicyEngine, CallContext
from .exceptions import (
    AccessDeniedError,
    ApprovalNotFoundError,
    ApprovalRequiredError,
    ApprovalStateError,
    BudgetNotFoundError,
    DuplicateBudgetError,
    DuplicatePolicyError,
    DuplicateServerError,
    MCPDiscoveryError,
    MCPGovernanceError,
    MCPServerNotFoundError,
    MCPToolNotFoundError,
    PolicyNotFoundError,
    PolicyViolationError,
)
from .integration import ApprovalNotifier, MCPGovernanceReport, NullApprovalNotifier
from .policy import PolicyEvaluator, ToolPolicyStore
from .registry import MCPRegistry
from .rules import ApprovalGate, PIIRedactor, SSRFGuard
from .schemas import (
    ApprovalRequest,
    AuditEvent,
    AuditPage,
    MCPServer,
    MCPRegistryRequest,
    PolicyDecision,
    RuleVerdict,
    ToolBudget,
    ToolBudgetRequest,
    ToolInfo,
    ToolPolicy,
    ToolPolicyRequest,
)
```

---

## 3. Shared conventions

- **Timestamps:** epoch seconds (`int`). Every stateful component accepts an
  injectable `clock: Callable[[], int] | None = None` (defaults to
  `lambda: int(time.time())`) so tests are deterministic.
- **Identifiers:** generated with `secrets.token_hex(8)` (matching
  `governance.py`). Client-supplied ids are never accepted.
- **Persistence:** SQLite via a **shared connection**. All stores take a
  `sqlite3.Connection` in `__init__` (they never open or close their own
  connection; the owner — the API factory or the test — does). `:memory:`
  connections are therefore safe across stores.
- **Async:** budget and engine methods are `async`. The repo's pytest config
  is `asyncio_mode = "strict"`, so async tests MUST carry
  `@pytest.mark.asyncio`.
- **Stub contract (RED phase):** `__init__` methods and pydantic schemas,
  exceptions, and `db.py` are fully functional so interface tests pass
  immediately (mirroring the `budget_enforcement.py` stub convention). Every
  other public method raises `NotImplementedError` until the implementer
  lands it.
- **HTTP status mapping** is defined per exception in §8.1.

---

## 4. Data schemas (`schemas.py`)

All models are `pydantic.BaseModel` (pydantic v2, already a dependency).
Request models use `model_config = ConfigDict(extra="forbid")` so unknown
fields are a 422. `Literal` fields reject unknown values with 422.

### 4.1 `MCPServer`

```python
class MCPServer(BaseModel):
    server_id: str
    name: str                      # ^[A-Za-z0-9_.-]{1,64}$  (else 422)
    transport: Literal["stdio", "sse", "http", "websocket"]
    endpoint: str | None = None    # required (non-empty) when transport != "stdio"
    version: str = "1.0.0"         # ^\d+\.\d+\.\d+$  (else 422)
    description: str = ""
    status: Literal["active", "retired"] = "active"
    config: dict[str, Any] = Field(default_factory=dict)
    created_at: int
    updated_at: int
```

The server row does **not** embed the tool inventory; tools are stored in the
`mcp_tools` table and exposed via `GET /v1/mcp/servers/{id}/tools`.

### 4.2 `ToolInfo`

```python
class ToolInfo(BaseModel):
    name: str                      # ^[A-Za-z0-9_.-]{1,128}$  (else 422)
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)  # JSON Schema
    enabled: bool = True
```

### 4.3 `MCPRegistryRequest`

```python
class MCPRegistryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str                      # ^[A-Za-z0-9_.-]{1,64}$
    transport: Literal["stdio", "sse", "http", "websocket"]
    endpoint: str | None = None    # required when transport != "stdio"
    version: str = "1.0.0"
    description: str = ""
    tools: list[ToolInfo] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
```

Validation rules:

- `endpoint` must be a non-empty string when `transport != "stdio"` (422).
- Tool names must be unique within the request (422 on duplicates).
- `transport == "stdio"` servers carry their launch command in
  `config["command"]` (a list of strings) — optional, not validated in v1.

### 4.4 `ToolPolicy` and `ToolPolicyRequest`

```python
class ToolPolicy(BaseModel):
    policy_id: str
    scope_kind: Literal["user", "team", "project", "global"]
    scope_key: str                 # non-empty (422)
    server_id: str | None = None   # None = any server (wildcard)
    tool_name: str | None = None   # None = any tool on the selected server
    effect: Literal["allow", "deny", "approval"]
    description: str = ""
    created_by: str = "admin"
    created_at: int

class ToolPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope_kind: Literal["user", "team", "project", "global"]
    scope_key: str
    server_id: str | None = None
    tool_name: str | None = None
    effect: Literal["allow", "deny", "approval"]
    description: str = ""
```

Wildcard rule (enforced, 422 otherwise): **`tool_name` may be set only when
`server_id` is set.** `server_id=None` implies `tool_name=None` (a
server-wide / global-tool policy).

### 4.5 `ToolBudget` and `ToolBudgetRequest`

```python
class ToolBudget(BaseModel):
    budget_id: str
    scope_kind: Literal["user", "team", "project", "global"]
    scope_key: str                 # non-empty
    server_id: str | None = None   # None = any server
    tool_name: str | None = None   # None = any tool (requires server_id, same rule as policy)
    soft_limit: float | None = None  # USD, >= 0; alert only
    hard_limit: float | None = None  # USD, >= 0; blocks when spend >= limit
    window: str = "30d"            # "30s"|"30m"|"30h"|"30d"|"daily"|"monthly"|<n><s|m|h|d>
    created_at: int

class ToolBudgetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope_kind: Literal["user", "team", "project", "global"]
    scope_key: str
    server_id: str | None = None
    tool_name: str | None = None
    soft_limit: float | None = None
    hard_limit: float | None = None
    window: str = "30d"
```

Validation: at least one of `soft_limit` / `hard_limit` must be set (422);
limits must be finite and `>= 0` (422); `window` must parse per
`budget_window_seconds()` (422 on unknown window strings).

### 4.6 `AuditEvent` and `AuditPage`

```python
class AuditEvent(BaseModel):
    event_id: str
    server_id: str
    tool_name: str
    caller: str                    # principal (user id or api key) that invoked the tool
    scope_kind: Literal["user", "team", "project", "global"]
    scope_key: str
    args: dict[str, Any] = Field(default_factory=dict)   # PII-redacted copy
    decision: Literal["allowed", "denied", "approval_required", "approved", "error"]
    status: Literal["started", "completed", "blocked", "failed"]
    reason: str | None = None      # e.g. "ssrf: private address 10.0.0.1", "budget exceeded"
    cost: float = 0.0              # USD
    latency_ms: int = 0
    timestamp: int
    redacted: bool = True          # True iff at least one PII pattern was replaced in args
    approval_id: str | None = None
    request_id: str | None = None  # correlation with the gateway request

class AuditPage(BaseModel):
    object: Literal["list"] = "list"
    data: list[AuditEvent]
    limit: int
    offset: int
    total: int                     # count ignoring limit/offset
```

Decision/status semantics (the engine writes exactly one row per attempt):

| Outcome                                            | decision            | status      | cost  |
|----------------------------------------------------|---------------------|-------------|-------|
| allowed, call succeeded                            | `allowed`           | `completed` | actual|
| allowed, tool raised an error                      | `allowed`           | `failed`    | actual|
| one-time approval consumed, call succeeded         | `approved`          | `completed` | actual|
| access policy denied                               | `denied`            | `blocked`   | 0     |
| approval required (pending)                        | `approval_required` | `blocked`   | 0     |
| SSRF / policy violation                            | `denied`            | `blocked`   | 0     |
| budget ceiling exceeded                            | `denied`            | `blocked`   | 0     |
| server retired / tool disabled                     | `denied`            | `blocked`   | 0     |

### 4.7 `PolicyDecision`

```python
class PolicyDecision(BaseModel):
    effect: Literal["allow", "deny", "approval"]
    policy_id: str | None = None
    reason: str                  # "allowed by policy <id>" | "denied by policy <id>" | "no policy matched; default deny"
    matched_scope: str | None = None   # e.g. "user:alice"
```

### 4.8 `RuleVerdict`

```python
class RuleVerdict(BaseModel):
    allowed: bool
    rule: str                    # "ssrf_guard"
    reason: str                  # deterministic, human-readable
    detail: str | None = None    # offending URL, if any
```

### 4.9 `ApprovalRequest`

```python
class ApprovalRequest(BaseModel):
    approval_id: str
    server_id: str
    tool_name: str
    caller: str
    scope_kind: Literal["user", "team", "project", "global"]
    scope_key: str
    args_redacted: dict[str, Any] = Field(default_factory=dict)
    args_hash: str               # sha256 hexdigest of canonical JSON, see §7.3
    status: Literal["pending", "approved", "rejected", "consumed", "expired"]
    requested_at: int
    decided_at: int | None = None
    decided_by: str | None = None
    expires_at: int | None = None
```

---

## 5. Persistence schema (SQLite)

Created by each store's `__init__` (`CREATE TABLE IF NOT EXISTS`). Wildcards
(`server_id`/`tool_name` in policies/budgets) are stored as SQL `NULL`;
uniqueness is enforced **application-side** (SQLite treats NULLs as distinct
in UNIQUE constraints).

```sql
CREATE TABLE IF NOT EXISTS mcp_servers (
    server_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    transport   TEXT NOT NULL,
    endpoint    TEXT,
    version     TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active',
    config_json TEXT NOT NULL DEFAULT '{}',
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL,
    UNIQUE (name, version)
);

CREATE TABLE IF NOT EXISTS mcp_tools (
    server_id          TEXT NOT NULL REFERENCES mcp_servers(server_id) ON DELETE CASCADE,
    tool_name          TEXT NOT NULL,
    description        TEXT NOT NULL DEFAULT '',
    input_schema_json  TEXT NOT NULL DEFAULT '{}',
    enabled            INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (server_id, tool_name)
);

CREATE TABLE IF NOT EXISTS mcp_policies (
    policy_id   TEXT PRIMARY KEY,
    scope_kind  TEXT NOT NULL,
    scope_key   TEXT NOT NULL,
    server_id   TEXT,              -- NULL = any server
    tool_name   TEXT,              -- NULL = any tool on server
    effect      TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_by  TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS mcp_budgets (
    budget_id   TEXT PRIMARY KEY,
    scope_kind  TEXT NOT NULL,
    scope_key   TEXT NOT NULL,
    server_id   TEXT,              -- NULL = any server
    tool_name   TEXT,              -- NULL = any tool on server
    soft_limit  REAL,
    hard_limit  REAL,
    window      TEXT NOT NULL DEFAULT '30d',
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS mcp_approvals (
    approval_id  TEXT PRIMARY KEY,
    server_id    TEXT NOT NULL,
    tool_name    TEXT NOT NULL,
    caller       TEXT NOT NULL,
    scope_kind   TEXT NOT NULL,
    scope_key    TEXT NOT NULL,
    args_json    TEXT NOT NULL,    -- PII-redacted
    args_hash    TEXT NOT NULL,
    status       TEXT NOT NULL,
    requested_at INTEGER NOT NULL,
    decided_at   INTEGER,
    decided_by   TEXT,
    expires_at   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_mcp_approvals_hash   ON mcp_approvals (args_hash);
CREATE INDEX IF NOT EXISTS idx_mcp_approvals_status ON mcp_approvals (status, caller);

CREATE TABLE IF NOT EXISTS mcp_audit_events (
    event_id    TEXT PRIMARY KEY,
    server_id   TEXT NOT NULL,
    tool_name   TEXT NOT NULL,
    caller      TEXT NOT NULL,
    scope_kind  TEXT NOT NULL,
    scope_key   TEXT NOT NULL,
    args_json   TEXT NOT NULL,     -- PII-redacted
    decision    TEXT NOT NULL,
    status      TEXT NOT NULL,
    reason      TEXT,
    cost        REAL NOT NULL DEFAULT 0,
    latency_ms  INTEGER NOT NULL DEFAULT 0,
    timestamp   INTEGER NOT NULL,
    redacted    INTEGER NOT NULL DEFAULT 1,
    approval_id TEXT,
    request_id  TEXT
);
CREATE INDEX IF NOT EXISTS idx_mcp_audit_caller  ON mcp_audit_events (caller, timestamp);
CREATE INDEX IF NOT EXISTS idx_mcp_audit_tool    ON mcp_audit_events (server_id, tool_name, timestamp);
CREATE INDEX IF NOT EXISTS idx_mcp_audit_ts      ON mcp_audit_events (timestamp);
```

### 5.1 `db.py`

```python
def open_mcp_db(db_path: str = ":memory:") -> sqlite3.Connection:
    """Open a connection for the mcp_governance stores.

    - row_factory = sqlite3.Row
    - check_same_thread=False
    - PRAGMA journal_mode=WAL when db_path is not ":memory:" (no-op otherwise)
    """
```

The connection is **not** closed by stores; the creator closes it. Table
creation happens in each store's `__init__` against the shared connection.

---

## 6. Component specifications

### 6.1 `registry.py` — `MCPRegistry`

```python
class MCPRegistry:
    def __init__(self, conn: sqlite3.Connection, clock: Callable[[], int] | None = None) -> None:
        """Creates mcp_servers + mcp_tools tables (IF NOT EXISTS)."""

    def register(self, request: MCPRegistryRequest) -> MCPServer:
        """Insert a server row + its tool inventory.

        - server_id = secrets.token_hex(8); created_at = updated_at = clock()
        - (name, version) already present  -> raises DuplicateServerError
        - Tools are upserted into mcp_tools for the new server_id.
        """

    def get_server(self, server_id: str) -> MCPServer:
        """Return by id (any status). Unknown id -> MCPServerNotFoundError."""

    def get_server_by_name(self, name: str, version: str | None = None) -> MCPServer:
        """Return the row for (name, version); when version is None return the
        HIGHEST version row for name. Unknown -> MCPServerNotFoundError.
        Versions compare as (major, minor, patch) int tuples."""

    def list_servers(self, include_retired: bool = False) -> list[MCPServer]:
        """One row per name — the highest version — ordered by name asc.
        Retired names are excluded unless include_retired=True."""

    def list_versions(self, name: str) -> list[MCPServer]:
        """All rows for name, highest version first. Unknown name -> []."""

    def retire_server(self, server_id: str) -> MCPServer:
        """Set status='retired', updated_at=clock(). Unknown id -> MCPServerNotFoundError.
        Retiring an already-retired server is idempotent (returns the row)."""

    def list_tools(self, server_id: str) -> list[ToolInfo]:
        """All tools for the server, ordered by name asc.
        Unknown server -> MCPServerNotFoundError. Empty inventory -> []."""

    def get_tool(self, server_id: str, tool_name: str) -> ToolInfo:
        """Unknown server -> MCPServerNotFoundError; unknown tool -> MCPToolNotFoundError."""

    def has_tool(self, server_id: str, tool_name: str) -> bool:
        """True iff the server exists and the tool is registered (regardless of enabled)."""
```

### 6.2 `policy.py` — `ToolPolicyStore` and `PolicyEvaluator`

```python
class ToolPolicyStore:
    def __init__(self, conn: sqlite3.Connection, clock: Callable[[], int] | None = None,
                 default_effect: str = "deny") -> None:
        """Creates the mcp_policies table. default_effect in {"allow","deny","approval"}."""

    def create_policy(self, request: ToolPolicyRequest) -> ToolPolicy:
        """policy_id = secrets.token_hex(8); created_at = clock().
        An existing policy with the same (scope_kind, scope_key, server_id, tool_name)
        4-tuple (NULL-aware) -> DuplicatePolicyError."""

    def list_policies(self, *, scope_kind: str | None = None,
                      scope_key: str | None = None,
                      server_id: str | None = None,
                      tool_name: str | None = None) -> list[ToolPolicy]:
        """Filter on exact equality (NULL filters are ignored); order by created_at asc, policy_id asc."""

    def get_policy(self, policy_id: str) -> ToolPolicy:
        """Unknown id -> PolicyNotFoundError."""

    def delete_policy(self, policy_id: str) -> None:
        """Unknown id -> PolicyNotFoundError. Deletion is permanent."""

    @property
    def default_effect(self) -> str: ...


class PolicyEvaluator:
    def __init__(self, store: ToolPolicyStore) -> None: ...

    def decide(self, *, scopes: list[BudgetScope], server_id: str,
               tool_name: str) -> PolicyDecision:
        """Resolve allow/deny/approval. See §6.2.1 for the exact algorithm."""
```

#### 6.2.1 Resolution algorithm (normative)

Inputs: `scopes` — the caller's scope identities, most specific first, e.g.
`[BudgetScope("user","alice"), BudgetScope("team","eng"), BudgetScope("project","p1"), BudgetScope("global","default")]`.
`BudgetScope` is the existing frozen dataclass from `budget_enforcement.py`.

1. **Candidate set:** every policy where
   - `scope_kind == "global"`, or `scope_kind`/`scope_key` exactly equal one of
     the caller's scopes (kind AND key must both match), **and**
   - tool selector matches: `policy.server_id is None` OR
     `policy.server_id == server_id`; and `policy.tool_name is None` OR
     `policy.tool_name == tool_name`.
2. **Specificity score:** `scope_rank * 10 + tool_rank` where
   - `scope_rank = {"user": 3, "team": 2, "project": 1, "global": 0}[kind]`
   - `tool_rank = 3` when both server and tool are exact, `2` when
     `server_id` exact and `tool_name` wildcard, `1` when `server_id`
     wildcard.
3. **Winner:** among candidates with the maximum score, effect precedence
   `deny > approval > allow`. Ties are broken by `policy_id` (lexicographic
   smallest wins) for determinism.
4. **No candidates:** return `PolicyDecision(effect=store.default_effect,
   policy_id=None, reason="no policy matched; default <effect>",
   matched_scope=None)`.
5. `reason` strings: `"allowed by policy <id>"`, `"denied by policy <id>"`,
   `"approval required by policy <id>"`; `matched_scope` = the matched
   `"<kind>:<key>"` (or `None` for global policies).

### 6.3 `budgets.py` — `ToolBudgetStore` and `ToolBudgetService`

```python
class ToolBudgetStore:
    def __init__(self, conn: sqlite3.Connection, clock: Callable[[], int] | None = None) -> None:
        """Creates the mcp_budgets table."""

    def create_budget(self, request: ToolBudgetRequest) -> ToolBudget:
        """budget_id = secrets.token_hex(8); created_at = clock().
        Same 4-tuple uniqueness rule as policies -> DuplicateBudgetError."""

    def list_budgets(self, *, scope_kind: str | None = None,
                     scope_key: str | None = None,
                     server_id: str | None = None,
                     tool_name: str | None = None) -> list[ToolBudget]:
        """Exact-equality filters; order created_at asc, budget_id asc."""

    def get_budget(self, budget_id: str) -> ToolBudget:
        """Unknown id -> BudgetNotFoundError."""

    def delete_budget(self, budget_id: str) -> None:
        """Unknown id -> BudgetNotFoundError."""


class ToolBudgetService:
    """Per-tool cost ceilings. Reuses BudgetScope, BudgetExceededError and the
    window semantics of the existing budget enforcement engine, enforced
    against the shared cost ledger (extended with tool_name/project, §9)."""

    def __init__(self, tracker: CostTracker, budgets: ToolBudgetStore,
                 now_fn: Callable[[], int] | None = None) -> None: ...

    def applicable_budgets(self, scopes: list[BudgetScope], server_id: str,
                           tool_name: str) -> list[ToolBudget]:
        """Budgets whose scope matches a caller scope and whose tool selector
        matches (same matching rules as §6.2.1 steps 1–2). Ordered most
        specific first."""

    async def check(self, scopes: list[BudgetScope], server_id: str,
                    tool_name: str) -> None:
        """For each applicable budget with a hard_limit:
        since = now - budget_window_seconds(budget.window)
        spend = await tracker.spend_since(scope_key, since, tool_name=f"{server_id}:{tool_name}")
        if spend >= hard_limit: raise BudgetExceededError(scope, spend, hard_limit)
        No applicable budgets -> no-op."""

    async def soft_exceeded(self, scopes: list[BudgetScope], server_id: str,
                            tool_name: str) -> list[BudgetScope]:
        """Return caller scopes whose applicable soft_limit is exceeded
        (spend >= soft_limit). Never raises."""

    async def record_usage(self, *, event: AuditEvent) -> None:
        """Persist tool-call cost attribution to the ledger.

        Builds a UsageRecord (see §9) with:
          request_id = event.event_id, api_key = event.caller,
          user_id/team/project from the caller scope columns,
          model = f"mcp:{event.server_id}:{event.tool_name}", provider = "mcp",
          prompt/completion/total tokens = 0, input/output cost = 0,
          total_cost = event.cost, latency_ms = event.latency_ms,
          status = "success" if event.status == "completed" else "error",
          timestamp = event.timestamp, tool_name = f"{server_id}:{tool_name}"
        and writes it via tracker.record(...). Only called for
        status == "completed" or "failed"."""

    def canonical_tool(self, server_id: str, tool_name: str) -> str:
        """Return f"{server_id}:{tool_name}" — the ledger tool_name value and
        the key used for per-tool spend lookups."""
```

The window math lives in the existing engine:
`budget_window_seconds(window, now_fn)` (see §9). `BudgetExceededError`
marshals to HTTP 412 (Portkey convention), `RateLimitExceededError` to 429
(not used by tool budgets in v1, reserved).

### 6.4 `audit.py` — `AuditStore`

```python
class AuditStore:
    def __init__(self, conn: sqlite3.Connection, clock: Callable[[], int] | None = None) -> None:
        """Creates the mcp_audit_events table."""

    def append(self, event: AuditEvent) -> AuditEvent:
        """Insert the event. When event.event_id == "", generate
        secrets.token_hex(8). Re-append with an existing event_id replaces
        (INSERT OR REPLACE). Returns the stored event."""

    def query(self, *, caller: str | None = None, server_id: str | None = None,
              tool_name: str | None = None, decision: str | None = None,
              status: str | None = None, since: int | None = None,
              until: int | None = None, limit: int = 50,
              offset: int = 0) -> AuditPage:
        """Filter on exact equality / inclusive timestamp range
        (timestamp >= since, timestamp <= until). Order timestamp DESC,
        event_id DESC. limit is clamped to [1, 500], offset >= 0.
        decision/status values outside the allowed Literal sets -> ValueError
        (marshals to 422). total = count ignoring limit/offset."""
```

### 6.5 `rules.py` — `SSRFGuard`, `PIIRedactor`, `ApprovalStore`, `ApprovalGate`

```python
class SSRFGuard:
    """Blocks http(s) URLs whose host resolves to private/reserved address space."""

    def __init__(self, allowed_hosts: Sequence[str] = (),
                 url_fields: Sequence[str] = ("url", "endpoint", "webhook", "callback_url")) -> None: ...

    def check(self, args: Mapping[str, Any]) -> RuleVerdict:
        """Inspect every value of the configured url_fields, recursively
        (dict values and list items). Non-http(s) schemes (file://, ftp://, ...)
        -> blocked. IP literals: private/loopback/link-local/reserved/multicast
        -> blocked. Hostnames: resolve ALL addresses via socket.getaddrinfo;
        ANY private/loopback/link-local/reserved address -> blocked. Exact
        (case-insensitive) host match in allowed_hosts -> allowed. No
        candidates -> RuleVerdict(allowed=True, rule="ssrf_guard",
        reason="no url fields", detail=None)."""

    def extract_urls(self, args: Mapping[str, Any]) -> list[str]:
        """Return the candidate URL strings in deterministic (walk) order."""
```

Deterministic `reason` strings (testable):

- `"ssrf: unsupported scheme <scheme>"`
- `"ssrf: loopback address <ip>"`
- `"ssrf: private address <ip>"`
- `"ssrf: link-local address <ip>"`
- `"ssrf: reserved address <ip>"`
- `"ssrf: multicast address <ip>"`
- `"ssrf: hostname <host> resolves to blocked address <ip>"`
- `"ssrf: unknown host <host>"`
- `"ssrf: invalid url <raw>"`
- `"allowed by allowlist <host>"`

```python
class PIIRedactor:
    """Deterministic PII masking for audit/approval persistence."""

    def __init__(self, patterns: Mapping[str, str] | None = None) -> None:
        """patterns: name -> regex. Defaults (name -> regex):
          email          r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
          phone          r"\+?\d[\d\s().-]{7,}\d"
          ssn            r"\b\d{3}-\d{2}-\d{4}\b"
          credit_card    r"\b(?:\d[ -]?){13,19}\b"
          api_key        r"\bsk-[A-Za-z0-9]{20,}\b"
          bearer_token   r"\bBearer\s+[A-Za-z0-9._~+/-]+=*\b"
          aws_access_key r"\bAKIA[0-9A-Z]{16}\b"
        Custom patterns replace the defaults entirely (no merging)."""

    def redact(self, value: Any) -> Any:
        """Deep copy of value with every string recursively scanned; each
        matched substring replaced by "[REDACTED:<name>]". Dict keys are never
        redacted; non-string scalars pass through unchanged; unmatched strings
        pass through unchanged."""

    def redact_text(self, text: str) -> str:
        """Redact a single string (used by redact() for string leaves)."""

    def scan(self, value: Any) -> list[str]:
        """Return the sorted unique pattern names that WOULD match value.
        Pure inspection — no copies. Used by the engine to set
        AuditEvent.redacted and by the assurance report."""
```

```python
class ApprovalStore:
    def __init__(self, conn: sqlite3.Connection, clock: Callable[[], int] | None = None) -> None:
        """Creates the mcp_approvals table."""

    def insert(self, approval: ApprovalRequest) -> ApprovalRequest: ...
    def get(self, approval_id: str) -> ApprovalRequest:
        """Unknown id -> ApprovalNotFoundError."""
    def update_status(self, approval_id: str, *, status: str, decided_by: str | None = None,
                      decided_at: int | None = None) -> ApprovalRequest:
        """Set status (+ decided_by/decided_at when given). Unknown id -> ApprovalNotFoundError."""
    def list(self, *, status: str | None = None, caller: str | None = None) -> list[ApprovalRequest]:
        """Order requested_at DESC, approval_id DESC."""


class ApprovalGate:
    """Four-eyes gate for approval-gated tool calls (consume-once semantics)."""

    def __init__(self, store: ApprovalStore, ttl_seconds: int | None = 3600) -> None:
        """ttl_seconds None disables expiry."""

    def requires_approval(self, policy: ToolPolicy) -> bool:
        """True iff policy.effect == "approval"."""

    def create_request(self, *, policy: ToolPolicy, caller: str,
                       scopes: list[BudgetScope], server_id: str, tool_name: str,
                       args: Mapping[str, Any]) -> ApprovalRequest:
        """Build and insert a pending ApprovalRequest:
          approval_id = secrets.token_hex(8)
          args_redacted = PIIRedactor().redact(dict(args))   # default redactor
          args_hash = sha256 hexdigest of
                      json.dumps(args, sort_keys=True, separators=(",", ":"))
          expires_at = requested_at + ttl_seconds (when ttl_seconds is not None)
        """

    def approve(self, approval_id: str, actor: str) -> ApprovalRequest:
        """pending -> approved (decided_by=actor, decided_at=clock()).
        Any other status -> ApprovalStateError."""

    def reject(self, approval_id: str, actor: str) -> ApprovalRequest:
        """pending -> rejected. Any other status -> ApprovalStateError."""

    def consume(self, approval_id: str, actor: str) -> ApprovalRequest:
        """approved -> consumed (single-use). Any other status -> ApprovalStateError."""

    def find_approved(self, *, caller: str, server_id: str, tool_name: str,
                      args_hash: str) -> ApprovalRequest | None:
        """Most recent (requested_at DESC) approval with status="approved",
        caller/server/tool/args_hash match, and expires_at is None or
        expires_at >= clock(). Returns None when absent or expired."""

    def expire_stale(self, now: int | None = None) -> int:
        """pending requests with expires_at < now -> expired; return count.
        now defaults to clock()."""
```

### 6.6 `engine.py` — `MCPPolicyEngine` and `CallContext`

```python
@dataclass
class CallContext:
    call_id: str                  # secrets.token_hex(8)
    request_id: str | None
    caller: str
    scopes: list[BudgetScope]     # most specific first
    server_id: str
    tool_name: str
    args_redacted: dict[str, Any]
    decision: str                 # "allowed" | "approved"
    policy_id: str | None
    approval_id: str | None
    reason: str | None


class MCPPolicyEngine:
    def __init__(self, *, registry: MCPRegistry, policies: ToolPolicyStore,
                 budgets: ToolBudgetService, audit: AuditStore,
                 approvals: ApprovalStore, redactor: PIIRedactor,
                 ssrf: SSRFGuard, notifier: ApprovalNotifier | None = None,
                 request_id_factory: Callable[[], str] | None = None) -> None: ...

    async def before_call(self, *, caller: str, scopes: list[BudgetScope],
                          server_id: str, tool_name: str,
                          args: Mapping[str, Any],
                          request_id: str | None = None) -> CallContext:
        """Gate a tool call BEFORE execution. Raises on block (each block is
        also written to the audit trail first). Returns a CallContext for
        after_call. Steps, in order:
          1. registry.get_server(server_id)          -> MCPServerNotFoundError (404)
          2. server.status == "retired"              -> AccessDeniedError("server is retired") (403)
          3. registry.get_tool(server_id, tool_name) -> MCPToolNotFoundError (404)
          4. tool.enabled is False                   -> AccessDeniedError("tool is disabled") (403)
          5. decision = PolicyEvaluator(store=policies).decide(...)
             - deny      -> audit(blocked) + AccessDeniedError (403)
             - approval  -> gate:
                 hash = ApprovalGate args_hash over raw args
                 approved = approvals.find_approved(...)
                 approved -> approvals.consume(...); decision = "approved"
                 else    -> reuse a pending request with the same args_hash
                            if one exists, else gate.create_request(...);
                            notifier.notify(request) when notifier set;
                            audit(blocked) + ApprovalRequiredError (409)
             - allow     -> decision = "allowed"
          6. verdict = ssrf.check(args)
             not allowed -> audit(blocked) + PolicyViolationError (403)
          7. await budgets.check(scopes, server_id, tool_name)
             BudgetExceededError -> audit(blocked) + re-raise (412)
          8. Build CallContext with args_redacted = redactor.redact(dict(args))
        """

    async def after_call(self, ctx: CallContext, *, status: str, cost: float,
                         latency_ms: int) -> AuditEvent:
        """Record the outcome AFTER execution. status in {"completed","failed"}.
        Builds the AuditEvent (see §4.6), appends it, and when
        status == "completed" calls budgets.record_usage(event) so the cost
        lands in the ledger. Returns the stored event."""
```

Notes for the implementer and tester:

- The engine does **not** know about `X-Tenant-Id`; the API layer handles auth.
- `before_call` writes the blocked audit row with the **redacted** args
  (`redactor.redact(dict(args))`), `cost=0.0`, `latency_ms=0`.
- `audit rows`: `event_id` generated by `AuditStore.append` (pass `""`).

### 6.7 `discovery.py` — `MCPDiscoveryAdapter`

```python
class MCPDiscoveryAdapter:
    """Live tool inventory via the official MCP SDK (lazy-imported)."""

    async def discover_tools(self, *, transport: str,
                             endpoint: str | None = None,
                             command: list[str] | None = None) -> list[ToolInfo]:
        """Connect to the server and return its tool inventory via
        ClientSession.list_tools().

        - transport "http" | "sse": endpoint is the server URL (required).
        - transport "stdio": command is the launch argv (required).
        - transport "websocket": endpoint ws(s):// URL (required).
        - Connection/negotiation failure -> MCPDiscoveryError.
        """
```

The `import mcp` happens **inside** `discover_tools` (`import mcp` /
`from mcp import ClientSession, StdioServerParameters` / streamable-http or
SSE client). No other module imports `mcp`. Tests that exercise discovery use
`pytest.importorskip("mcp")`. Registration in v1 accepts an explicit
`tools` list; the adapter is the recommended way to populate it:

```python
tools = await MCPDiscoveryAdapter().discover_tools(transport="http", endpoint="https://mcp.example.com/mcp")
server = registry.register(MCPRegistryRequest(name="example", transport="http",
                                             endpoint="https://mcp.example.com/mcp", tools=tools))
```

### 6.8 `integration.py` — Assurance Center report and approval notifier

```python
class ApprovalNotifier(Protocol):
    def notify(self, approval: ApprovalRequest) -> None: ...


class NullApprovalNotifier:
    """No-op default; the extension point for Slack/webhook/Assurance Center
    automation."""

    def notify(self, approval: ApprovalRequest) -> None: ...


class MCPGovernanceReport:
    """Assurance Center hook — deterministic governance posture snapshot."""

    def build(self, *, registry: MCPRegistry, policies: ToolPolicyStore,
              budgets: ToolBudgetStore, audit: AuditStore,
              approvals: ApprovalStore, since_epoch: int) -> dict[str, object]:
        """Return (all counts int):
          {
            "total_servers": int, "active_servers": int, "retired_servers": int,
            "total_tools": int,                    # distinct (server_id, tool_name) across active servers
            "tools_with_policy": int,              # distinct covered by >= 1 policy
            "tools_with_budget": int,              # distinct covered by >= 1 budget
            "pending_approvals": int,
            "ssrf_blocks_24h": int,                # audit: decision=denied, reason LIKE 'ssrf:%', timestamp >= since_epoch
            "pii_redactions_24h": int,             # audit: redacted=1, timestamp >= since_epoch
            "budget_breaches_24h": int,            # audit: decision=denied, reason LIKE 'budget%', timestamp >= since_epoch
            "risk_tier": "high" | "medium" | "low",
          }
        risk_tier: "high" if pending_approvals > 0 or ssrf_blocks_24h > 0
                  else "medium" if tools_with_policy < total_tools or total_tools == 0
                  else "low"."""

    def assess(self, report: Mapping[str, object]) -> dict[str, object]:
        """Pure-function Assurance Center capability (mirrors fleet style):
          {"risk_tier": str, "gaps": [str], "recommendation": str}
        gaps: "pending approvals", "ssrf blocks in window", "ungoverned tools",
              "tools without budget ceilings", "no servers registered"
        (each present only when the corresponding report condition holds)."""
```

**Assurance Center integration (pending — NOT yet wired):** the design calls
for `assurance_api.py`'s `services` dict to gain a `"mcp-governance"`
capability — `lambda b: MCPGovernanceReport().assess(**b)` — so a request to
the assurance path `.../v1/assurance/mcp-governance` returns the posture
assessment, and the Assurance Center dashboard surfaces it as a new
capability card ("MCP governance"). **As of this release the capability is
NOT registered** (the `services` dict has no `mcp-governance` entry), so
that assurance request currently returns `404 unknown assurance
capability`. Wiring it is tracked as follow-up work; the standalone
`GET /v1/mcp/report` endpoint on the governance app already returns the same
posture snapshot. No change to the assurance suite's other 20 capabilities.

**Cost ledger integration:** `ToolBudgetService.record_usage` writes tool-call
costs into the shared `cost_records` ledger with `tool_name` attribution, so
existing budget reporting and `spend_since` queries see tool spend
(§9, §10.1).

**Approval-gated automation integration:** when `before_call` creates a new
approval request, `notifier.notify(request)` fires (no-op by default).
Approval-gated automation (Slack bots, Assurance Center "Approvals"
capability, `governance.py`-style propose/approve flows) can consume
`ApprovalStore` + `ApprovalGate.approve/reject`.

---

## 7. REST API contracts

### 7.1 Factory and auth

```python
def create_mcp_governance_app(api_key: str | None = None,
                              *, conn: sqlite3.Connection | None = None) -> FastAPI:
    """Build the fail-closed MCP governance app.

    - api_key None -> os.getenv("GATEWAY_MCP_API_KEY", "")
    - conn None -> open_mcp_db(":memory:") owned by the app
    - Wires registry / policy / budget / audit / approval stores, the
      SSRFGuard, PIIRedactor, ToolBudgetService (tracker required — see
      below), MCPPolicyEngine, and the routes below.
    """
```

The factory requires a `CostTracker` for the budget service. It is resolved
from the environment: `GATEWAY_DATABASE_URL` (sqlite path) when set, otherwise
a `CostTracker` over an in-memory `CostStore` is created for the app's
lifetime. (Tests that exercise budgets construct the service directly with a
fake tracker — see §11.)

**Authentication (every `/v1/*` route):**

- `Authorization: Bearer <key>` must equal the configured key, and
  `X-Tenant-Id` must be present and non-empty → else `401`.
- If no key is configured at all → `503` (fail closed; matches
  `create_fleet_app`).
- `GET /mcp` (dashboard) is unauthenticated (matches fleet/assurance).

**Error body:** FastAPI `HTTPException` default `{"detail": "<message>"}`,
except `ApprovalRequiredError` which returns
`JSONResponse(status_code=409, content={"detail": "<reason>", "approval_id": "<id>"})`.

**List shapes:** `{"object": "list", "data": [...]}` (OpenAI-style, matching
`GET /v1/models`).

### 7.2 Endpoints

| Method | Path | Auth | Success | Errors |
|---|---|---|---|---|
| GET | `/mcp` | no | 200 HTML dashboard | — |
| POST | `/v1/mcp/servers` | yes | 201 `MCPServer` | 401/503, 409, 422 |
| GET | `/v1/mcp/servers` | yes | 200 `{object:"list", data:[MCPServer]}` | 401/503 |
| GET | `/v1/mcp/servers/{server_id}` | yes | 200 `MCPServer` | 401/503, 404 |
| DELETE | `/v1/mcp/servers/{server_id}` | yes | 200 `{server_id, status:"retired"}` | 401/503, 404 |
| GET | `/v1/mcp/servers/{server_id}/tools` | yes | 200 `{object:"list", data:[ToolInfo]}` | 401/503, 404 |
| POST | `/v1/mcp/policies` | yes | 201 `ToolPolicy` | 401/503, 409, 422 |
| GET | `/v1/mcp/policies` | yes | 200 `{object:"list", data:[ToolPolicy]}` | 401/503 |
| DELETE | `/v1/mcp/policies/{policy_id}` | yes | 204 | 401/503, 404 |
| POST | `/v1/mcp/budgets` | yes | 201 `ToolBudget` | 401/503, 409, 422 |
| GET | `/v1/mcp/budgets` | yes | 200 `{object:"list", data:[ToolBudget]}` | 401/503 |
| DELETE | `/v1/mcp/budgets/{budget_id}` | yes | 204 | 401/503, 404 |
| GET | `/v1/mcp/audit` | yes | 200 `AuditPage` | 401/503, 422 |
| GET | `/v1/mcp/approvals` | yes | 200 `{object:"list", data:[ApprovalRequest]}` | 401/503 |
| POST | `/v1/mcp/approvals/{approval_id}/approve` | yes | 200 `ApprovalRequest` | 401/503, 404, 409 |
| POST | `/v1/mcp/approvals/{approval_id}/reject` | yes | 200 `ApprovalRequest` | 401/503, 404, 409 |
| GET | `/v1/mcp/report` | yes | 200 `MCPGovernanceReport.build(...)` (since_epoch = now - 86400) | 401/503 |

### 7.3 Request/response examples

**POST `/v1/mcp/servers`** — request:

```json
{
  "name": "github-mcp",
  "transport": "http",
  "endpoint": "https://mcp.example.com/mcp",
  "version": "1.0.0",
  "description": "GitHub tooling",
  "tools": [
    {
      "name": "create_issue",
      "description": "Create a GitHub issue",
      "input_schema": {"type": "object", "properties": {"title": {"type": "string"}}},
      "enabled": true
    },
    {
      "name": "get_repo",
      "description": "Read repository metadata",
      "input_schema": {"type": "object", "properties": {"owner": {"type": "string"}}},
      "enabled": true
    }
  ],
  "config": {"auth": "bearer"}
}
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

Registering `github-mcp` again with `version: "1.1.0"` succeeds (new row);
with `version: "1.0.0"` → `409 {"detail": "server github-mcp version 1.0.0 already registered"}`.

**GET `/v1/mcp/servers/srv123/tools`** → `200`:

```json
{
  "object": "list",
  "data": [
    {"name": "create_issue", "description": "Create a GitHub issue",
     "input_schema": {"type": "object", "properties": {"title": {"type": "string"}}}, "enabled": true},
    {"name": "get_repo", "description": "Read repository metadata",
     "input_schema": {"type": "object", "properties": {"owner": {"type": "string"}}}, "enabled": true}
  ]
}
```

**POST `/v1/mcp/policies`** — request:

```json
{
  "scope_kind": "user",
  "scope_key": "alice",
  "server_id": "srv123",
  "tool_name": "create_issue",
  "effect": "allow",
  "description": "Alice may create issues"
}
```

Response `201` adds `policy_id` and `created_at`. Duplicate 4-tuple → `409`.

**GET `/v1/mcp/audit`** — query parameters: `caller`, `server_id`, `tool_name`,
`decision`, `status`, `since`, `until`, `limit` (default 50, max 500),
`offset` (default 0). Response `200`:

```json
{
  "object": "list",
  "data": [
    {
      "event_id": "a1b2c3d4e5f6a7b8",
      "server_id": "srv123",
      "tool_name": "create_issue",
      "caller": "alice",
      "scope_kind": "user",
      "scope_key": "alice",
      "args": {"title": "Fix the bug", "labels": ["[REDACTED:email]"]},
      "decision": "allowed",
      "status": "completed",
      "reason": null,
      "cost": 0.0042,
      "latency_ms": 812,
      "timestamp": 1785708000,
      "redacted": true,
      "approval_id": null,
      "request_id": "req_9f8e7d"
    }
  ],
  "limit": 50,
  "offset": 0,
  "total": 1
}
```

**POST `/v1/mcp/approvals/{approval_id}/approve`** — request body:
`{"actor": "bob"}`. Response `200` `ApprovalRequest` with
`status: "approved"`, `decided_by: "bob"`, `decided_at` set. Non-pending →
`409`.

**GET `/v1/mcp/report`** → `200` (see `MCPGovernanceReport.build` fields).

**Dashboard `GET /mcp`:** static HTML mirroring the fleet/assurance page
accessibility markers (`data-theme=dark`, `focus-visible`, `aria-live`,
`skip link`, `skeleton`, `empty`, `error`, `toast`, `@media(max-width:560px)`,
"Skip to main content", "Theme changed"), title "MCP Governance".

### 7.4 OpenAPI

`create_mcp_governance_app("k").openapi()["paths"]` contains all paths above
(`/v1/mcp/servers`, `/v1/mcp/servers/{server_id}`,
`/v1/mcp/servers/{server_id}/tools`, `/v1/mcp/policies`,
`/v1/mcp/policies/{policy_id}`, `/v1/mcp/budgets`, `/v1/mcp/budgets/{budget_id}`,
`/v1/mcp/audit`, `/v1/mcp/approvals`,
`/v1/mcp/approvals/{approval_id}/approve`,
`/v1/mcp/approvals/{approval_id}/reject`, `/v1/mcp/report`, `/mcp`).

---

## 8. Exceptions (`exceptions.py`)

```python
class MCPGovernanceError(Exception):
    status_code = 400


class MCPServerNotFoundError(MCPGovernanceError):
    status_code = 404

class MCPToolNotFoundError(MCPGovernanceError):
    status_code = 404

class PolicyNotFoundError(MCPGovernanceError):
    status_code = 404

class BudgetNotFoundError(MCPGovernanceError):
    status_code = 404

class ApprovalNotFoundError(MCPGovernanceError):
    status_code = 404

class DuplicateServerError(MCPGovernanceError):
    status_code = 409

class DuplicatePolicyError(MCPGovernanceError):
    status_code = 409

class DuplicateBudgetError(MCPGovernanceError):
    status_code = 409

class AccessDeniedError(MCPGovernanceError):
    status_code = 403

class PolicyViolationError(MCPGovernanceError):
    status_code = 403

class ApprovalRequiredError(MCPGovernanceError):
    status_code = 409
    def __init__(self, approval_id: str, reason: str = "approval required") -> None:
        self.approval_id = approval_id
        super().__init__(reason)

class ApprovalStateError(MCPGovernanceError):
    status_code = 409

class MCPDiscoveryError(MCPGovernanceError):
    status_code = 502
```

### 8.1 Status mapping summary

| Exception / error | HTTP |
|---|---|
| `MCPServerNotFoundError`, `MCPToolNotFoundError`, `PolicyNotFoundError`, `BudgetNotFoundError`, `ApprovalNotFoundError` | 404 |
| `AccessDeniedError`, `PolicyViolationError` | 403 |
| `DuplicateServerError`, `DuplicatePolicyError`, `DuplicateBudgetError`, `ApprovalRequiredError`, `ApprovalStateError` | 409 |
| `BudgetExceededError` (existing, from `budget_enforcement`) | 412 |
| `RateLimitExceededError` (existing) | 429 |
| `MCPDiscoveryError` | 502 |
| pydantic `ValidationError`, `ValueError` (invalid filter values etc.) | 422 |

---

## 9. Changes to existing modules (additive, backward-compatible)

The implementer must make these **additive** changes. Nothing existing is
removed or re-typed.

### 9.1 `cost_tracking.py`

1. `UsageRecord` gains two trailing fields (both defaulted → existing
   constructions unaffected):
   ```python
   tool_name: str | None = None
   project: str | None = None
   ```
2. `_CREATE_TABLE` gains `tool_name TEXT` and `project TEXT` columns plus a
   new index:
   ```sql
   CREATE INDEX IF NOT EXISTS idx_cost_records_tool_timestamp
       ON cost_records (tool_name, timestamp);
   ```
3. `CostStore.__init__` runs an idempotent migration after `_CREATE_TABLE`:
   inspect `PRAGMA table_info(cost_records)`; `ALTER TABLE cost_records ADD
   COLUMN tool_name TEXT` / `ADD COLUMN project TEXT` when missing (existing
   databases keep working).
4. `CostStore.insert` persists `tool_name` and `project`.
5. `CostStore.spend_since(scope_key, since_epoch, tool_name=None)`:
   - when `tool_name` is provided, add `AND tool_name = ?`;
   - extend the kind→column map to `{"key": "api_key", "user": "user_id",
     "team": "team", "project": "project"}`.
6. `CostTracker.spend_since(scope_key, since_epoch, tool_name=None)` passes
   `tool_name` through.
7. `CostTracker.build_record` gains keyword params `tool_name: str | None =
   None` and `project: str | None = None`, forwarded to `UsageRecord`.

### 9.2 `budget_enforcement.py`

1. New module-level function (extracted from `BudgetEnforcer.window_seconds`,
   behavior identical):
   ```python
   def budget_window_seconds(window: str,
                             now_fn: Callable[[], int] | None = None) -> int:
       """Map a window string to seconds ("monthly" = current calendar month).
       Unknown window -> ValueError."""
   ```
2. `BudgetEnforcer.window_seconds(window)` delegates to it.

`BudgetScope` and `BudgetExceededError` are reused as-is (no changes). The
existing model-call budget engine (kinds `global|team|user|key`) is untouched;
`project` scopes exist only for the mcp tool-budget path.

---

## 10. Dependencies

### 10.1 New runtime dependency — MUST be added to `pyproject.toml`

```toml
[project]
dependencies = [
    "fastapi>=0.100",
    "uvicorn>=0.24",
    "pydantic>=2.5",
    "pydantic-settings>=2.0",
    "litellm>=1.40,<2",
    "pyyaml>=6.0",
    "mcp>=1.2,<2",          # NEW — MCP SDK for live tool inventory (discovery.py)
]
```

- `mcp` is used **only** by `MCPDiscoveryAdapter.discover_tools` via a lazy
  import; no other module imports it, so interface tests and module imports
  never require it.
- No other new runtime deps: SSRF guard uses stdlib `ipaddress`/`socket`;
  PII redaction uses stdlib `re`; storage uses stdlib `sqlite3`.

### 10.2 Dev dependency (recommended, pre-existing gap)

`httpx` is already installed in `.venv` and used by existing API tests
(`tests/test_fleet_api.py`) but is **not** declared. Add to
`[project.optional-dependencies] dev`:

```toml
dev = [
    "pytest>=7.4",
    "pytest-asyncio>=0.23",
    "pytest-mock>=3.12",
    "ruff>=0.4",
    "pytest-cov>=5.0",
    "httpx>=0.27",          # NEW — ASGITransport for real-HTTP endpoint tests
]
```

---

## 11. Testing guidance (for the pre-tester)

Follow existing patterns (`tests/test_fleet_api.py`, `tests/conftest.py`).

### 11.1 Layout

- `tests/test_mcp_governance.py` — or split into:
  `test_mcp_governance_registry.py`, `test_mcp_governance_policy.py`,
  `test_mcp_governance_budgets.py`, `test_mcp_governance_audit.py`,
  `test_mcp_governance_rules.py`, `test_mcp_governance_engine.py`,
  `test_mcp_governance_api.py`.
- Stub package: `src/llm_budget_gateway/mcp_governance/` with functional
  constructors, `schemas.py`, `exceptions.py`, `db.py`, and
  `NotImplementedError` on every behavioral method (§3 stub contract).

### 11.2 Interface tests (must pass immediately)

- Import every public name from `llm_budget_gateway.mcp_governance`.
- `inspect.signature` checks for the key constructors and methods (names,
  keyword-only markers, defaults).
- Pydantic models construct/validate (valid cases construct; invalid cases
  raise `ValidationError`).
- Store `__init__(conn, clock=None)` constructs against
  `sqlite3.connect(":memory:")` and creates tables (query
  `sqlite_master`).

### 11.3 Behavioral tests (RED — NotImplementedError until implemented)

Use `tmp_path`-backed or in-memory sqlite; a shared `conn` fixture in
`tests/conftest.py` (e.g. `open_mcp_db(":memory:")`). Fake the ledger for
budget tests (see below). Async tests MUST use `@pytest.mark.asyncio`.

- **Registry (~10):** register + inventory; duplicate (name, version) → 409;
  versioning (two versions → `list_servers` returns latest; `list_versions`
  newest first); `get_server_by_name` latest; retire + list exclusion;
  unknown server/tool → not-found; tool list ordering.
- **Policy (~9):** CRUD; duplicate 4-tuple → 409; wildcard validation
  (`tool_name` without `server_id` → 422); evaluator precedence
  (user > team > project > global; deny > approval > allow; exact tool >
  wildcard); default deny with no policies.
- **Budgets (~8):** CRUD + duplicates; `applicable_budgets` matching;
  `check` raises `BudgetExceededError` when a fake tracker reports spend ≥
  hard_limit; passes under the limit; `soft_exceeded`; `record_usage` writes
  a `UsageRecord` carrying `tool_name=f"{server_id}:{tool_name}"` and
  `project`; window parsing incl. `ValueError` on bad window.
  Fake tracker: a stub class with `async def spend_since(self, scope_key,
  since, tool_name=None) -> float` returning a controllable value, and
  `record` recording calls.
- **Audit (~6):** append (auto id + replace); every query filter; ordering
  (timestamp DESC); pagination + `total`; `limit` clamp; invalid
  decision/status → ValueError.
- **Rules (~14):** SSRF — private/loopback/link-local/reserved/multicast IP
  literals blocked with the exact reason strings; public IP allowed;
  `file://` blocked; allowlist allow; hostname resolution (monkeypatch
  `socket.getaddrinfo`) blocked on private result; NXDOMAIN blocked; nested
  url fields found. PII — each default pattern redacts to
  `[REDACTED:<name>]`; nested dicts/lists; keys untouched; no-match
  passthrough; `scan` returns sorted unique names. Approvals — create
  (hash determinism, redacted args, expiry); approve/reject/consume state
  machine incl. `ApprovalStateError`; `find_approved` match/no-match/expired;
  `expire_stale`.
- **Engine (~8):** allow path returns CallContext; deny → audit row +
  `AccessDeniedError`; approval path creates request + `ApprovalRequiredError`
  (with `approval_id`), then approve → `find_approved` → consume → second
  call succeeds with decision `approved`; SSRF block; budget block; retired
  server; disabled tool; unknown server/tool; `after_call` writes event +
  ledger.
- **API (~13):** 401 (no/wrong key), 503 (no key configured), 422 (bad body),
  register → 201 + GET list/get/tools, duplicate → 409, policies CRUD +
  filters, budgets CRUD, audit query with filters, approvals approve/reject
  + 404/409, report shape, `/mcp` dashboard markers, OpenAPI paths.
  HTTP via `httpx.ASGITransport` (see `tests/test_fleet_api.py`).

### 11.4 Test hygiene

- Run via the repo venv: `.venv/bin/python -m pytest` and
  `.venv/bin/python -m ruff check src tests` (never bare `pytest`/`python3`).
- Run the existing suite before and after adding tests (no regressions).
- If `mcp` is not yet installed when discovery tests run, use
  `pytest.importorskip("mcp")` for the discovery behavioral test.
- Add `mcp>=1.2,<2` to `pyproject.toml` `dependencies` and `httpx>=0.27` to
  the dev group, then `.venv/bin/pip install -e ".[dev]"` and re-run in
  `.venv` before reporting any failure.

---

## 12. Implementation order (for the tech-lead)

1. Add deps to `pyproject.toml` (§10), install in `.venv`.
2. Additive changes to `cost_tracking.py` + `budget_enforcement.py` (§9).
3. `mcp_governance` package: schemas → exceptions → db → stores
   (registry, policy, budgets, audit, approvals) → rules → engine →
   integration → discovery → api.
4. Wire `create_mcp_governance_app` into the gateway control plane
   (`control_plane.py` / `main.py` mount) and expose
   `GATEWAY_MCP_API_KEY` in `.env.example`.
5. Assurance Center capability `mcp-governance` (§6.8).
6. Docs: README section, CHANGELOG entry, API reference
   (`docs/architecture/mcp-governance.md` is the design source).
7. Run `tdd-gate-v3.sh`, `security-gate.sh`, `doc-sync-check.sh`.
