"""Pre-development tests for alert rule and event data models.

Interface tests (imports, signatures, model fields): pass immediately.
Behavioral tests (instantiation, serialization, validation): RED until the
developer implements ``src/llm_budget_gateway/alert_models.py`` with
Pydantic v2 models (analysis brief P1-6, §5.13 of the planning spec).

Contract:
- ``AlertRule``: Pydantic model fields — name, threshold (0.0–1.0),
  channel (enum: webhook|slack|telegram|email), config (dict),
  cooldown_seconds (default 300), enabled (default True).
- ``AlertEvent``: alert_rule_id, scope, current_spend, threshold,
  triggered_at.
- ``AlertDispatchLog``: alert_rule_id, channel, delivery_status
  (delivered|failed|pending), response_code, error_message, dispatched_at.
- ``AlertChannel``: Enum or Literal with webhook, slack, telegram, email.
"""

from __future__ import annotations

import inspect

import pytest

# ============================================================
# Helpers — resolve channel class (AlertChannel / ChannelType / Channel)
# ============================================================


def _resolve_channel_class():
    """Resolve channel enum from alert_models (covers naming variants)."""
    from llm_budget_gateway import alert_models

    cls = (
        getattr(alert_models, "AlertChannel", None)
        or getattr(alert_models, "ChannelType", None)
        or getattr(alert_models, "Channel", None)
    )
    return cls


def _webhook_channel():
    """Return the webhook value from whatever channel class exists."""
    cls = _resolve_channel_class()
    if cls is None:
        return "webhook"
    return cls.webhook if hasattr(cls, "webhook") else "webhook"


# ============================================================
# Interface tests — imports, signatures, type guards
# ============================================================


class TestAlertModelsInterface:
    """Verify that the models module exists and exposes the expected symbols."""

    def test_alert_models_module_importable(self):
        from llm_budget_gateway import alert_models  # noqa: F401

    def test_alert_rule_class_exists(self):
        from llm_budget_gateway.alert_models import AlertRule

        assert AlertRule is not None

    def test_alert_event_class_exists(self):
        from llm_budget_gateway.alert_models import AlertEvent

        assert AlertEvent is not None

    def test_alert_dispatch_log_class_exists(self):
        from llm_budget_gateway.alert_models import AlertDispatchLog

        assert AlertDispatchLog is not None

    def test_alert_channel_enum_or_literal_exists(self):
        """Channel values must be an Enum or Literal — either is acceptable."""
        channel_cls = _resolve_channel_class()
        assert channel_cls is not None, (
            "alert_models must expose a channel enum "
            "(AlertChannel, ChannelType, or Channel)"
        )

    def test_alert_rule_instantiable_with_defaults(self):
        from llm_budget_gateway.alert_models import AlertRule

        channel = _webhook_channel()
        rule = AlertRule(
            name="test",
            threshold=0.8,
            channel=channel,
            config={"url": "https://example.com"},
        )
        assert rule is not None

    def test_alert_event_instantiable(self):
        from llm_budget_gateway.alert_models import AlertEvent

        event = AlertEvent(
            alert_rule_id="r1",
            scope="global",
            current_spend=0.95,
            threshold=0.8,
            triggered_at="2026-08-09T12:00:00Z",
        )
        assert event is not None

    def test_alert_dispatch_log_instantiable(self):
        from llm_budget_gateway.alert_models import AlertDispatchLog

        log = AlertDispatchLog(
            alert_rule_id="r1",
            channel="webhook",
            delivery_status="delivered",
            response_code=200,
            error_message=None,
            dispatched_at="2026-08-09T12:00:01Z",
        )
        assert log is not None

    def test_alert_rule_has_expected_fields(self):
        """Verify all required field names exist on the model."""
        from llm_budget_gateway.alert_models import AlertRule

        sig = inspect.signature(AlertRule)
        params = set(sig.parameters.keys())
        for field in (
            "name",
            "threshold",
            "channel",
            "config",
            "cooldown_seconds",
            "enabled",
        ):
            assert field in params, f"AlertRule missing field: {field}"

    def test_alert_event_has_expected_fields(self):
        from llm_budget_gateway.alert_models import AlertEvent

        sig = inspect.signature(AlertEvent)
        params = set(sig.parameters.keys())
        for field in (
            "alert_rule_id",
            "scope",
            "current_spend",
            "threshold",
            "triggered_at",
        ):
            assert field in params, f"AlertEvent missing field: {field}"

    def test_alert_dispatch_log_has_expected_fields(self):
        from llm_budget_gateway.alert_models import AlertDispatchLog

        sig = inspect.signature(AlertDispatchLog)
        params = set(sig.parameters.keys())
        for field in (
            "alert_rule_id",
            "channel",
            "delivery_status",
            "response_code",
            "error_message",
            "dispatched_at",
        ):
            assert field in params, f"AlertDispatchLog missing field: {field}"

    def test_alert_rule_cooldown_seconds_default(self):
        """cooldown_seconds should default to 300 (5 min)."""
        from llm_budget_gateway.alert_models import AlertRule

        channel = _webhook_channel()
        rule = AlertRule(name="x", threshold=0.5, channel=channel, config={})
        assert getattr(rule, "cooldown_seconds", None) == 300

    def test_alert_rule_enabled_default(self):
        """enabled should default to True."""
        from llm_budget_gateway.alert_models import AlertRule

        channel = _webhook_channel()
        rule = AlertRule(name="x", threshold=0.5, channel=channel, config={})
        assert getattr(rule, "enabled", None) is True


