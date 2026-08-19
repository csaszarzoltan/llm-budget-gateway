# Configuration Reference

All settings are loaded from **environment variables** with the
`GATEWAY_` prefix using [pydantic-settings v2](https://docs.pydantic.dev/latest/concepts/pydantic_settings/).
Dict/list fields accept JSON strings. A ready-to-edit copy lives at
[`.env.example`](../.env.example).

---

## Environment Variables

### Core Settings

| Variable | Type | Default | Description | Example |
|---|---|---|---|---|
| `GATEWAY_DATABASE_URL` | `str` | `sqlite:///./gateway.db` | SQLite ledger path. The `sqlite:///` prefix is stripped to a filesystem path. | `sqlite:///./data/gateway.db` |
| `GATEWAY_BUDGET_CONFIG_PATH` | `str` | `budgets.yaml` | Path to the YAML budget configuration file. A missing file means no budget configs are loaded. | `budgets.yaml` |

### Virtual Keys & Header Mappings

| Variable | Type | Default | Description | Example |
|---|---|---|---|---|
| `GATEWAY_VIRTUAL_KEYS` | `dict[str, str]` | `{}` | Static mapping from client API key to internal key id. Unknown or missing keys return `401`. | `{"sk-test-123": "key1"}` |
| `GATEWAY_USER_HEADER_MAPPINGS` | `dict[str, str]` | `{}` | Maps incoming header names to scope kinds (`"user"` or `"team"`). Only listed headers are trusted for scope resolution. | `{"X-User-Id": "user", "X-Team-Id": "team"}` |

### API Keys

| Variable | Type | Default | Description | Example |
|---|---|---|---|---|
| `GATEWAY_ASSURANCE_API_KEY` | `str` | — | API key for the Assurance subsystem. | `replace-with-a-strong-random-secret` |
| `GATEWAY_MCP_API_KEY` | `str` | — | API key for the MCP (Model Context Protocol) integration. | `replace-with-a-strong-random-secret` |

### Provider Configuration

| Variable | Type | Default | Description | Example |
|---|---|---|---|---|
| `GATEWAY_PRICING_OVERRIDES` | `dict[str, dict]` | `{}` | Per-model pricing overrides. Keys are model names; values have `input_cost_per_million` and `output_cost_per_million` (USD). Overrides litellm's baseline pricing. | See [Pricing Configuration](#pricing-configuration) below. |
| `GATEWAY_FALLBACK_CONFIGS` | `list[dict]` | `[]` | Fallback chain configurations — ordered lists of models to try when the primary fails. | See [Fallback Configuration](#fallback-configuration) below. |

### Network & Timeouts

| Variable | Type | Default | Description | Example |
|---|---|---|---|---|
| `GATEWAY_PROVIDER_TIMEOUT` | `float` | `60.0` | Max seconds to wait for the first upstream byte **and** for each subsequent stream chunk. A stall past this → `502`. | `30.0` |
| `GATEWAY_ROUTE_TIMEOUT_BUDGET` | `float` | `90.0` | Total wall-clock budget for a route's entire fallback chain. Prevents the sum of per-attempt timeouts from exceeding the client's patience. Once spent, remaining candidates are skipped and the last is tried with the leftover time. | `120.0` |

### Retry & Cooldown

| Variable | Type | Default | Description | Example |
|---|---|---|---|---|
| `GATEWAY_COOLDOWN_LADDER` | `list[int]` | `[60, 300, 900, 3600, 7200, 14400, 28800, 43200, 64800, 86400]` | Dynamic cooldown durations in seconds. A target that fails repeatedly escalates through this ladder: 1m → 5m → 15m → 1h → 2h → 4h → 8h → 12h → 18h → 1d. A successful call resets the strike count. | `[60, 300, 900, 3600]` |
| `GATEWAY_COOLDOWN_DYNAMIC` | `bool` | `true` | When `true`, uses the dynamic cooldown ladder. When `false`, every failure applies the same fixed `cooldown_seconds` (default 3600) and the ladder is ignored. | `true` |
| `GATEWAY_RETRY_BACKOFF_SECONDS` | `float` | `1.0` | Base seconds between retries of the same target. Grows exponentially (1s → 2s → 4s → 8s …), capped at `retry_backoff_max_seconds`. | `0.5` |
| `GATEWAY_RETRY_BACKOFF_MAX_SECONDS` | `float` | `10.0` | Upper bound for the exponential retry backoff. | `30.0` |

---

## Budget Configuration

Budgets are defined per scope in a YAML file pointed to by
`GATEWAY_BUDGET_CONFIG_PATH` (default `budgets.yaml`).

**For the full YAML format, scope hierarchy, enforcement model, and
examples, see [Budget Configuration](budget-configuration.md).**

Quick reference:

- **Scopes** are hierarchical: `global > team > user > key`.
- **Soft limits** produce alerts but never block requests.
- **Hard limits** reject with HTTP `412` when exceeded.
- **TPM/RPM ceilings** enforce token/request rate limits (`429`).
- **Windows** support `30s`, `30m`, `30h`, `30d`, `daily`, `monthly`.

---

## Fallback Configuration

Chains are configured via `GATEWAY_FALLBACK_CONFIGS` (JSON list). Each
entry defines a primary model, its ordered fallbacks, and which error
classes trigger a switch.

### Format

```json
{
  "model": "gpt-4o",
  "chain": ["gpt-3.5-turbo", "claude-3-5-haiku"],
  "on": ["rate_limit", "server_error", "timeout"],
  "cooldown_seconds": 60,
  "disable": false
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `model` | `str` | — | Primary model name (must match the request body). |
| `chain` | `list[str]` | — | Ordered fallback models tried after the primary fails. |
| `on` | `list[str]` | `["rate_limit", "server_error", "timeout"]` | Error classes that trigger a fallback. |
| `cooldown_seconds` | `int` | `60` | How long a failed model is excluded from chains. Set `0` to disable. |
| `disable` | `bool` | `false` | Kill switch — reduces candidate list to `[model]` only. |

### Error Classification

| Class | Detected by |
|---|---|
| `rate_limit` | `RateLimitExceededError` or HTTP `429`. |
| `timeout` | `TimeoutError`, `ProviderTimeoutError`, or "timeout"/"timed out" in message. |
| `server_error` | Status code `500–599`. |
| `content_policy` | "content management policy", "content filter", or "filtered due to" in message. |
| `context_window` | "context length" or "maximum context" in message. |
| `unknown` | Anything else — **never** triggers a fallback. |

`content_policy` and `context_window` are **off by default**. See
[Fallback Chains](fallback-chains.md) for dispatch flow and streaming
caveats.

### Example

```bash
export GATEWAY_FALLBACK_CONFIGS='[
  {
    "model": "gpt-4o",
    "chain": ["gpt-3.5-turbo", "claude-3-5-haiku"],
    "on": ["rate_limit", "server_error", "timeout"],
    "cooldown_seconds": 60,
    "disable": false
  }
]'
```

---

## Pricing Configuration

### Default Pricing

The gateway uses litellm's built-in `model_cost` table as the baseline
for all known models. No configuration is needed for standard OpenAI,
Anthropic, and other litellm-supported providers.

### Pricing Overrides

Override any model's price via `GATEWAY_PRICING_OVERRIDES` (JSON dict).
Overrides take precedence over litellm's baseline.

```json
{
  "model-name": {
    "input_cost_per_million": <float>,
    "output_cost_per_million": <float>
  }
}
```

### Per-Model Examples

```bash
# Override a self-hosted model's pricing
export GATEWAY_PRICING_OVERRIDES='{
  "my-custom-model": {
    "input_cost_per_million": 1.5,
    "output_cost_per_million": 2.5
  }
}'
```

**Unknown models** price at $0 without raising — requests still record
tokens so zero-price models do not bypass budget counting by token.

Cost math: `input_cost = prompt_tokens × input_cost_per_million / 1e6`.

See [Cost Tracking](cost-tracking.md) for the full ledger schema and
querying spend.

---

## Database

### SQLite Path

Set `GATEWAY_DATABASE_URL` to control the ledger file location. The
`sqlite:///` prefix is stripped to a filesystem path:

```
sqlite:///./gateway.db       →  ./gateway.db
sqlite:///./data/gateway.db  →  ./data/gateway.db
```

### WAL Mode

The SQLite ledger uses **Write-Ahead Logging (WAL)** journal mode by
default. WAL allows concurrent readers during writes — essential for
the budget enforcer which reads spend while the cost tracker records
new entries.

### Backup Strategy

For production, back up the SQLite file while the gateway is running.
WAL mode supports safe hot backups:

```bash
sqlite3 gateway.db ".backup gateway-backup-$(date +%Y%m%d).db"
```

The ledger is append-only and non-destructive — if lost, the gateway
continues operating with zero historical spend data (budget enforcement
resets).

See [Cost Tracking](cost-tracking.md) for the full ledger schema.
