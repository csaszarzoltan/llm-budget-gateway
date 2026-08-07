# US-001 Cost Attribution — Implementation Spec

- **Task:** t_f9210490 (analyst) · **Epic:** Cost Attribution · **Priority:** P0 · **Points:** 8
- **Repo:** `/home/zoltan/llm-budget-gateway` (HEAD `496ed24`, package v14.1.0)
- **Date:** 2026-08-07
- **Research-brief substitution:** no prior US-001 research brief existed on the board; replaced with direct repo inspection + targeted competitor research (Helicone, Langfuse, LiteLLM spend-log pattern, CloudZero per-customer attribution). Noted in task comment 1296.
- **Baseline verified:** full suite `1016 passed` (repo `.venv`, 54.97s), ruff clean, ui-gate PASS (React/Vite cockpit detected).

---

## 1. Current State Assessment

### 1.1 What already exists (relevant to US-001)

| Area | File(s) | State |
|---|---|---|
| Per-request cost ledger (SQLite WAL) | `src/llm_budget_gateway/cost_tracking.py` (`CostStore`, `CostTracker`, `CostCalculator`, `PriceMap`) | **DONE, battle-tested.** `cost_records` table with `api_key, user_id, team, model, provider, prompt/completion/total/reasoning tokens, input/output/reasoning/total cost, latency_ms, status, timestamp, tool_name, project, route, client_id, client_profile, cache_hit, conversation_id`. Indexes on `(timestamp)` and `(api_key, timestamp)`. Thread-safe via lock; async facade via `asyncio.to_thread`. |
| Ledger write path (request completion) | `src/llm_budget_gateway/gateway_proxy.py` (~line 886) | On every completed chat/completion request: `build_record(...)` → tags `client_id`/`client_profile`/`cache_hit` → `cost_tracker.record(...)` — **synchronous at completion, well under 60s**. |
| Client identity resolution | `gateway_proxy.py` `resolve_client_identity` (~line 440) | Priority: body `metadata.client_id` > `X-Gateway-Client-Id` header > `pc_apps` app name (key lookup) > `gw-<key suffix>` > `anonymous`. |
| Scopes for budgets | `budget_enforcement.py` `BudgetScope(kind, key)`, `resolve_scopes()` in `gateway_proxy.py` | `key` (virtual key id), `user`/`team` (from `GATEWAY_USER_HEADER_MAPPINGS` headers), `global`. |
| Per-day/per-period aggregate queries | `cost_tracking.py` `daily_usage()`, `usage_by_period()` | Group by `date(timestamp,'unixepoch')` / `strftime` buckets, by model (+route); returns `days[] {date, models[]}` + paginated raw `calls[]`. **Global only — no customer/tenant filter parameter.** |
| Raw calls pagination | `daily_usage()` / `usage_by_period()` | `page`/`page_size`, `total_calls` count, `usage_missing` flag (success w/o usage chunk). |
| Global usage API | `console_api.py` `GET /v1/product/usage?days&period&route&page&page_size` | Wires `cost_store.usage_by_period(...)`; buckets `hour|day|month`. **No customer dimension.** |
| Ledger-based spend totals per scope | `cost_tracking.py` `spend_since(scope_key, since_epoch)` | Sums `total_cost` for `global|key|user|team|project` scopes since a window start. |
| Budget config + enforcement | `budget_enforcement.py` `BudgetEnforcer`, `load_budget_configs`; `examples/budgets.example.yaml`; docs/budget-configuration.md | Windows incl. `monthly` (current calendar month); soft (alert) / hard (412) limits; sync TPM/RPM ceilings. |
| UI-managed budgets (operator-set) | `product_extensions.py` `px_budgets(scope, limit_usd, spent_usd, reset_day)`; `PUT /v1/product/budgets/{scope}`; `budget()` returns `percent_used` | **Dead letter:** `spent_usd` is never written from the ledger — `add_spend()` exists but no caller updates it. Budget progress would show 0% forever. |
| Operator-facing UI | `ui/src/main.tsx` — React 19 + Vite + TS, SPA "cockpit" served by console at `/cockpit`; left nav `Home, Applications, Routes, Providers, Activity, Usage, Safety, Intelligence, Prompts, Quality, Advanced` | Modern (ui-gate PASS). Usage tab is global-only. **No Customers concept anywhere.** |
| Server-rendered fallback console | `console_ui.py` `render_console()` (dependency-free SPA) + `create_console_app` at `http://127.0.0.1:8013` | Catalog of 14 centers; FinOps group incl. Control Center & Optimization. Runner UI. |
| Control plane (tenant RBAC) | `control_plane.py` `ControlPlane` (workspaces, keys, budgets, reservations, policies, routes, decisions, audit) | Tenant-scoped; `export_spend_csv()` exists but exports **reservations** (request_id/model/actual/latency/state) — NOT the required customer, timestamp, model, tokens, cost rows. `spend.csv` endpoint is `/v1/admin/spend.csv` w/ tenant+role headers. |
| Forecaster | `optimization_suite.py` `BudgetForecast.forecast(daily_costs, elapsed_days, period_days, budget)` | Reusable for the budget progress card's run-rate projection (P1). |

