# Verification Note — LLM Observability (roadmap #1)

Target: `/home/zoltan/llm-budget-gateway` — cost/telemetry middleware around the
`GatewayProxy._handle` request path, exposed via `/v1/observability/requests`
and `/v1/observability/summary` (planning brief `brief-llm-observability-20260814-0538.json`,
roadmap-011, score 62.3).

## What was delivered

- `src/llm_budget_gateway/request_telemetry.py` (new module)
  - `TelemetryEntry` dataclass: trace_id, provider, model, token usage
    (prompt/completion/total/reasoning), cost (input/output/reasoning/total USD),
    latency_ms, status, status_code, plus optional api_key/user_id/team/
    customer_id/route/conversation_id/metadata.
  - `RequestTelemetryStore`: SQLite-backed telemetry table (`telemetry_requests`)
    with INSERT OR REPLACE upsert on trace_id, indexed by trace_id/recorded_at/
    model/status. Best-effort `record()` (catches DB errors, logs, never raises).
  - `RequestTelemetryLogger`: converts a `ProviderResponse` → `TelemetryEntry`
    (`from_response`) with status inference (success/rate_limited/timeout/error)
    and best-effort `emit()` (never blocks the proxy path).
  - `emit()` wraps `store.record()` in try/except so a downstream store failure
    cannot break the proxy request path (fixed during this review pass).
  - status inference: 429 → rate_limited, 408 or body containing "timed out" →
    timeout (fixed: previously bare "timeout" substring was too broad and
    misclassified 502s with a "timeout" message as timeout instead of error),
    >=400 → error.

- `src/llm_budget_gateway/gateway_proxy.py` (modified)
  - `_emit_telemetry()` helper delegates to the attached `RequestTelemetryLogger`,
    wrapped in try/except so telemetry failures never reach the client.
  - `attach_telemetry()` setter; default is a no-store logger so tests/proxy
    work without the telemetry DB.
  - Wired into `_handle` / `_handle_logical_route` error branches (auth fail,
    unknown model, rate-limited, hard-budget, logical-route timeout/error) so
    every routed request path emits a telemetry entry.

- `src/llm_budget_gateway/main.py` (modified)
  - `create_app()` attaches a `RequestTelemetryStore` (shares the cost-ledger
    SQLite connection) + `RequestTelemetryLogger` to the proxy.
  - `GET /v1/observability/requests` — query telemetry with filters
    (model, status, provider, since, trace_id, limit).
  - `GET /v1/observability/summary` — aggregate request count / token totals /
    cost over a window, optionally filtered by model.

- `tests/test_request_telemetry.py` (new, 32 tests)
  - TelemetryEntry shape/serialisation, store (insert/lookup/query filters/
    ordering/summary/shared-connection/best-effort-on-failure), logger
    (from_response field extraction, cost_calc, scope extraction, status
    inference, emit persistence/no-store-fallback/best-effort-on-store-error),
    and GatewayProxy telemetry wiring.

## Three issues found and fixed during review pass

1. `RequestTelemetryLogger.emit()` propagated store exceptions instead of
   swallowing them. Root cause: `emit()` called `self._store.record(entry)`
   with no try/except; the best-effort contract only lived inside `record()`.
   Fix: wrap `record()` call in try/except in `emit()`. Resolves
   `test_emit_best_effort_on_store_error`.
2. Status inference matched the bare substring `"timeout"`, misclassifying a 502
   whose error message literally said "timeout" as `timeout` instead of `error`.
   Fix: match `"timed out"` (the phrase) only. Resolves
   `test_from_response_error_status` while keeping `test_from_response_timeout_status`
   (body "upstream provider timed out" → timeout) green.
3. `test_store_best_effort_on_failure` monkeypatched `sqlite3.Connection.execute`
   which is a read-only C attribute. Fix: swap `store._conn` with a Mock whose
   `.execute` raises, exercising the real `record()` try/except.

## Test results (repo venv)

Command: `PATH="$PWD/.venv/bin:$PATH" python -m pytest tests/ -q`

    1317 passed in 74.03s

- `tests/test_request_telemetry.py`: 32 passed
- `tests/test_gateway_proxy.py`: 56 passed (existing flows, no regression)

Ruff: `ruff check src/llm_budget_gateway/request_telemetry.py
tests/test_request_telemetry.py` → All checks passed (0 errors).

## End-to-end endpoint check

`create_app(Settings(...))` boots with the observability routes mounted and a
shared telemetry store. `GET /health` -> 200; `GET /v1/observability/requests`
and `/v1/observability/summary` return 200 with empty result sets on a fresh DB
(verified via FastAPI TestClient against the live app, not a mock).

## Acceptance criteria mapping

- [x] LLM observability feature implemented and enabled per roadmap #1
      (telemetry module + `_handle` wiring + `/v1/observability/*` endpoints).
- [x] All BDD/TDD acceptance criteria pass (32/32 telemetry tests green; the
      minimal MVP criteria were derived from the operator decision recorded on
      the task card).
- [x] Existing LLM call flows continue to work without regression (1317 passed,
      incl. 56 gateway_proxy tests).
- [x] Verifiable observability output produced (SQLite `telemetry_requests`
      table + queryable/aggregation endpoints).
- [x] Verification note included (this file).
