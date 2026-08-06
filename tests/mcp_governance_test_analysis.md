# MCP Governance Test Analysis

## Test Files Analyzed

1. `test_mcp_governance.py` — Package-level interface tests
2. `test_mcp_governance_engine.py` — MCPPolicyEngine + CallContext tests
3. `test_mcp_governance_budgets.py` — ToolBudgetStore + ToolBudgetService tests
4. `test_mcp_governance_registry.py` — MCPRegistry tests
5. `test_mcp_governance_policy.py` — ToolPolicyStore + PolicyEvaluator tests
6. `test_mcp_governance_rules.py` — SSRFGuard, PIIRedactor, ApprovalStore, ApprovalGate tests
7. `test_mcp_governance_api.py` — REST API endpoint tests
8. `test_mcp_governance_schemas.py` — Pydantic schema validation tests
9. `test_mcp_governance_discovery.py` — MCPDiscoveryAdapter tests
10. `test_mcp_governance_audit.py` — AuditStore tests

## Missing Test Files

No dedicated tests exist for:
- `db.py` (`open_mcp_db`) — tested indirectly via fixtures
- `integration.py` (`MCPGovernanceReport.assess()`, `NullApprovalNotifier`, `_covered()`)
- `exceptions.py` — tested via package-level tests

---

## Per-File Analysis

### 1. test_mcp_governance.py — Package-Level Interface

**APIs covered:** Package imports, `__all__` consistency, all 13 exception classes, `ApprovalRequiredError.approval_id`, `MCPGovernanceError.status_code`, `create_mcp_governance_app`/`open_mcp_db` callable checks.

**Edge cases:** Exception status codes (404, 409, 403, 502, 400), `ApprovalRequiredError` with/without custom reason.

**Gaps:** None — appropriate for its scope.

**Quality issues:** None.

---

### 2. test_mcp_governance_engine.py — Engine

**APIs covered:** `CallContext` dataclass fields/contruction, `MCPPolicyEngine.__init__` signature, `before_call`/`after_call` async signatures, 7 blocking paths in `before_call`, `after_call` happy path.

**Edge cases:** Unknown server → `MCPServerNotFoundError`, unknown tool → `MCPToolNotFoundError`, allow path → `CallContext`, denied policy → `AccessDeniedError`, approval required → `ApprovalRequiredError`, SSRF block → `PolicyViolationError`, budget block → `BudgetExceededError`.

**Gaps:**
- **Retired server block** — NOT tested
- **Disabled tool block** — NOT tested
- **Approval already-approved path** (consume existing approval) — NOT tested
- **`request_id_factory`** parameter — NOT tested
- **`_notifier.notify`** called when approval required — NOT tested
- **`after_call` with status != "completed"** (should NOT call `record_usage`) — NOT tested
- **`after_call` verifying `record_usage` is called** — NOT tested
- **`_blocked_scope` with empty scopes** — NOT tested

**Quality issues:**
- `after_call` test only asserts `event.status` and `event.cost` but does NOT verify audit store was written to or `record_usage` was called.

---

### 3. test_mcp_governance_budgets.py — Budget Store & Service

**APIs covered:** `ToolBudgetStore` constructor/table, `create_budget`, `list_budgets`, `get_budget`, `delete_budget`, `ToolBudgetService` constructor, `applicable_budgets`, `check`, `soft_exceeded`, `record_usage`, `canonical_tool`, `budget_window_seconds`.

**Edge cases:** Duplicate 4-tuple → `DuplicateBudgetError`, unknown ID → `BudgetNotFoundError`, budget exceeded → `BudgetExceededError`, no applicable budgets → passes, soft limit exceeded → returns scope, bad window → `ValueError`.

**Gaps:**
- **`list_budgets` filters** by `scope_key`, `server_id`, `tool_name` — only `scope_kind` tested
- **`budget_window_seconds`** for `30s`, `30h`, `monthly` — NOT tested
- **`record_usage`** with non-"user" scope_kind — NOT tested
- **Multi-budget priority ranking** — NOT tested
- **`check` with soft_limit only budget** — NOT tested

**Quality issues:** None significant.

---

### 4. test_mcp_governance_registry.py — Registry

**APIs covered:** `MCPRegistry` constructor (clock, tables), `register`, `get_server`, `get_server_by_name`, `list_servers`, `list_versions`, `retire_server`, `list_tools`, `get_tool`, `has_tool`.

**Edge cases:** Duplicate name+version → `DuplicateServerError`, same name new version → OK, unknown server → `MCPServerNotFoundError`, retired server excluded/included, version ordering, unknown tool → `MCPToolNotFoundError`.

**Gaps:**
- **`get_server_by_name` with explicit `version`** — NOT tested
- **`get_server_by_name` with unknown name/version** — NOT tested
- **`retire_server` idempotent** on already-retired — NOT tested
- **`register` with empty tools list** — NOT tested

**Quality issues:** None.

---

### 5. test_mcp_governance_policy.py — Policy Store & Evaluator

**APIs covered:** `ToolPolicyStore` constructor (default_effect variants), table creation, CRUD methods, `PolicyEvaluator.decide` with multiple resolution scenarios.

**Edge cases:** Invalid default_effect → `ValueError`, duplicate 4-tuple → `DuplicatePolicyError`, unknown policy → `PolicyNotFoundError`, scope precedence (user > team), effect precedence (deny > approval > allow), exact tool > wildcard, global policy matches any caller.

**Gaps:**
- **`list_policies` filters** by `scope_key`, `server_id`, `tool_name` — only `scope_kind` tested
- **`PolicyEvaluator.decide` with `default_effect="allow"`** — NOT tested
- **`PolicyEvaluator.decide` with `project` scope** — NOT tested
- **Tie-breaking** at same score — NOT explicitly tested

