# Cost Tracking

Every proxied request produces one row in a SQLite (WAL) ledger with
token counts and dollar cost. The same ledger feeds budget enforcement
(see [Budget Configuration](budget-configuration.md)) — spend is
computed once and reused.

## Ledger schema

Table `cost_records` (SQLite, WAL journal mode, `request_id` primary
key):

| Column | Type | Meaning |
|---|---|---|
| `request_id` | TEXT PK | Gateway-generated request id |
| `api_key` | TEXT | Key id (`key:<id>` scope) when the scope kind is `key`, else `''` |
| `user_id` | TEXT NULL | Header-mapped user scope id, when present |
| `team` | TEXT NULL | Header-mapped team scope id, when present |
| `model` | TEXT | Model that **served** the request (after fallback) |
| `provider` | TEXT | `litellm` |
| `prompt_tokens` / `completion_tokens` / `total_tokens` | INTEGER | From the provider `usage` object |
| `input_cost` / `output_cost` / `total_cost` | REAL | USD, token × price / 1e6 |
| `latency_ms` | INTEGER | Gateway-side round trip |
| `status` | TEXT | `success` \| `error` \| `timeout` (what the gateway actually writes; `fallback` is reserved in the dataclass but not currently emitted — a fallback-served request records `success` with the serving model) |
| `timestamp` | INTEGER | Epoch seconds |

Indexes exist on `(timestamp)` and `(api_key, timestamp)`.

Only token counts and cost are stored — never prompt/response content.

## Pricing

`PriceMap` resolves a model's price:

1. `GATEWAY_PRICING_OVERRIDES` first — use this for negotiated rates and
   self-hosted models that litellm does not price:

   ```bash
   export GATEWAY_PRICING_OVERRIDES='{"my-custom-model":{"input_cost_per_million":1.5,"output_cost_per_million":2.5}}'
   ```

2. litellm's `model_cost` baseline otherwise.
3. Unknown models price at **$0** without raising — a request still
   records tokens, so zero-price models do not bypass budget counting
   by token.

## Cost math

`CostCalculator.calculate(model, prompt_tokens, completion_tokens)`
returns `(input_cost, output_cost, total_cost)`:

```
input_cost  = prompt_tokens      × input_cost_per_million  / 1e6
output_cost = completion_tokens  × output_cost_per_million / 1e6
total_cost  = input_cost + output_cost
```

Example at gpt-4o litellm baseline ($2.50/1M in, $10.00/1M out), 1000
prompt + 500 completion tokens:

```
input  = 1000 × 2.50 / 1e6  = $0.002500
output =  500 × 10.00 / 1e6 = $0.005000
total  = $0.007500
```

Run `examples/cost_tracking.py` to see this computed live (with a
custom-model override included).

## Streaming cost

`stream=true` responses are drained and chunk usage is aggregated with
`accumulate_usage()` before the record is written — partial-usage
chunks sum into one `TokenUsage`. A stream that never emits a usage
object records zero tokens. The `$0-record` class of budget-bypass
bugs (streams costing nothing) is covered by regression tests.

## Failures

Timeout and error responses still record a row with `status=timeout`
or `status=error` and zero cost — the ledger is an audit trail of what
the gateway attempted, not just what it billed.

## Querying spend

`CostStore.spend_since(scope_key, since_epoch)` sums `total_cost` for a
scope in a window. Scope keys are `key:<id>`, `user:<id>`, `team:<id>`,
`global:default` (global sums across all keys). This is the query the
budget enforcer uses for hard-limit checks.

```bash
# total spend in the last hour for key id "key1"
sqlite3 gateway.db \
  "SELECT COALESCE(SUM(total_cost), 0) FROM cost_records
   WHERE api_key = 'key1' AND timestamp >= $(date +%s -d '1 hour ago');"

# per-model spend, last 30 days
sqlite3 gateway.db \
  "SELECT model, ROUND(SUM(total_cost), 4) AS spend
   FROM cost_records
   WHERE timestamp >= $(date +%s -d '30 days ago')
   GROUP BY model ORDER BY spend DESC;"
```

## Roadmap

- **P1** — `PostgresCostStore` swap-in behind the same interface for
  multi-instance deployments.
- **P2** — spend/admin API + dashboards (spend by key/team/model/user).
