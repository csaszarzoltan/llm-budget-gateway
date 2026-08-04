# Proxy Setup

The gateway is an OpenAI-compatible proxy: your clients point their
base URL at it, keep sending OpenAI-shaped requests, and the gateway
handles auth, budgets, rate ceilings, and fallback routing before
forwarding to the real provider via the LiteLLM SDK.

## Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/chat/completions` | Chat completions (chat / tool-calling bodies) |
| `POST` | `/v1/completions` | Legacy completions (`prompt` bodies) |
| `POST` | `/v1/embeddings` | Embeddings (`input` bodies → `litellm.aembedding`) |
| `GET` | `/v1/models` | Gateway-configured models + all litellm-known models |
| `GET` | `/health` | Liveness probe |

## Request lifecycle

For every proxied request the gateway runs, in order:

1. **Auth** — resolve the `Authorization: Bearer <key>` virtual key to a
   key id via `GATEWAY_VIRTUAL_KEYS`; unknown/missing key → `401`.
2. **Model check** — the requested model must be gateway-configured
   (pricing override or fallback chain) or litellm-known; else `404`.
3. **Sync enforce** — TPM/RPM counters are incremented pre-dispatch;
   ceiling hit → `429` (the only truly race-free ceiling).
4. **Hard budget check** — dollar spend per scope since window start vs
   `hard_limit`; over → `412`.
5. **Forward with fallback** — `FallbackManager.dispatch()` walks the
   chain (see [Fallback Chains](fallback-chains.md)); every attempt is
   bounded by `GATEWAY_PROVIDER_TIMEOUT`.
6. **Cost record** — token usage × price is persisted to the SQLite
   ledger (see [Cost Tracking](cost-tracking.md)); recording failures
   are logged and swallowed, never surfaced to the client.

## Environment configuration

Every setting is a `GATEWAY_`-prefixed environment variable
(pydantic-settings v2; JSON values for dict/list fields).

| Variable | Default | Description |
|---|---|---|
| `GATEWAY_DATABASE_URL` | `sqlite:///./gateway.db` | SQLite ledger path (`sqlite:///` prefix stripped to a filesystem path) |
| `GATEWAY_BUDGET_CONFIG_PATH` | `budgets.yaml` | YAML budget config file; missing file = no budget configs |
| `GATEWAY_VIRTUAL_KEYS` | `{}` | JSON dict: client API key → key id (`{"sk-test-123":"key1"}`) |
| `GATEWAY_USER_HEADER_MAPPINGS` | `{}` | JSON dict: header name → scope kind (`{"X-User-Id":"user","X-Team-Id":"team"}`) |
| `GATEWAY_PRICING_OVERRIDES` | `{}` | JSON dict: model → `{"input_cost_per_million": x, "output_cost_per_million": y}` |
| `GATEWAY_FALLBACK_CONFIGS` | `[]` | JSON list of fallback chain configs (see [Fallback Chains](fallback-chains.md)) |
| `GATEWAY_PROVIDER_TIMEOUT` | `60.0` | Max seconds for the first upstream byte and for each subsequent stream chunk; a stall past it → `502` |

A ready-to-edit copy lives at [`.env.example`](../.env.example) in the
repo root.

## Running

```bash
.venv/bin/uvicorn llm_budget_gateway.main:create_app --factory --port 8000
```

`--factory` is required: `create_app()` returns the FastAPI app and
reads configuration from the environment at call time.

## HTTP semantics

| Status | Meaning | Example body |
|---|---|---|
| `200` | Provider response (JSON, or SSE stream for `stream=true`) | provider-shaped body |
| `400` | Malformed request body (invalid JSON, or a JSON value that is not an object) | `{"error":{"message":"request body is not valid JSON","type":"invalid_request_error","code":400}}` |
| `401` | Missing / unknown virtual API key | `{"error":{"message":"invalid or missing api key","type":"invalid_request_error","code":401}}` |
| `404` | Unknown model | `{"error":{"message":"unknown model: no-such-model","type":"invalid_request_error","code":404}}` |
| `412` | Hard dollar budget exceeded (Portkey convention) | `{"error":{"message":"budget exceeded for key:key1: 0.0125 >= 0.01","type":"invalid_request_error","code":412}}` |
| `429` | TPM/RPM ceiling exceeded | `{"error":{"message":"rate limit exceeded (rpm) for key:key2: 1","type":"invalid_request_error","code":429}}` |
| `502` | Upstream provider error, timeout, or fallback chain exhausted | `{"error":{"message":"upstream provider timed out","type":"invalid_request_error","code":502}}` |

Error bodies follow the OpenAI error shape:
`{"error": {"message": ..., "type": "invalid_request_error", "code": <status>}}`.

The `412` vs `429` split is deliberate: budget exhaustion is distinct
from rate limiting so clients can treat them differently.

## Streaming

`stream: true` bodies are forwarded as streams. The gateway drains the
upstream stream, aggregates chunk usage (so the request is costed at
real spend, not $0), and re-frames the chunks as OpenAI-style SSE:

```
data: {"model": "gpt-4o", "choices": [{"delta": {"role": "assistant", "content": "Hel"}, "finish_reason": null}]}

data: {"model": "gpt-4o", "choices": [{"delta": {"content": "lo!"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500}}

data: [DONE]
```

`content-type: text/event-stream`. Each chunk must arrive within
`GATEWAY_PROVIDER_TIMEOUT`; a stalled stream surfaces as `502`.

Note: the upstream stream is fully drained (and thus buffered) before
the response is served — a deliberate trade-off so cost accounting stays
exact.

## Embeddings

Bodies with an `input` field (and no `messages`/`prompt`) are routed to
`litellm.aembedding`:

```bash
curl -s http://localhost:8000/v1/embeddings \
  -H "Authorization: Bearer sk-test-123" \
  -H "Content-Type: application/json" \
  -d '{"model": "text-embedding-3-small", "input": ["hello"]}'
```

## Models

`GET /v1/models` lists pricing overrides + all fallback-chain models +
every model in litellm's price table — i.e. exactly what the request
path will serve. Anything not in that set is a `404`.

## Security model

- **Client bodies are allow-listed.** Only a fixed set of OpenAI
  parameters (`model`, `messages`, `prompt`, `input`, `stream`,
  `temperature`, `tools`, ...) is forwarded. `api_key`, `api_base`,
  `base_url`, and `headers` in the client body are **dropped** — provider
  credentials and endpoints come exclusively from gateway settings/env.
  This closes SSRF and cost-bypass injection.
- **Keys never hit logs.** Failed auth attempts log a redacted form
  (`first4…Nch`), never the submitted key. Provider error bodies are
  mapped to generic `502` messages — raw exception text is not leaked.
- **No prompt content is stored.** The ledger keeps token counts and
  cost only.
- **Scopes from headers are opt-in.** Only headers listed in
  `GATEWAY_USER_HEADER_MAPPINGS` are trusted for user/team scoping; the
  mapping is configured by the operator, so header spoofing only
  affects enforcement scopes you explicitly opted into.
