"""Alert dispatch engine: routes triggered alerts to channel adapters.

Pre-development stub: the public interface is complete and constructible so
interface tests pass immediately; every behavioral method raises
``NotImplementedError`` until the developer implements it (TDD RED phase).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .alert_models import AlertEvent

# Default cooldown window for dedup: 5 minutes.
DEFAULT_COOLDOWN_SECONDS = 300


class ChannelAdapter:
    """Base class for notification channel adapters."""

    async def dispatch(self, event: AlertEvent) -> bool:
        """Dispatch an alert event via this channel.  Returns True on success."""
        raise NotImplementedError  # pragma: no cover


class WebhookDispatcher(ChannelAdapter):
    """POST a HMAC-SHA256 signed envelope to a webhook URL."""

    def __init__(self, url: str, secret: str, client: object = None, clock=None):
        self.url = url
        self.secret = secret
        self.client = client
        self.clock = clock or time.time

    async def dispatch(self, event: AlertEvent) -> bool:
        raise NotImplementedError  # pragma: no cover


class SlackDispatcher(ChannelAdapter):
    """Post to Slack via the chat.postMessage API."""

    def __init__(self, bot_token: str, channel: str, client: object = None):
        self.bot_token = bot_token
        self.channel = channel
        self.client = client

    async def dispatch(self, event: AlertEvent) -> bool:
        raise NotImplementedError  # pragma: no cover


class TelegramDispatcher(ChannelAdapter):
    """Post to Telegram via the bot sendMessage API."""

    def __init__(self, bot_token: str, chat_id: str, client: object = None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.client = client

    async def dispatch(self, event: AlertEvent) -> bool:
        raise NotImplementedError  # pragma: no cover


class EmailDispatcher(ChannelAdapter):
    """Send email via SMTP."""

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
        raise NotImplementedError  # pragma: no cover


class AlertDispatcher:
    """Routes AlertEvent to the correct channel adapter with cooldown and retry."""

    # Class-level default so tests can assert AlertDispatcher.cooldown_seconds.
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS

    def __init__(
        self,
        adapters: dict[str, ChannelAdapter] | None = None,
        retries: int = 3,
        backoff_base: float = 1.0,
        cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
        clock=None,
    ):
        self.adapters = adapters or {
            "webhook": WebhookDispatcher(url="", secret=""),
            "slack": SlackDispatcher(bot_token="", channel=""),
            "telegram": TelegramDispatcher(bot_token="", chat_id=""),
            "email": EmailDispatcher(host=""),
        }
        self.retries = retries
        self.backoff_base = backoff_base
        self.clock = clock or time.time
        self._last_fired: dict[str, float] = {}

    async def dispatch(self, event: AlertEvent) -> bool:
        raise NotImplementedError  # pragma: no cover
