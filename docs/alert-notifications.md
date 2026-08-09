# Budget Alert Notifications

Notify teams when budget thresholds are breached. The alert dispatch
system evaluates rules against live spend, routes triggered alerts to
the configured channel (webhook, Slack, Telegram, or email), and logs
every attempt for audit.

## Overview

The system has three layers:

1. **Alert Rules API** — create, list, and delete rules via FastAPI
   endpoints (`/api/alerts`). Each rule names a threshold (0.0–1.0
   ratio), a notification channel, and channel-specific config.
2. **Dispatch Engine** — `AlertDispatcher` routes triggered alerts to
   the correct channel adapter with cooldown dedup, exponential-backoff
   retry, and per-attempt logging.
3. **Control Plane Integration** — `evaluate_alerts()` compares live
   spend against rule thresholds and fires dispatch asynchronously
   (never blocks the request path).

```
                         ┌──────────────┐
  POST /api/alerts ─────▶│ Alert Rules  │──▶ SQLite (alert_rules)
                         │    CRUD API  │
                         └──────┬───────┘
                                │
  evaluate_alerts() ───────────▶│
       (control_plane)          │
                         ┌──────▼───────┐
                         │   Alert      │──▶ SQLite (alert_dispatch_log)
                         │  Dispatcher  │
                         └──────┬───────┘
                                │
              ┌────────┬────────┼────────┬────────┐
              ▼        ▼        ▼        ▼        ▼
           Webhook   Slack   Telegram  Email   (adapter)
```

## Alert Rules API

### Create rule

```
POST /api/alerts
```

Request body (JSON):

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Human-readable rule name (min 1 char) |
| `threshold` | float | yes | Spend ratio to trigger (0.0–1.0, e.g. `0.8` = 80%) |
| `channel` | string | yes | `webhook`, `slack`, `telegram`, or `email` |
| `config` | object | yes | Channel-specific fields (see below) |
| `cooldown_seconds` | int | no | Min seconds between dispatches (default `300`) |
| `enabled` | bool | no | Enable/disable rule (default `true`) |

**Example — create a webhook rule:**

```json
{
  "name": "High spend alert",
  "threshold": 0.8,
  "channel": "webhook",
  "config": {
    "url": "https://hooks.example.com/budget",
    "secret": "hmac-signing-secret"
  },
  "cooldown_seconds": 600,
  "enabled": true
}
```

Response `201`:

```json
{
  "id": "a1b2c3d4e5f6a7b8",
  "name": "High spend alert",
  "threshold": 0.8,
  "channel": "webhook",
  "config": {"url": "https://hooks.example.com/budget", "secret": "..."},
  "cooldown_seconds": 600,
  "enabled": true
}
```

**Validation error `422`** — missing required config fields for the
channel:

```json
{
  "detail": "Channel 'slack' requires config fields: ['bot_token', 'channel']"
}
```

### List all rules

```
GET /api/alerts
```

Response `200`:

```json
{
  "items": [
    {
      "id": "a1b2c3d4e5f6a7b8",
      "name": "High spend alert",
      "threshold": 0.8,
      "channel": "webhook",
      "config": {"url": "https://hooks.example.com/budget", "secret": "..."},
      "cooldown_seconds": 600,
      "enabled": true
    }
  ]
}
```

### Get one rule

```
GET /api/alerts/{rule_id}
```

Response `200` — single rule object. Response `404` — `{"detail": "Alert rule not found"}`.

### Delete a rule

```
DELETE /api/alerts/{rule_id}
```

Response `204` on success, `404` if not found.

### Dispatch history

```
GET /api/alerts/history?page=1&page_size=20&alert_rule_id=...&channel=...&delivery_status=...
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | int | 1 | Page number (1-indexed) |
| `page_size` | int | 20 | Items per page |
| `alert_rule_id` | string | — | Filter by rule id |
| `channel` | string | — | Filter by channel |
| `delivery_status` | string | — | `delivered`, `failed`, or `pending` |

Response `200`:

```json
{
  "items": [
    {
      "id": 1,
      "alert_rule_id": "a1b2c3d4",
      "channel": "webhook",
      "delivery_status": "delivered",
      "response_code": 200,
      "error_message": null,
      "dispatched_at": 1691000000.0
    }
  ],
  "total": 42
}
```

## Channel Configuration

### Webhook (HMAC-SHA256 signed)

Webhook payloads are signed with HMAC-SHA256 using the existing
`SignedWebhook` utility. The signature is sent in the `X-Signature-256`
header.

Required config fields:

| Field | Description |
|---|---|
| `url` | Target webhook URL |
| `secret` | HMAC signing secret (shared with your receiver) |

**Create a webhook rule:**

```python
import requests