### 1.2 What is missing (gap list for US-001)

1. **No customer/tenant dimension** on the ledger. `cost_records` has `api_key, user_id, team, project, client_id` — no single canonical `customer_id` FK, and no `customers` table (only `pc_apps` = app/application, which is close but semantically "client application", and control-plane `workspaces`/tenant = the operator's own tenant). US-001's "customer" = **the operator's paying customer/tenant that consumes LLM calls through the gateway** → needs a `customers` table + `customer_id` on the usage event.
2. **No per-customer aggregation queries.** `daily_usage()`/`usage_by_period()` are global-only.
3. **No per-customer MTD summary API** (cost, call count, token volume).
4. **No budget progress** fed from the ledger (`px_budgets.spent_usd` never updated; no per-customer monthly budget).
5. **No CSV export** of `customer, timestamp, model, tokens, cost` rows (existing exports are reservations-based, wrong shape).
6. **No Customers UI** (list page, detail page, daily chart, model breakdown, budget bar, export button).
7. **No attribution latency guarantee** test — the write is synchronous at completion today; the 60s SLO must be pinned by tests.

### 1.3 Locked decisions (do not re-litigate)

- SQLite ledger stays the source of truth; no Redis/message bus required for the 60s SLO (synchronous write at completion is already faster).
- Keep the existing `cost_records` table; **extend additively** (`customer_id` column) with the existing `_migrate_legacy_schema` pattern — do not create a parallel usage-events store.
- The 60s attribution requirement is satisfied by the existing synchronous `tracker.record()` on request completion (verify by test, no async pipeline needed).
- API/UI land on the **console product API** (`create_console_app`, port 8013, `/v1/product/...`) and the **React cockpit** (`ui/src/main.tsx`) — not on the data-plane proxy (port 8000) and not on the server-rendered `console_ui.py` catalog (which stays as the fallback console; Customers is a cockpit nav addition).
- Auth for the new endpoints: same local console boundary as `/v1/product/*` (no extra auth header; consistent with `applications`, `usage`, `budgets` endpoints).

---

## 2. Clustered Options

### Option A — Minimal: extend ledger + cockpit only (chosen core)
- Add `customers` table + `customer_id` column (backfill from `client_id`), per-customer aggregate queries on `CostStore`, `/v1/product/customers/*` endpoints in `console_api.py`, Customers list + detail views in the React cockpit.
- Pros: small surface, reuses proven `CostStore`/SQLite patterns, no new infra. Cons: `client_id` is self-reported (a customer can spoof attribution); operator must keep header/metadata conventions.

### Option B — Control-plane tenant pivot
- Model "customer" as control-plane `workspaces` (tenant) and key every customer query through `X-Tenant-Id`/`X-Role` RBAC like `/v1/admin/*`.
- Pros: strongest multi-tenant isolation story for the paid multi-customer product. Cons: `cost_records` has no tenant FK; the data-plane and control-plane DBs are separate files (gateway.db vs control-plane.db); retrofitting is a much larger P1-scale change. Rejected for P0.

### Option C — Separate usage-events store (event-sourcing style)
- New `usage_events` table with append-only rows + materialized aggregations.
- Pros: clean event log, easy time-travel. Cons: duplicates `cost_records`, doubles write path and migration surface, no benefit at P0 volume. Rejected.

### Option D — Third-party analytics embed (Helicone/Langfuse)
- Send usage to an external service and render its widgets.
- Pros: fastest to market. Cons: violates the self-hosted, no-external-dependency product thesis; recurring SaaS cost; data leaves the gateway. Rejected.

**Decision:** **Option A** for P0. Keep Option B (tenant-aware RBAC on customer endpoints) as a documented P2 extension: the endpoint contract below is already written tenant-shaped (customer id is a path/query parameter) so it can be gated behind RBAC later without breaking the UI.

---

## 3. Chosen Tech Stack (with rationale)

| Layer | Choice | Rationale |
|---|---|---|
| Storage | SQLite (existing `gateway.db`, WAL) — additive `customers` table + `customer_id` column on `cost_records` | Existing proven pattern; `_migrate_legacy_schema()` already does additive ALTERs; 1016-test baseline green. No new runtime deps. |
| Backend | Python 3.11 + FastAPI (existing `create_console_app`), new module `src/llm_budget_gateway/cost_attribution.py` + endpoints in `console_api.py` | Matches every existing capability (optimization, platform, etc.). `cost_attribution.py` mirrors the `*_suite.py` + `*_api.py` split used by optimization/agentops/assurance. |
| Aggregation | SQL `GROUP BY` on `cost_records` (strftime day buckets, month start for MTD) | Same shape as `usage_by_period`; indexes `(customer_id, timestamp)` keep it fast at P0 volumes. |
| Money math | Existing `PriceMap`/`CostCalculator` — never recompute; store `total_cost` at write time | Avoids price-map drift between request time and report time. |
| Budget progress | New `customer_budgets` table (monthly limit per customer) + percent from ledger MTD spend | Fixes the dead `px_budgets.spent_usd` by computing spend from the ledger instead of a never-updated counter. |
| CSV export | `csv` stdlib, `StreamingResponse`/`PlainTextResponse` with `text/csv` | Zero deps; matches `control_plane.export_spend_csv` precedent. |
| UI | React 19 + Vite + TS cockpit (`ui/src/main.tsx`), new `Customers` nav + `CustomerDetail` view | ui-gate PASS already; charts hand-rolled SVG (existing Usage tab pattern) — no new chart lib. |
| Tests | pytest + httpx ASGITransport (repo conventions) | Interface tests pass on stubs; behavioral tests RED until implemented (pre-tester task t_62736001). |
| Deploy | None (local cockpit service) | Feature is deployment-agnostic like prior cycles. |

Cost per user: $0 incremental (all self-hosted, no new services).

---

## 4. Concrete Backend Data Model

### 4.1 New tables

```sql
-- Customer registry (operator-managed billing entity).
CREATE TABLE IF NOT EXISTS customers (
    id            TEXT PRIMARY KEY,          -- 'cus_' + token_hex(8)
    name          TEXT NOT NULL,             -- display name, unique
    tenant        TEXT NOT NULL DEFAULT 'local',  -- control-plane tenant (P2 RBAC hook)
    created_at    INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_name ON customers (name);

-- Monthly budget per customer (monthly window = current calendar month).
CREATE TABLE IF NOT EXISTS customer_budgets (
    customer_id   TEXT PRIMARY KEY REFERENCES customers(id) ON DELETE CASCADE,
    monthly_limit_usd REAL NOT NULL CHECK (monthly_limit_usd > 0),
    updated_at    INTEGER NOT NULL
);
```

### 4.2 Additive migration on `cost_records`

```sql
ALTER TABLE cost_records ADD COLUMN customer_id TEXT;   -- FK to customers.id (logical; SQLite ALTER can't add FK)
CREATE INDEX IF NOT EXISTS idx_cost_records_customer_timestamp
    ON cost_records (customer_id, timestamp);
```

- Added in `CostStore.__init__` via the existing `_migrate_legacy_schema()` pattern (idempotent `ALTER TABLE ... ADD COLUMN` guarded by `PRAGMA table_info`).
- **Write-path hook:** in `gateway_proxy.py`, after `resolve_client_identity` produces `client_id`, also resolve `customer_id` and set it on the `UsageRecord` (`record.customer_id = ...`). Resolution priority: `metadata.customer_id` > `X-Gateway-Customer-Id` header > exact `customers.name` match on `client_id` > `None` (unattributed requests stay queryable via `WHERE customer_id IS NULL` or `'*'`).
- Backfill (`customer_id = client_id` where a customer name matches) runs in the migration for operator convenience — documented as best-effort, not required for acceptance.

### 4.3 New dataclasses (module `cost_attribution.py`)

```python
@dataclass
class CustomerSpendSummary:
    customer_id: str
    customer_name: str
    mtd_cost_usd: float
    mtd_calls: int
    mtd_total_tokens: int
    mtd_prompt_tokens: int
    mtd_completion_tokens: int

@dataclass
class DailySpendPoint:
    date: str            # YYYY-MM-DD
    cost_usd: float
    calls: int
    total_tokens: int

@dataclass
class ModelSpend:
    model: str
    cost_usd: float
    calls: int
    total_tokens: int

@dataclass
class CustomerBudgetStatus:
    customer_id: str
    monthly_limit_usd: float
    mtd_spend_usd: float
    percent_used: float     # clamp 0..100
    remaining_usd: float
    reset_day: int          # 1 (calendar month)

@dataclass
class UsageLedgerRow:       # CSV row shape (acceptance criterion 3)
    customer: str
    timestamp: str          # ISO-8601 UTC (or epoch seconds per API impl; CSV = ISO)
    model: str
    tokens: int             # total_tokens
    cost: float             # total_cost
```

### 4.4 Store API (on `CostStore`, or a thin `CostAttributionStore` over the same connection)

```python
class CostAttributionStore:
    def __init__(self, connection: sqlite3.Connection) -> None: ...
    # Customers CRUD
    def create_customer(self, name: str, tenant: str = "local") -> dict: ...      # raises ValueError on duplicate
    def list_customers(self) -> list[dict]: ...                                    # + MTD summary per customer (1 query, GROUP BY)
    def get_customer(self, customer_id: str) -> dict | None: ...
    def set_monthly_budget(self, customer_id: str, limit_usd: float) -> dict: ...  # upsert customer_budgets
    def get_budget(self, customer_id: str) -> dict | None: ...
    # Aggregations (all filtered by customer_id, all timezone = UTC)
    def mtd_summary(self, customer_id: str, now_epoch: int | None = None) -> CustomerSpendSummary: ...
    def daily_spend(self, customer_id: str, days: int = 31, granularity: str = "day") -> list[DailySpendPoint]: ...
    def spend_by_model(self, customer_id: str, since_epoch: int | None = None) -> list[ModelSpend]: ...
    def ledger_rows(self, customer_id: str, limit: int = 10000) -> list[UsageLedgerRow]: ...  # newest first, for CSV
```

### 4.5 Aggregation queries (canonical)

```sql
-- MTD summary (current calendar month, UTC)
SELECT COUNT(*),
       COALESCE(SUM(total_cost), 0),
       COALESCE(SUM(total_tokens), 0),
       COALESCE(SUM(prompt_tokens), 0),
       COALESCE(SUM(completion_tokens), 0)
FROM cost_records
WHERE customer_id = ?
  AND timestamp >= strftime('%s', date('now', 'start of month', 'utc'));

-- Daily spend (N days)
SELECT date(timestamp, 'unixepoch') AS day,
       COALESCE(SUM(total_cost), 0),
       COUNT(*),
       COALESCE(SUM(total_tokens), 0)
FROM cost_records
WHERE customer_id = ? AND timestamp >= ?
GROUP BY day ORDER BY day ASC;

-- Breakdown by model (MTD by default, else since_epoch)
SELECT model, COALESCE(SUM(total_cost), 0), COUNT(*), COALESCE(SUM(total_tokens), 0)
FROM cost_records
WHERE customer_id = ? AND timestamp >= ?
GROUP BY model ORDER BY SUM(total_cost) DESC;

-- Budget progress: mtd_spend (above) vs customer_budgets.monthly_limit_usd
SELECT cb.monthly_limit_usd, cb.updated_at
FROM customer_budgets cb WHERE cb.customer_id = ?;
-- percent_used = min(100, mtd_spend / monthly_limit_usd * 100); remaining = max(0, limit - mtd)

-- CSV ledger rows (newest first)
SELECT c.name AS customer, timestamp, model, total_tokens, total_cost
FROM cost_records cr JOIN customers c ON c.id = cr.customer_id
WHERE cr.customer_id = ?
ORDER BY cr.timestamp DESC LIMIT ?;
```

### 4.6 "Within 60 seconds" guarantee

Write path is already synchronous at completion (`gateway_proxy` → `tracker.record()` → `CostStore.insert`, awaited). The 60s SLO is met structurally; tests must pin it: a behavioral test asserts that after a simulated completed request (insert via `CostTracker.record`), `mtd_summary` reflects the new spend **immediately** (0s), i.e. `<= 60s` by a wide margin. No background worker, no queue, no event bus. (If multi-instance scale-out ever lands, swap `CostStore` writes for a Redis-backed event bus — documented P2, out of scope.)

---

## 5. API Contract

All endpoints on the **console app** (`create_console_app`, port 8013), same local boundary as existing `/v1/product/*`. No new auth headers (consistent with `/v1/product/usage`). Timestamps in responses are **epoch seconds** (repo convention); CSV uses ISO-8601 UTC.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/product/customers` | List customers + MTD summary each |
| `POST` | `/v1/product/customers` | Create customer `{name}` (201; 409 duplicate) |
| `GET` | `/v1/product/customers/{customer_id}` | **Customer detail** (summary + budget status) |
| `PUT` | `/v1/product/customers/{customer_id}/budget` | Set monthly budget `{monthly_limit_usd}` |
| `GET` | `/v1/product/customers/{customer_id}/daily-spend` | Daily chart data (`days`, `granularity=day\|week\|month`) |
| `GET` | `/v1/product/customers/{customer_id}/models` | Breakdown by model |
| `GET` | `/v1/product/customers/{customer_id}/export.csv` | CSV ledger (`customer,timestamp,model,tokens,cost`) |

### 5.1 `GET /v1/product/customers` → 200

```json
{
  "customers": [
    {
      "id": "cus_1a2b3c4d",
      "name": "Acme Corp",
      "created_at": 1786000000,
      "mtd": {"cost_usd": 12.34, "calls": 152, "total_tokens": 4182051, "prompt_tokens": 3000000, "completion_tokens": 1182051},
      "budget": {"monthly_limit_usd": 100.0, "percent_used": 12.34, "remaining_usd": 87.66}
    }
  ],
  "total_customers": 1
}
```
`budget` is `null` when no budget is set.

### 5.2 `POST /v1/product/customers` — body `{"name": "Acme Corp"}` → 201

```json
{"id": "cus_1a2b3c4d", "name": "Acme Corp", "created_at": 1786000000}
```
Errors: 422 (empty name), 409 (duplicate name).

### 5.3 `GET /v1/product/customers/{customer_id}` → 200 (customer detail page payload)

```json
{
  "customer": {"id": "cus_1a2b3c4d", "name": "Acme Corp", "created_at": 1786000000},
  "summary": {"mtd_cost_usd": 12.34, "mtd_calls": 152, "mtd_total_tokens": 4182051, "mtd_prompt_tokens": 3000000, "mtd_completion_tokens": 1182051},
  "budget": {"monthly_limit_usd": 100.0, "percent_used": 12.34, "remaining_usd": 87.66, "reset_day": 1}
}
```
404 with `{"detail": "unknown customer"}` when the id does not exist.

### 5.4 `PUT /v1/product/customers/{customer_id}/budget` — body `{"monthly_limit_usd": 100.0}` → 200

```json
{"customer_id": "cus_1a2b3c4d", "monthly_limit_usd": 100.0, "percent_used": 12.34, "remaining_usd": 87.66, "reset_day": 1}
```
422 on non-positive limit.

### 5.5 `GET /v1/product/customers/{customer_id}/daily-spend?days=31&granularity=day` → 200

```json
{
  "customer_id": "cus_1a2b3c4d",
  "granularity": "day",
  "points": [
    {"date": "2026-08-01", "cost_usd": 0.42, "calls": 7, "total_tokens": 90123},
    {"date": "2026-08-07", "cost_usd": 1.10, "calls": 19, "total_tokens": 240512}
  ]
}
```
`granularity` values: `day` (default, last `days` days), `week` (ISO week buckets, `strftime('%Y-W%W')`), `month` (calendar month buckets). `days` clamp `1..90`.

### 5.6 `GET /v1/product/customers/{customer_id}/models` → 200 (MTD by default)

```json
{
  "customer_id": "cus_1a2b3c4d",
  "since_epoch": 1785600000,
  "models": [
    {"model": "gpt-4o", "cost_usd": 8.20, "calls": 61, "total_tokens": 2100000},
    {"model": "gemini-3.6-flash", "cost_usd": 4.14, "calls": 91, "total_tokens": 2082051}
  ]
}
```

### 5.7 `GET /v1/product/customers/{customer_id}/export.csv` → 200 `text/csv`

```csv
customer,timestamp,model,tokens,cost
Acme Corp,2026-08-07T10:15:30Z,gpt-4o,1500,0.007500
Acme Corp,2026-08-07T10:14:02Z,gemini-3.6-flash,890,0.000089
```
- Header exactly `customer,timestamp,model,tokens,cost` (acceptance criterion 3).
- One row per ledger entry, newest first; `tokens` = `total_tokens`; `cost` = `total_cost` (6 decimals).
- `Content-Disposition: attachment; filename="<customer-name>-usage.csv"`.

### 5.8 Data-plane hook (non-endpoint)

In `gateway_proxy.py`: after `resolve_client_identity`, resolve `customer_id` (metadata > header > name match) and set `record.customer_id` before `tracker.record()`. Best-effort: unknown customer → `None` (rows remain attributed to `client_id` only).

---

## 6. UI Requirements — Customer Detail Page

Land in the React cockpit (`ui/src/main.tsx`) following the existing pattern (new `View` value `'customers'`, nav entry `['customers','Customers','◈']` after `Usage`; backend fetch via `api('/v1/product/customers')`).

### 6.1 Customers list page
- Header "Customers", description "Per-customer LLM spend, budgets and usage export.", primary action "+ Add customer" (modal with name input; POST `/v1/product/customers`).
- Table/cards: name, MTD cost (USD), MTD calls, MTD tokens, budget bar (if set) with `% used`, last activity. Row click → detail page.
- Empty state: "Connect a customer first" with the add action.

### 6.2 Customer detail page (GUI flow, exact order)
1. Back link to Customers list.
2. **Spend summary card** — MTD cost (`$12.34`), call count (`152`), token volume (`4.18M`). (AC1)
3. **Daily spend chart** — bar/line chart of `daily-spend` points; granularity toggle **Day / Week / Month**; `days=31` default. (AC1, GUI step 4)
4. **Breakdown by model** — list/table from `/models`: model, cost, calls, tokens, sorted by cost desc; each model a colored segment matching the existing palette. (GUI step 5)
5. **Budget progress bar** — `percent_used` with `remaining_usd`; color states: <80% good, 80–100% warning, ≥100% danger; "+ Set budget" inline edit (PUT budget). (GUI step 6)
6. **Export** button — `<a href="/v1/product/customers/{id}/export.csv" download>` triggering the CSV download. (GUI step 7, AC3)
7. Request table (optional but recommended): recent ledger rows from `/daily-spend?granularity=day` `calls` side or a new paginated endpoint — P1, not required for AC.

Accessibility: `aria-label` on chart/export, `:focus-visible` outlines, responsive ≤760px stacking — match existing cockpit standards.

---

## 7. Prioritized Task List

### P0 — MVP spine (US-001 acceptance)

| # | Task | Module | Interface | Depends on |
|---|---|---|---|---|
| P0-1 | Usage-event customer attribution | `cost_tracking.py` (`UsageRecord.customer_id`, `CostStore` migration + index), `gateway_proxy.py` (resolve + tag), `cost_attribution.py` (`CostAttributionStore` skeleton) | `UsageRecord.customer_id: str \| None`; `CostStore.attribution_store()` accessor | — |
| P0-2 | Customer registry + budgets | `cost_attribution.py` (`customers`, `customer_budgets` tables, CRUD, `set_monthly_budget`) | store methods §4.4; endpoints §5.1–5.4 | P0-1 |
| P0-3 | Per-customer aggregation (MTD / daily / by model) | `cost_attribution.py` queries §4.5 | endpoints §5.5–5.6 | P0-1 |
| P0-4 | CSV export | `cost_attribution.py` `ledger_rows()`; `console_api.py` endpoint | §5.7 | P0-2 |
| P0-5 | API wiring in console | `console_api.py` (7 endpoints) | §5 | P0-2, P0-3, P0-4 |
| P0-6 | Customers UI (list + detail) | `ui/src/main.tsx` (+ tests in `ui/src/*.test.tsx`) | §6 | P0-5 |

### P1 — polish (post-acceptance)
- P1-1 Recent-activity table on customer detail (paginated raw calls per customer — extend `daily_usage` with `customer_id` filter).
- P1-2 Budget forecast card (reuse `BudgetForecast` on the customer's daily series).
- P1-3 Customer "last 24h" sparkline + weekly digest email (out of scope for board; product backlog).

### P2 — platform extensions (documented, not built)
- P2-1 Tenant-aware RBAC on customer endpoints (Option B: `X-Tenant-Id`/`X-Role` gate, `customers.tenant` column already present).
- P2-2 Redis-backed write path for multi-instance scale-out (event bus → aggregator).
- P2-3 Automatic customer creation from `client_id` on first request (self-serve attribution).

---

## 8. Acceptance Criteria per Task

### P0-1 (attribution write path)
- [ ] `UsageRecord` has `customer_id: str | None`; `CostStore` migration adds column + `(customer_id, timestamp)` index idempotently (run twice on same DB).
- [ ] `gateway_proxy` resolves customer via `metadata.customer_id` > `X-Gateway-Customer-Id` > `customers.name == client_id`; unknown → `None`, never raises.
- [ ] After `CostTracker.record()` of a completed request with a customer, `mtd_summary` includes it **immediately** (attribution SLO ≤ 60s structurally; test asserts 0s freshness).
- [ ] Interface tests pass on stub; behavioral tests RED (`NotImplementedError`) until implemented.

### P0-2 (registry + budgets)
- [ ] `create_customer` persists and rejects duplicates (ValueError); `list_customers` returns MTD summary per customer in one query.
- [ ] `set_monthly_budget` upserts; `get_budget` returns limit + `percent_used` computed from ledger spend (not a stale counter).
- [ ] Budget progress reflects ledger MTD spend: `percent_used = min(100, spend/limit*100)`, `remaining = max(0, limit-spend)`.

### P0-3 (aggregation)
- [ ] `daily_spend(day|week|month)` returns correct buckets; `spend_by_model` sorted cost desc; all customer-scoped (other customers' rows excluded).
- [ ] MTD window = current calendar month (UTC); `days` clamp 1..90.

### P0-4 (CSV export)
- [ ] CSV header exactly `customer,timestamp,model,tokens,cost`; one row per ledger entry; `tokens` = total_tokens, `cost` = total_cost; ISO-8601 UTC timestamps; newest first; content-type `text/csv`.
- [ ] Empty customer → header-only CSV (200), no crash.

### P0-5 (API wiring)
- [ ] All 7 endpoints live under `/v1/product/customers*`; 404 unknown customer; 422 invalid body; 409 duplicate name; shapes match §5 (verified via httpx ASGITransport tests).

### P0-6 (UI)
- [ ] 'Customers' nav entry present; list page renders customers with MTD cost/calls/tokens + budget bar; row click opens detail.
- [ ] Detail page renders: summary card (MTD cost, call count, token volume), daily chart with Day/Week/Month toggle, model breakdown, budget progress bar with % consumed, Export button downloading the CSV.
- [ ] Verified visually with `browser_vision`; `bash ~/.hermes/scripts/ui-gate.sh <repo>` PASS.

### Quality gates (all tasks)
- [ ] `.venv/bin/python -m pytest` full suite green (baseline 1016; new tests added by pre-tester).
- [ ] `.venv/bin/python -m ruff check src/ tests/` clean.
- [ ] All runtime deps pinned in `pyproject.toml` (none new expected).
- [ ] No modifications to existing test files (except where the analyst spec requires).

---

## 9. Handoff Protocol (for pre-tester t_62736001 → developer t_5fd5e292 → tester t_d3f0b20d)

1. **pre-tester (t_62736001):** write `tests/test_cost_attribution.py` — interface tests (imports, `CostAttributionStore` signatures/type hints, `UsageRecord.customer_id`, dataclass shapes) passing on a stub `cost_attribution.py` raising `NotImplementedError`; behavioral tests (record→MTD, daily buckets, by-model, budget percent, CSV rows incl. header) RED. Also `ui/src/customers.test.tsx` source-contract tests if the dev task accepts them (existing `main.test.tsx` pattern).
2. **developer (t_5fd5e292):** implement per §4–§5 against pre-tests; keep `cost_attribution.py` module + `console_api.py` wiring + `gateway_proxy.py` hook. If a pre-test conflicts with this brief, the pre-test wins (board policy: tests are the spec).
3. **developer (t_30c09377, UI):** implement §6 in the cockpit; verify with browser_vision + ui-gate.
4. **tester (t_d3f0b20d):** full suite + ruff per repo .venv; verify UI against live backend; report per-module totals; block (dependency) with failing test names on any failure — do not fix.
5. **tech-lead (t_fe28737e root):** reviews and completes.

### Explicit scope boundaries
- No new runtime dependencies; no Redis; no schema rewrite of `cost_records`.
- The 2010s-era vanilla `console_ui.py` catalog is NOT the delivery target — Customers lives in the React cockpit (`ui/`).
- `control_plane.export_spend_csv` (reservations-based) stays untouched; the new CSV export is the US-001 deliverable.