# ============================================================
# Behavioral — validation (RED phase)
# ============================================================


class TestAlertRuleValidation:
    """Validation rules that enforce model constraints."""

    def test_threshold_must_be_between_0_and_1(self):
        from llm_budget_gateway.alert_models import AlertRule

        with pytest.raises((ValueError, Exception)):
            AlertRule(
                name="bad",
                threshold=1.5,
                channel=_webhook_channel(),
                config={},
            )

    def test_threshold_zero_is_valid(self):
        from llm_budget_gateway.alert_models import AlertRule

        rule = AlertRule(
            name="zero",
            threshold=0.0,
            channel=_webhook_channel(),
            config={},
        )
        assert rule.threshold == 0.0

    def test_threshold_one_is_valid(self):
        from llm_budget_gateway.alert_models import AlertRule

        rule = AlertRule(
            name="one",
            threshold=1.0,
            channel=_webhook_channel(),
            config={},
        )
        assert rule.threshold == 1.0

    def test_negative_threshold_rejected(self):
        from llm_budget_gateway.alert_models import AlertRule

        with pytest.raises((ValueError, Exception)):
            AlertRule(
                name="neg",
                threshold=-0.1,
                channel=_webhook_channel(),
                config={},
            )

    def test_invalid_channel_rejected(self):
        from llm_budget_gateway.alert_models import AlertRule

        with pytest.raises((ValueError, Exception)):
            AlertRule(
                name="bad",
                threshold=0.5,
                channel="carrier_pigeon",
                config={},
            )

    def test_name_cannot_be_empty(self):
        from llm_budget_gateway.alert_models import AlertRule

        with pytest.raises((ValueError, Exception)):
            AlertRule(
                name="",
                threshold=0.5,
                channel=_webhook_channel(),
                config={},
            )


# ============================================================
# Behavioral — serialization (RED phase)
# ============================================================


