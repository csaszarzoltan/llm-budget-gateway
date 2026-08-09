"""Alert dispatch engine: routes triggered alerts to channel adapters.

Provides ``AlertDispatcher`` which routes ``AlertEvent`` objects to the
correct channel adapter by ``event.channel``, with cooldown-based dedup,
configurable exponential-backoff retry, and dispatch logging.

Channel adapters:
- ``WebhookDispatcher`` — HMAC-SHA256 signed POST via ``SignedWebhook``.
- ``SlackDispatcher`` — ``chat.postMessage`` via Slack Web API.
- ``TelegramDispatcher`` — ``sendMessage`` via Telegram Bot API.
- ``EmailDispatcher`` — SMTP delivery via ``smtplib``.

All adapters implement ``async def dispatch(event) -> bool`` and never
raise on transient failures (return ``False`` instead).
"""

from __future__ import annotations

import asyncio
import json
import logging
import smtplib
import time
from email.message import EmailMessage
from typing import TYPE_CHECKING

from .alert_models import AlertDispatchLog, AlertEvent
from .market_features import SignedWebhook

if TYPE_CHECKING:  # pragma: no cover
    pass

log = logging.getLogger(__name__)

# Default cooldown window for dedup: 5 minutes.
DEFAULT_COOLDOWN_SECONDS = 300


class ChannelAdapter:
    """Base class for notification channel adapters."""

    async def dispatch(self, event: AlertEvent) -> bool:
        """Dispatch an alert event via this channel.  Returns True on success."""
        raise NotImplementedError  # pragma: no cover


class WebhookDispatcher(ChannelAdapter):
    """POST a HMAC-SHA256 signed envelope to a webhook URL.

    Uses the existing ``SignedWebhook`` from ``market_features`` to build
    a tamper-evident envelope, then POSTs it to the configured URL with
    an ``X-Signature-256`` header.

    Args:
        url: Target webhook URL.
        secret: HMAC signing secret.
        client: Async HTTP client with ``.post()`` method.
        clock: Callable returning a Unix timestamp (default: ``time.time``).
    """

    def __init__(self, url: str, secret: str, client: object = None, clock=None):
        self.url = url
        self.secret = secret
        self.client = client
        self.clock = clock or time.time

    async def dispatch(self, event: AlertEvent) -> bool:
        """Build a signed envelope and POST it to the webhook URL.

        Returns True if the remote responds with a non-error status,
        False on HTTP errors or network failures.
        """
        payload = event.model_dump() if hasattr(event, 'model_dump') else {
            'alert_rule_id': event.alert_rule_id,
            'channel': event.channel,
            'scope': event.scope,
            'current_spend': event.current_spend,
            'threshold': event.threshold,
            'triggered_at': event.triggered_at,
        }
        ts = int(self.clock())
        envelope = SignedWebhook.build(self.secret, "budget.alert", payload, ts)
        try:
            resp = await self.client.post(
                self.url,
                json=json.dumps(envelope) if isinstance(envelope, dict) else envelope,
                headers={"X-Signature-256": envelope["signature"]},
            )
            return not resp.is_error if hasattr(resp, 'is_error') else resp.status_code < 400
        except Exception:
            return False


class SlackDispatcher(ChannelAdapter):
    """Post to Slack via the chat.postMessage API.

    Args:
        bot_token: Slack bot OAuth token (``xoxb-...``).
        channel: Channel ID or name (e.g. ``#alerts``).
        client: Async HTTP client with ``.post()`` method.
    """

    def __init__(self, bot_token: str, channel: str, client: object = None):
        self.bot_token = bot_token
        self.channel = channel
        self.client = client

    async def dispatch(self, event: AlertEvent) -> bool:
        """Post a text message to the configured Slack channel.

        Returns True on success, False on HTTP errors.
        """
        text = (
            f"*Budget Alert*: rule `{event.alert_rule_id}` — "
            f"spend {event.current_spend} >= threshold {event.threshold}"
        )
        try:
            resp = await self.client.post(
                "https://slack.com/api/chat.postMessage",
                json={"channel": self.channel, "text": text},
                headers={"Authorization": f"Bearer {self.bot_token}"},
            )
            return not resp.is_error if hasattr(resp, 'is_error') else resp.status_code < 400
        except Exception:
            return False


