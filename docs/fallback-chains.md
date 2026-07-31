# Fallback Chains

When a model fails, the gateway can retry the request against the next
model in an ordered chain — automatically, without the client noticing
(except for a slower response and a different `model` in the body).

## Configuration

Chains are configured per logical model via
`GATEWAY_FALLBACK_CONFIGS` (JSON list):

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

| Field | Type | Default | Semantics |
|---|---|---|---|
| `model` | str | — | Logical model name (must match the request body) |
| `chain` | list[str] | — | Ordered fallback models, tried after `model` |
| `on` | list[str] | `["rate_limit", "server_error", "timeout"]` | Trigger classes that justify a fallback |
| `cooldown_seconds` | int | `60` | How long a failed model is skipped (stampede protection) |
| `disable` | bool | `false` | Config-level kill switch for the chain |

Models in a chain are also accepted by the request path (`/v1/models`
lists them, and `_model_known` accepts them).

## Error classification

`FallbackManager.classify_error(exc, status_code)` maps failures to
trigger classes:

| Class | Detected by |
|---|---|
| `rate_limit` | `RateLimitExceededError` or status `429` |
| `timeout` | `TimeoutError` (incl. `ProviderTimeoutError`), or "timeout"/"timed out" in the message |
| `server_error` | status in `500..599` |
| `content_policy` | "content management policy" / "content filter" / "filtered due to" in the message |
| `context_window` | "context length" / "maximum context" in the message |
| `unknown` | anything else — **never** triggers a fallback |

Fallback happens only when the classified class is in the config's
`on` list. `content_policy` and `context_window` are **off by default**
— semantics (quality, tool-calling) change across models, so those
failures surface to the client instead of silently switching models.
Tool-calling requests should only fall back to tool-capable models.

## Dispatch flow

`FallbackManager.dispatch(proxy, model, body, api_key, headers)`:

1. Build the candidate list: `[model] + chain` (cooldown-filtered).
2. For each candidate in order:
   - **Context pre-check** — if the estimated prompt tokens exceed the
     model's context budget (litellm `max_input_tokens`, fallback
     128k), the model is **skipped pre-call** — no provider call, no
     tokens spent.
   - Call `proxy.forward(candidate, body)` (timeout-bounded).
   - On success → return the response; `response.model` is the **serving
     model** (cost is recorded against it).
   - On failure → classify; if not in `on`, re-raise immediately; else
     `mark_failed(candidate)` (starts its cooldown) and move on.
3. Chain exhausted → re-raise the **last** exception → the proxy maps it
   to `502`.

`disable_fallbacks=True` (per-call) or `disable: true` (per-config)
reduces the candidate list to `[model]` and re-raises the original
error on failure.

## Cooldowns

After a model fails, it is excluded from chains for
`cooldown_seconds` (default 60s, per-model config lookup; models
without their own config default to 60s). `cooldown_seconds: 0`
disables cooldown entirely. This prevents stampedes onto a model that
just proved unhealthy.

## Streaming caveat

Fallback triggers only *before* the upstream stream starts: a stream
that dies mid-flight returns what it already emitted — the gateway
does not splice two providers' streams together. Clients should handle
truncated streams themselves.

## Examples

- `examples/fallback_chains.py` — classification table, cooldown
  filtering, dispatch falling back on `429`, `disable_fallbacks`, chain
  exhaustion (the `502` path), and the context pre-check skip
- `examples/quickstart.py` — `502` at the HTTP layer (provider timeout
  path) plus every other status code