**Quality issues:** None.

---

### 6. test_mcp_governance_rules.py — SSRF, PII, Approvals

**APIs covered:** `SSRFGuard` (constructor, `check`, `extract_urls`), `PIIRedactor` (constructor, `redact`, `redact_text`, `scan`), `ApprovalStore` (constructor, table), `ApprovalGate` (constructor with TTL, `requires_approval`, `create_request`, `approve`, `reject`, `consume`, `find_approved`, `expire_stale`).

**Edge cases:** SSRF: 5 IP categories blocked, public IP allowed, unsupported scheme, allowlist, hostname resolution, unknown host, no URLs, nested URLs. PII: email, phone, SSN, API key, nested dict/list, scan. Approvals: double-approve → `ApprovalStateError`, hash deterministic, args redacted.

**Gaps:**
- **`ApprovalStore.get` with unknown ID** — NOT tested
- **`ApprovalStore.list` with both filters** — NOT tested
- **`ApprovalGate.reject`/`consume` with wrong state** → `ApprovalStateError` — NOT tested
- **`expire_stale` actually expiring** — NOT tested
- **SSRF `https` scheme** — NOT tested
- **PII patterns** credit_card, bearer_token, aws_access_key — NOT tested
- **`args_hash_of`** function — NOT directly tested

**Quality issues:**
- `test_find_approved_none_when_expired`: assertion `assert found is not None or found is None` is a **tautology** that always passes.
- `test_expire_stale_returns_count`: assertion `>= 0` is **trivially true**.

---

### 7. test_mcp_governance_api.py — REST API

**APIs covered:** Factory signature, auth (401, 503), server CRUD (POST/GET/DELETE), tools list, policy CRUD, budget CRUD, audit query, approval approve, governance report shape, dashboard HTML, OpenAPI paths.

**Edge cases:** Auth failures, fail-closed mode, invalid body (422), duplicate server (409), unknown server (404), unknown approval (404).

**Gaps:**
- **`/v1/mcp/approvals` list** — NOT tested
- **`/v1/mcp/approvals/{id}/reject`** — NOT tested
- **`DELETE /v1/mcp/budgets/{budget_id}`** — NOT tested
- **Budget/policy invalid body (422)** — NOT tested
- **Budget exceeded through API (412)** — NOT tested
- **Audit invalid decision/status** — NOT tested

**Quality issues:**
- `test_approve_approval` depends on demo `aprv1` seeded by factory — fragile coupling.

---

### 8. test_mcp_governance_schemas.py — Schema Validation

**APIs covered:** All Pydantic models — `MCPServer`, `ToolInfo`, `MCPRegistryRequest`, `ToolPolicy`, `ToolPolicyRequest`, `ToolBudget`, `ToolBudgetRequest`, `AuditEvent`, `AuditPage`, `PolicyDecision`, `RuleVerdict`, `ApprovalRequest`.

**Edge cases:** Regex validation, Literal field rejection, cross-field validators, extra field forbidden, defaults.

**Gaps:**
- **`_window_seconds` for `monthly`** — NOT tested in this file
- **Custom `config` dict** on `MCPServer`/`MCPRegistryRequest` — NOT tested

**Quality issues:** None.

---

### 9. test_mcp_governance_discovery.py — Discovery Adapter

**APIs covered:** Instantiation, `discover_tools` async signature.

**Gaps:** All actual discovery behavior — `NotImplementedError` (RED phase).

**Quality issues:** None — proper RED-phase test.

---

### 10. test_mcp_governance_audit.py — Audit Store

**APIs covered:** Constructor (table + indexes), `append` (auto ID, replace-on-same-id), `query` (caller filter, decision filter, timestamp range, ordering, pagination, limit clamping, invalid decision/status).

**Gaps:**
- **`query` filter by `server_id`** — NOT tested
- **`query` filter by `tool_name`** — NOT tested
- **`query` filter by `status`** — NOT tested (only invalid status)
- **`query` offset beyond total** — NOT tested
- **`query` limit=0** (clamped to 1) — NOT tested

**Quality issues:** None.

---

## Critical Gaps Summary

| Module | Gap | Severity |
|---|---|---|
| `engine.py` | Retired server block, disabled tool block, approval-consume path, notifier call, `request_id_factory` | **High** |
| `engine.py` | `after_call` audit write + `record_usage` verification | **High** |
| `integration.py` | `assess()` method completely untested | **High** |
| `integration.py` | `risk_tier` threshold logic untested | **High** |
| `rules.py` | `reject`/`consume` with wrong state → `ApprovalStateError` | **Medium** |
| `rules.py` | `expire_stale` actually expiring entries | **Medium** |
| `api.py` | Reject approval endpoint, delete budget endpoint, budget exceeded 422 | **Medium** |
| `budgets.py` | Multi-budget priority ranking, non-user scope in `record_usage` | **Medium** |
| `audit.py` | Filter by `server_id`/`tool_name`/`status` | **Low** |
| `db.py` | WAL pragma, `check_same_thread` behavior | **Low** |

## Test Quality Issues

1. **Tautological assertion** in `test_mcp_governance_rules.py::test_find_approved_none_when_expired` — `assert found is not None or found is None` always passes.
2. **Trivially true assertion** in `test_mcp_governance_rules.py::test_expire_stale_returns_count` — `assert gate.expire_stale(now=1000) >= 0` is always true.
3. **Fragile coupling** in `test_mcp_governance_api.py::test_approve_approval` — depends on demo `aprv1` seeded by factory.
4. **Incomplete assertion** in `test_mcp_governance_engine.py::test_after_call_writes_event_and_ledger` — does NOT verify audit store write or `record_usage` call.