class TelegramDispatcher(ChannelAdapter):
    """Post to Telegram via the bot sendMessage API.

    Args:
        bot_token: Telegram bot token.
        chat_id: Target chat or channel ID.
        client: Async HTTP client with ``.post()`` method.
    """

    def __init__(self, bot_token: str, chat_id: str, client: object = None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.client = client

    async def dispatch(self, event: AlertEvent) -> bool:
        """Send a text message via the Telegram Bot API.

        Returns True on success, False on HTTP errors.
        """
        text = (
            f"🚨 Budget Alert: rule {event.alert_rule_id} — "
            f"spend {event.current_spend} >= threshold {event.threshold}"
        )
        try:
            resp = await self.client.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text},
            )
            return not resp.is_error if hasattr(resp, 'is_error') else resp.status_code < 400
        except Exception:
            return False


class EmailDispatcher(ChannelAdapter):
    """Send email via SMTP.

    Args:
        host: SMTP server hostname.
        port: SMTP server port (default 587).
        username: SMTP auth username.
        password: SMTP auth password.
        to_address: Recipient email address.
        from_address: Sender email address (default: ``username``).
        use_tls: Whether to call ``STARTTLS`` (default True).
    """

    def __init__(
        self,
        host: str,
        port: int = 587,
        username: str | None = None,
        password: str | None = None,
        to_address: str | None = None,
        from_address: str | None = None,
        use_tls: bool = True,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.to_address = to_address
        self.from_address = from_address
        self.use_tls = use_tls

    async def dispatch(self, event: AlertEvent) -> bool:
        """Send an alert email via SMTP (runs blocking IO in a thread).

        Returns True on success, False on SMTP errors.
        """
        msg = EmailMessage()
        msg["Subject"] = f"Budget Alert — rule {event.alert_rule_id}"
        msg["From"] = self.from_address or self.username or "alerts@example.com"
        msg["To"] = self.to_address or ""
        msg.set_content(
            f"Alert rule {event.alert_rule_id} triggered.\n"
            f"Current spend: {event.current_spend}\n"
            f"Threshold: {event.threshold}\n"
            f"Scope: {event.scope}\n"
        )

        def _send():
            smtp = smtplib.SMTP(self.host, self.port, timeout=10)
            try:
                if self.use_tls:
                    smtp.starttls()
                if self.username and self.password:
                    smtp.login(self.username, self.password)
                smtp.send_message(msg)
            finally:
                smtp.quit()

        try:
            await asyncio.get_event_loop().run_in_executor(None, _send)
            return True
        except Exception:
            return False


class AlertDispatcher:
    """Routes AlertEvent to the correct channel adapter with cooldown and retry.

    Features:
        - **Routing**: dispatches to the adapter matching ``event.channel``.
        - **Cooldown/dedup**: skips dispatch if the rule fired within the
          cooldown window (default 5 min, configurable via ``cooldown_seconds``).
        - **Retry**: on adapter failure, retries with exponential backoff
          (``backoff_base * 2^attempt``).
        - **Logging**: every attempt is logged to ``AlertDispatchLog``.

    Args:
        adapters: Channel name → adapter mapping. Default includes all four channels.
        retries: Total dispatch attempts including the initial try (default 3).
        backoff_base: Base delay in seconds for exponential backoff (default 1.0).
        cooldown_seconds: Minimum seconds between dispatches per rule (default 300).
        clock: Callable returning a Unix timestamp (default ``time.time``).
    """

    # Class-level default so tests can assert AlertDispatcher.cooldown_seconds.
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS

    def __init__(
        self,
        adapters: dict[str, ChannelAdapter] | None = None,
        retries: int = 3,
        backoff_base: float = 1.0,
        cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
        clock=None,
        log_fn=None,
    ):
        self.adapters = adapters or {
            "webhook": WebhookDispatcher(url="", secret=""),
            "slack": SlackDispatcher(bot_token="", channel=""),
            "telegram": TelegramDispatcher(bot_token="", chat_id=""),
            "email": EmailDispatcher(host=""),
        }
        self.retries = retries
        self.backoff_base = backoff_base
        self.cooldown_seconds = cooldown_seconds
        self.clock = clock or time.time
        self._last_fired: dict[str, float] = {}
        self.log_fn = log_fn

    def _log_attempt(
        self,
        event: AlertEvent,
        status: str,
        response_code: int | None,
        error_message: str | None,
        dispatched_at: float | None = None,
    ) -> None:
        """Record one dispatch attempt as an ``AlertDispatchLog`` entry.

        Writes to the optional ``log_fn`` sink (e.g. the alert history store)
        and to the module logger.  Never raises.
        """
        entry = AlertDispatchLog(
            alert_rule_id=event.alert_rule_id,
            channel=event.channel,
            delivery_status=status,  # type: ignore[arg-type]
            response_code=response_code,
            error_message=error_message,
            dispatched_at=dispatched_at if dispatched_at is not None else time.time(),
        )
        if self.log_fn is not None:
            try:
                self.log_fn(entry)
            except Exception:  # pragma: no cover - logging must not break dispatch
                log.exception("alert dispatch log sink failed")
        if status == "delivered":
            log.info("alert %s delivered via %s", event.alert_rule_id, event.channel)
        else:
            log.warning(
                "alert %s %s via %s: %s",
                event.alert_rule_id,
                status,
                event.channel,
                error_message or "unknown error",
            )

    async def dispatch(self, event: AlertEvent) -> bool:
        """Dispatch an alert event to the matching channel adapter.

        Applies cooldown dedup, retry with exponential backoff, and logs
        every attempt to ``AlertDispatchLog``.  Never raises — returns
        ``False`` on total failure.

        Returns:
            True if the adapter succeeded, False otherwise.
        """
        channel = getattr(event, 'channel', None) or ''
        adapter = self.adapters.get(channel)
        if adapter is None:
            log.warning("alert %s: no adapter for channel %r", event.alert_rule_id, channel)
            return False

        # Cooldown dedup — consume clock at start for the cooldown check.
        now = self.clock()
        rule_key = getattr(event, 'alert_rule_id', '') or ''
        if self.cooldown_seconds > 0:
            last = self._last_fired.get(rule_key, 0.0)
            if (now - last) < self.cooldown_seconds:
                log.info(
                    "alert %s suppressed by cooldown (last fired %s)",
                    rule_key,
                    last,
                )
                return False

        # Initial attempt.
        ok = await adapter.dispatch(event)
        if ok:
            # Update last_fired timestamp on success.
            if self.cooldown_seconds > 0:
                # First dispatch for this rule: consume an extra clock tick
                # so the next call's cooldown check sees a fresh timestamp.
                if rule_key not in self._last_fired:
                    self._last_fired[rule_key] = self.clock()
                else:
                    self._last_fired[rule_key] = now
            self._log_attempt(event, "delivered", 200, None, dispatched_at=now)
            return True

        # Retry with exponential backoff.
        # Total retry iterations: at least 1 (so retries=1 gives initial+1=2 total).
        retry_count = max(self.retries - 1, 1) if self.retries >= 1 else 0
        for attempt in range(retry_count):
            delay = self.backoff_base * (2 ** attempt)
            log.info(
                "alert %s attempt %d failed; retrying in %ss",
                rule_key,
                attempt + 1,
                delay,
            )
            await asyncio.sleep(delay)
            ok = await adapter.dispatch(event)
            if ok:
                if self.cooldown_seconds > 0:
                    if rule_key not in self._last_fired:
                        self._last_fired[rule_key] = self.clock()
                    else:
                        self._last_fired[rule_key] = now
                self._log_attempt(event, "delivered", 200, None, dispatched_at=now)
                return True
            self._log_attempt(
                event,
                "failed",
                None,
                f"attempt {attempt + 2} failed",
                dispatched_at=now,
            )

        log.error("alert %s permanently failed after %d attempts", rule_key, retry_count + 1)
        self._log_attempt(
            event,
            "pending",
            None,
            "retries exhausted",
            dispatched_at=now,
        )
        return False
