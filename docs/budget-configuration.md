# Budget Configuration

Budgets are configured per **scope** in a YAML file
(`GATEWAY_BUDGET_CONFIG_PATH`, default `budgets.yaml`). The canonical
shape is `examples/budgets.example.yaml`.

## Scopes

Scopes are hierarchical: `global > team > user > key`. **Every scope
that applies to a request is checked** — one key blowing the team
budget blocks the whole team, not just that key.

For a request with `Authorization: Bearer sk-test-123` (mapped to key
id `key1`) and header `X-Team-Id: eng` (mapped via
`GATEWAY_USER_HEADER_MAPPINGS`), the checked scopes are:

1. `key:key1` — from the virtual key table
2. `team:eng` — from the header mapping (`user` and/or `team` kinds)
3. `global:default` — always present

## YAML shape

```yaml
scopes:
  - scope:
      kind: key           # key | user | team | global
      key: "sk_live_abc"  # scope id
    soft_limit: 25.0      # USD — alert only, never blocks
    hard_limit: 50.0      # USD — reject with 412 when exceeded
    window: "30d"         # 30s | 30m | 30h | 30d | daily | monthly
    tpm_limit: 90000      # tokens per minute (sync ceiling, 429)
    rpm_limit: 60         # requests per minute (sync ceiling, 429)

  - scope: {kind: team, key: "eng"}
    soft_limit: 500.0
    hard_limit: 1000.0
    window: "monthly"

  - scope: {kind: user, key: "42"}
    hard_limit: 100.0
    window: "30d"

  - scope: {kind: global, key: "default"}
    soft_limit: 5000.0
    hard_limit: 10000.0
    window: "monthly"
```

| Field | Type | Default | Semantics |
|---|---|---|---|
| `scope.kind` | str | — | `key` \| `user` \| `team` \| `global`; anything else → `ValueError` at load |
| `scope.key` | str | — | Scope id (must match the resolved key id / header value / `default`) |
| `soft_limit` | float | `null` | USD; alerting signal only — **never blocks** |
| `hard_limit` | float | `null` | USD; reject with `412` once spend in window ≥ limit |
| `window` | str | `"30d"` | Rolling window, see below |
| `tpm_limit` | int | `null` | Tokens per minute ceiling; sync pre-dispatch, `429` |
| `rpm_limit` | int | `null` | Requests per minute ceiling; sync pre-dispatch, `429` |

A scope with no limits configured is simply never enforced. Missing
budget file → no configs (requests pass enforcement); malformed YAML or
unknown scope kind → `ValueError` at startup; missing file path handled
by `create_app` (falls back to empty configs).

## Windows

| Window | Duration |
|---|---|
| `30s` / `30m` / `30h` / `30d` | 30 × the unit |
| `daily` | 86,400 s |
| `monthly` | seconds of the **current calendar month** (31 days in July, 28/29 in February) |

Windows are rolling buckets aligned to epoch boundaries — a 30s window
is bucket `(now // 30) * 30`. Counters and spend are evaluated against
the current bucket, so windows reset automatically.

## Enforcement model (two layers)

The sync/async split is the core design decision:

1. **Sync — TPM/RPM ceilings (pre-dispatch, race-free).** Atomic
   counters in `InMemoryCounterStore` are incremented *before* the
   request is forwarded. Hitting a ceiling raises
   `RateLimitExceededError` → `429`. This is the only true hard
   ceiling: it cannot be overshot by concurrency.

2. **Async — dollar budgets (post-response).** Cost is only knowable
   once the provider returns `usage`, so dollar accounting happens
   after the response. `soft_limit` → alerting signal
   (`soft_exceeded()` returns the scopes; nothing blocks). `hard_limit`
   → `BudgetExceededError` → `412` (Portkey convention, checked
   against spend since window start).

**Known exposure:** because dollar checks are async, a burst of
concurrent requests can overshoot a hard dollar limit before the spend
is recorded (the concurrency window). Token ceilings are the
counterweight; a true sync dollar cap requires reserve-and-reconcile
(P1, opt-in). The README and this guide intentionally do **not** claim
a hard dollar cap.

## HTTP mapping

| Condition | Status | Body |
|---|---|---|
| `tpm_limit` / `rpm_limit` exceeded | `429` | `{"error":{"message":"rate limit exceeded (rpm) for key:key2: 1",...}}` |
| `hard_limit` exceeded | `412` | `{"error":{"message":"budget exceeded for key:key1: 0.0125 >= 0.01",...}}` |
| soft limit crossed | — | no HTTP impact (alert channel only) |

`412` (budget) is deliberately distinct from `429` (rate) so clients
can treat overspend differently from throttling.

## Examples

- `examples/quickstart.py` — live `412` and `429` against a running app
  (fake provider)
- `examples/budget_enforcement.py` — library-level: windows, YAML
  loading, counter ceilings, composite scope blocking, soft reporting

## Roadmap

- **P1** — reserve-and-reconcile sync dollar caps; Redis `CounterStore`
  for multi-instance; alert webhooks (soft-budget ladder with
  once-per-window dedup).
- **P2** — virtual key lifecycle (issue/rotate/expire/revoke).