class TestAlertRuleSerialization:
    """Round-trip dict serialization for AlertRule."""

    def test_dict_roundtrip(self):
        from llm_budget_gateway.alert_models import AlertRule

        rule = AlertRule(
            name="team-budget",
            threshold=0.8,
            channel=_webhook_channel(),
            config={"url": "https://hook.example.com"},
            cooldown_seconds=600,
            enabled=False,
        )
        if hasattr(rule, "model_dump"):
            d = rule.model_dump()
        elif hasattr(rule, "dict"):
            d = rule.dict()
        else:
            d = rule.__dict__

        assert d["name"] == "team-budget"
        assert d["threshold"] == 0.8
        assert d["cooldown_seconds"] == 600
        assert d["enabled"] is False
        assert d["config"]["url"] == "https://hook.example.com"

    def test_json_roundtrip(self):
        from llm_budget_gateway.alert_models import AlertRule

        rule = AlertRule(
            name="json-test",
            threshold=0.5,
            channel=_webhook_channel(),
            config={"url": "https://example.com"},
        )
        if hasattr(rule, "model_dump_json"):
            j = rule.model_dump_json()
            if hasattr(AlertRule, "model_validate_json"):
                restored = AlertRule.model_validate_json(j)
                assert restored.name == "json-test"
                assert restored.threshold == 0.5
            else:
                import json

                d = json.loads(j)
                assert d["name"] == "json-test"
        else:
            import json

            if hasattr(rule, "dict"):
                j = json.dumps(rule.dict())
            else:
                j = json.dumps(rule.__dict__)
            d = json.loads(j)
            assert d["name"] == "json-test"


# ============================================================
# Behavioral — AlertEvent validation (RED phase)
# ============================================================


class TestAlertEventValidation:
    def test_scope_defaults_to_global(self):
        """scope is optional and defaults to 'global' (dispatch contract)."""
        from llm_budget_gateway.alert_models import AlertEvent

        event = AlertEvent(
            alert_rule_id="r1",
            current_spend=0.9,
            threshold=0.8,
            triggered_at="2026-08-09T12:00:00Z",
        )
        assert event.scope == "global"

    def test_negative_current_spend_rejected(self):
        from llm_budget_gateway.alert_models import AlertEvent

        with pytest.raises((ValueError, Exception)):
            AlertEvent(
                alert_rule_id="r1",
                scope="global",
                current_spend=-0.1,
                threshold=0.8,
                triggered_at="2026-08-09T12:00:00Z",
            )

    def test_current_spend_can_be_zero(self):
        from llm_budget_gateway.alert_models import AlertEvent

        event = AlertEvent(
            alert_rule_id="r1",
            scope="global",
            current_spend=0.0,
            threshold=0.8,
            triggered_at="2026-08-09T12:00:00Z",
        )
        assert event.current_spend == 0.0


# ============================================================
# Behavioral — AlertDispatchLog validation (RED phase)
# ============================================================


class TestAlertDispatchLogValidation:
    def test_delivery_status_must_be_valid(self):
        from llm_budget_gateway.alert_models import AlertDispatchLog

        with pytest.raises((ValueError, Exception)):
            AlertDispatchLog(
                alert_rule_id="r1",
                channel="webhook",
                delivery_status="maybe",
                response_code=None,
                error_message=None,
                dispatched_at="2026-08-09T12:00:01Z",
            )

    def test_delivered_status_accepted(self):
        from llm_budget_gateway.alert_models import AlertDispatchLog

        log = AlertDispatchLog(
            alert_rule_id="r1",
            channel="webhook",
            delivery_status="delivered",
            response_code=200,
            error_message=None,
            dispatched_at="2026-08-09T12:00:01Z",
        )
        assert log.delivery_status == "delivered"

    def test_pending_status_accepted(self):
        from llm_budget_gateway.alert_models import AlertDispatchLog

        log = AlertDispatchLog(
            alert_rule_id="r1",
            channel="slack",
            delivery_status="pending",
            response_code=None,
            error_message=None,
            dispatched_at="2026-08-09T12:00:01Z",
        )
        assert log.delivery_status == "pending"

    def test_failed_status_accepted(self):
        from llm_budget_gateway.alert_models import AlertDispatchLog

        log = AlertDispatchLog(
            alert_rule_id="r1",
            channel="telegram",
            delivery_status="failed",
            response_code=500,
            error_message="timeout",
            dispatched_at="2026-08-09T12:00:01Z",
        )
        assert log.delivery_status == "failed"
