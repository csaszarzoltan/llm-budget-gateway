"""Data models for the budget alert notification dispatch feature.

Pre-development stub: Pydantic v2 models with correct fields and enum so
interface tests pass for BOTH pre-tester files (test_alert_models.py and
test_dispatch_engine.py). Behavioral validation logic is left to the
developer.
"""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class AlertChannel(StrEnum):
    """Supported notification channels."""

    webhook = "webhook"
    slack = "slack"
    telegram = "telegram"
    email = "email"


# Alias for dispatch engine tests
ChannelType = AlertChannel


class AlertRule(BaseModel):
    """A budget alert rule: when to fire and where to notify."""

    id: str = ""
    name: str = Field(min_length=1)
    threshold: float = Field(ge=0.0, le=1.0)
    channel: AlertChannel
    config: dict = Field(default_factory=dict)
    # ge=0: a negative cooldown would silently disable dedup in the
    # dispatch engine (``if cooldown_seconds > 0``). 0 is valid and
    # documents "dedup disabled".
    cooldown_seconds: int = Field(default=300, ge=0)
    enabled: bool = True
    state: str = "ready"


class AlertEvent(BaseModel):
    """A triggered alert event dispatched to a channel adapter.

    ``channel`` defaults to "webhook" so callers that create events without
    it (e.g. pure threshold tests) still work; the dispatch engine always
    sets it explicitly before routing.
    ``triggered_at`` accepts both ISO-8601 strings (from API responses) and
    floats (from dispatch engine internal use).
    """

    alert_rule_id: str
    channel: str = "webhook"
    scope: str = "global"
    current_spend: float = Field(ge=0.0)
    threshold: float = Field(ge=0.0)
    triggered_at: str | float | None = Field(default_factory=time.time)


class AlertDispatchLog(BaseModel):
    """Immutable log entry for every dispatch attempt (success or failure)."""

    alert_rule_id: str
    channel: str
    delivery_status: Literal["delivered", "failed", "pending"]
    response_code: int | None = None
    error_message: str | None = None
    dispatched_at: str | float | None = Field(default_factory=time.time)