requests.post("http://localhost:8000/api/alerts", json={
    "name": "Webhook alert",
    "threshold": 0.8,
    "channel": "webhook",
    "config": {
        "url": "https://hooks.example.com/budget",
        "secret": "my-hmac-secret"
    }
})
```

**Receiver verification (Python):**

```python
import hashlib
import hmac

def verify_webhook(payload_bytes: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(
        secret.encode(), payload_bytes, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)
```

### Slack

Posts to a Slack channel via the `chat.postMessage` Web API.

Required config fields:

| Field | Description |
|---|---|
| `bot_token` | Slack bot OAuth token (`xoxb-...`) |
| `channel` | Channel ID or name (e.g. `#budget-alerts`) |

**Create a Slack rule:**

```python
import requests

requests.post("http://localhost:8000/api/alerts", json={
    "name": "Slack alert",
    "threshold": 0.9,
    "channel": "slack",
    "config": {
        "bot_token": "xoxb-your-bot-token",
        "channel": "#budget-alerts"
    }
})
```

**Bot setup steps:**

1. Create a Slack app at https://api.slack.com/apps
2. Enable "Bot Token Scopes" → add `chat:write`
3. Install the app to your workspace
4. Copy the Bot User OAuth Token (`xoxb-...`)
5. Invite the bot to the target channel: `/invite @YourBot`

### Telegram

Sends messages via the Telegram Bot API (`sendMessage`).

Required config fields:

| Field | Description |
|---|---|
| `bot_token` | Telegram bot token (from @BotFather) |
| `chat_id` | Target chat or channel ID |

**Create a Telegram rule:**

```python
import requests

requests.post("http://localhost:8000/api/alerts", json={
    "name": "Telegram alert",
    "threshold": 0.85,
    "channel": "telegram",
    "config": {
        "bot_token": "123456:ABC-DEF...",
        "chat_id": "-1001234567890"
    }
})
```

**Bot setup steps:**

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot`, follow the prompts
3. Copy the bot token
4. Add the bot to your group/channel
5. Get the chat ID: send a message, then visit
   `https://api.telegram.org/bot<TOKEN>/getUpdates`

### Email

Sends alert emails via SMTP with optional STARTTLS.

Required config fields:

| Field | Description |
|---|---|
| `host` | SMTP server hostname (e.g. `smtp.gmail.com`) |
| `username` | SMTP auth username |
| `to_address` | Recipient email address |

Optional fields:

| Field | Default | Description |
|---|---|---|
| `port` | `587` | SMTP port |
| `password` | — | SMTP auth password |
| `from_address` | `username` | Sender address |
| `use_tls` | `true` | Enable STARTTLS |

**Create an email rule:**

```python
import requests

requests.post("http://localhost:8000/api/alerts", json={
    "name": "Email alert",
    "threshold": 0.75,
    "channel": "email",
    "config": {
        "host": "smtp.gmail.com",
        "username": "you@gmail.com",
        "password": "app-password",
        "to_address": "ops-team@example.com"
    }
})
```

Email is dispatched via `smtplib` in a thread executor with a 10-second
timeout per attempt.

## Cooldown and Dedup

The dispatcher tracks the last successful dispatch timestamp per rule.
If a rule fires again within its `cooldown_seconds` window, the
dispatch is suppressed and returns `False` (no `AlertDispatchLog` entry
is written — the suppression is recorded on the module logger only).

| Parameter | Default | Description |
|---|---|---|
| `cooldown_seconds` | `300` (5 min) | Set per rule in `POST /api/alerts` |
| Default `AlertDispatcher.cooldown_seconds` | `300` | Class-level fallback |

**Behavior:**

- Cooldown starts on the first successful dispatch for a rule.
- A fresh rule with no prior dispatch is never suppressed.
- Failed dispatches do not reset the cooldown timer.
- Setting `cooldown_seconds: 0` disables dedup entirely.

## Retry and Error Handling

On adapter failure, the dispatcher retries with exponential backoff:

| Attempt | Delay | Cumulative |
|---|---|---|
| 1 (initial) | — | 0s |
| 2 (retry 1) | 1s | 1s |
| 3 (retry 2) | 2s | 3s |

Default: `retries=3` (3 total attempts: initial + 2 retries).
Backoff formula: `backoff_base * 2^attempt` (base defaults to `1.0`).

| Parameter | Default | Description |
|---|---|---|
| `retries` | `3` | Total attempts including initial |
| `backoff_base` | `1.0` | Base seconds for exponential backoff |

**All adapters never raise.** Transient failures return `False` rather
than propagating an exception. The dispatcher retries until success or
until retries are exhausted, then returns `False`.

**Logging behavior.** Every *completed* attempt is recorded as an
`AlertDispatchLog` entry:

- The **first attempt** succeeds → logged as `delivered`; fails → logged
  only when the first retry fails (as `failed`).
- Each **retry** that fails is logged as `failed`.
- The **terminal outcome** is always logged: `delivered` on success, or
  `pending` (`error_message: "retries exhausted"`) when all attempts fail.

For example, with `retries=3` and success only on the final attempt, the
history shows `[failed, delivered]` (the silent first failure surfaces
as the first retry's `failed` entry, then the success). With `retries=2`
and all attempts failing, history shows `[failed, pending]`.

**Status codes in dispatch history:**

| `delivery_status` | Meaning |
|---|---|
| `delivered` | Adapter returned success; `response_code` is set |
| `failed` | A retry attempt failed; `error_message` describes it |
| `pending` | All retries exhausted; alert was never delivered |

## Alert History

Every completed dispatch attempt (successes, failed retries, and the
terminal pending state) is logged as an `AlertDispatchLog` entry
persisted via the alert API's `app.state.record_dispatch` sink.

**Query history:**

```bash
# All history, newest first
curl -s http://localhost:8000/api/alerts/history

# Filter by rule
curl -s "http://localhost:8000/api/alerts/history?alert_rule_id=a1b2c3d4"

# Filter by channel and status
curl -s "http://localhost:8000/api/alerts/history?channel=slack&delivery_status=failed"
```

**History entry fields:**

| Field | Type | Description |
|---|---|---|
| `id` | int | Auto-incremented row id |
| `alert_rule_id` | string | Rule that triggered the dispatch |
| `channel` | string | `webhook`, `slack`, `telegram`, or `email` |
| `delivery_status` | string | `delivered`, `failed`, or `pending` |
| `response_code` | int \| null | HTTP status code (on HTTP adapters) |
| `error_message` | string \| null | Error detail (on failure) |
| `dispatched_at` | float | Unix timestamp of the attempt |

## Control Plane Integration

`evaluate_alerts()` in the control plane compares each rule's threshold
against the current spend ratio and triggers dispatch asynchronously:

```python
from llm_budget_gateway.control_plane import ControlPlane
from llm_budget_gateway.dispatch_engine import AlertDispatcher

plane = ControlPlane("gateway.db")  # SQLite control-plane database path
dispatcher = AlertDispatcher()

# Dispatch is non-blocking — triggered alerts fire in the background
results = plane.evaluate_alerts(t="t1", dispatch=dispatcher)
```

**How it works:**

1. `evaluate_alerts` computes `ratio = (spent + reserved) / limit` for
   the tenant's `global` scope.
2. For each rule where `ratio >= threshold`, the rule state is updated
   to `"triggered"` and an `AlertEvent` is built.
3. The event is dispatched via `_fire_and_forget()`, which uses
   `asyncio.eager_task_factory` (Python 3.12+) so fast dispatch paths
   complete immediately while slow ones suspend to background. Falls
   back to plain `create_task` on Python 3.11.
4. The caller is never blocked.

**Channel extraction:** `evaluate_alerts` parses the channel from
control plane's `webhook:test` format (the control plane stores channel
as `webhook:test`), extracting just the adapter key before routing.

## Extending with Custom Adapters

Implement the `ChannelAdapter` base class:

```python
from llm_budget_gateway.dispatch_engine import (
    AlertDispatcher,
    ChannelAdapter,
    EmailDispatcher,
    SlackDispatcher,
    TelegramDispatcher,
    WebhookDispatcher,
)
from llm_budget_gateway.alert_models import AlertEvent

class PagerDutyAdapter(ChannelAdapter):
    def __init__(self, routing_key: str, client=None):
        self.routing_key = routing_key
        self.client = client

    async def dispatch(self, event: AlertEvent) -> bool:
        """Post an incident to PagerDuty Events API v2."""
        # ... implement your adapter logic ...
        return True

# Register with the dispatcher
dispatcher = AlertDispatcher(adapters={
    "webhook": WebhookDispatcher(url="...", secret="..."),
    "slack": SlackDispatcher(bot_token="...", channel="..."),
    "telegram": TelegramDispatcher(bot_token="...", chat_id="..."),
    "email": EmailDispatcher(host="..."),
    "pagerduty": PagerDutyAdapter(routing_key="..."),
})
```

The dispatcher routes by `event.channel` — any string key works as long
as an adapter is registered for it.
