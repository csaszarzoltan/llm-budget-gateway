"""Pre-development behavioral tests for the alert dispatch engine.

Covers the Budget Alert Notification Dispatch feature (parent t_407986f6):

1. AlertDispatcher routes AlertEvent to the correct channel adapter by
   ``event.channel``, returns True/False, fires asynchronously.
2. Channel adapters (WebhookDispatcher/SlackDispatcher/TelegramDispatcher/
   EmailDispatcher) implement ``async def dispatch(event: AlertEvent) -> bool``.
3. Cooldown/dedup: an alert rule does not fire more than once per cooldown
   window (default 5 min, configurable); it fires again after expiry.
4. Retry with exponential backoff: failed dispatches retry up to 3 times,
   backoff grows exponentially, alert stays "pending" after 3 failures.
5. Integration: ``evaluate_alerts()`` triggers async (non-blocking) dispatch.

These are behavioral tests asserting EXPECTED behavior — they call real
methods and assert real outcomes. External HTTP/SMTP calls are mocked with
``unittest.mock`` (repo convention; no pytest-httpx/respx dependency). The
webhook signature is verified against the EXISTING ``SignedWebhook`` from
``llm_budget_gateway.market_features``.

Contracts encoded here (normative for the developer, t_95778f6b):
- ``AlertDispatcher(adapters=None, retries=3, backoff_base=1.0,
  clock=time.time)``; ``dispatch(event)`` returns bool and never raises on
  adapter failure (False + retry path instead).
- ``ChannelAdapter`` base exposes ``async def dispatch(event) -> bool``.
- ``WebhookDispatcher(url, secret, client=None, clock=time.time)`` POSTs the
  signed envelope to ``url`` with header ``X-Signature-256``.
- ``SlackDispatcher(bot_token, channel, client=None)`` POSTs
  ``{"text": ...}`` to ``https://slack.com/api/chat.postMessage`` with
  ``Authorization: Bearer``.
- ``TelegramDispatcher(bot_token, chat_id, client=None)`` POSTs
  ``{"chat_id": ..., "text": ...}`` to
  ``https://api.telegram.org/bot<token>/sendMessage``.
- ``EmailDispatcher(host, port=587, username=None, password=None,
  to_address=None, from_address=None, use_tls=True)`` SMTP-delivers via
  smtplib; returns True/False.
- ``evaluate_alerts(tenant, dispatch=None, clock=None)`` — new signature that
  dispatches triggered alerts asynchronously via ``asyncio.create_task`` when
  ``dispatch`` is provided (never blocks the caller).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import json
import smtplib
from unittest import mock

import pytest
from llm_budget_gateway.alert_models import (
    AlertDispatchLog,
    AlertEvent,
    AlertRule,
    ChannelType,
)
from llm_budget_gateway.dispatch_engine import (
    AlertDispatcher,
    ChannelAdapter,
    EmailDispatcher,
    SlackDispatcher,
    TelegramDispatcher,
    WebhookDispatcher,
)

from llm_budget_gateway.control_plane import ControlPlane
from llm_budget_gateway.market_features import SignedWebhook


def make_event(**overrides) -> AlertEvent:
    """Build a valid AlertEvent; overrides win over the default payload."""
    base = {
        "alert_rule_id": "rule-1",
        "channel": "webhook",
        "scope": "global",
        "current_spend": 95.0,
        "threshold": 80.0,
        "triggered_at": 1000.0,
    }
    base.update(overrides)
    return AlertEvent(**base)


# --------------------------------------------------------------------------
# Interface tests — module imports, signatures, async-ness. These must pass
# once the module exists; they pin the contract the developer implements to.
# --------------------------------------------------------------------------


class TestDispatchEngineInterface:
    def test_module_importable(self) -> None:
        import llm_budget_gateway.dispatch_engine  # noqa: F401

    def test_public_symbols_exist(self) -> None:
        assert AlertDispatcher is not None
        assert ChannelAdapter is not None
        assert WebhookDispatcher is not None
        assert SlackDispatcher is not None
        assert TelegramDispatcher is not None
        assert EmailDispatcher is not None

    def test_all_adapters_are_async_dispatch(self) -> None:
        for cls in (
            ChannelAdapter,
            WebhookDispatcher,
            SlackDispatcher,
            TelegramDispatcher,
            EmailDispatcher,
        ):
            assert inspect.iscoroutinefunction(cls.dispatch), cls.__name__

    def test_adapter_dispatch_returns_bool_annotation(self) -> None:
        sig = inspect.signature(ChannelAdapter.dispatch)
        assert list(sig.parameters) == ["self", "event"]
        assert "bool" in str(sig.return_annotation)

    def test_alert_dispatcher_init_signature(self) -> None:
        sig = inspect.signature(AlertDispatcher.__init__)
        assert "adapters" in sig.parameters
        assert sig.parameters["adapters"].default is None
        assert "retries" in sig.parameters
        assert sig.parameters["retries"].default == 3
        assert "backoff_base" in sig.parameters
        assert "clock" in sig.parameters

    def test_alert_dispatcher_dispatch_signature(self) -> None:
        sig = inspect.signature(AlertDispatcher.dispatch)
        assert list(sig.parameters) == ["self", "event"]
        assert "bool" in str(sig.return_annotation)

    def test_alert_dispatcher_has_async_dispatch(self) -> None:
        assert inspect.iscoroutinefunction(AlertDispatcher.dispatch)

    def test_webhook_dispatcher_init_signature(self) -> None:
        sig = inspect.signature(WebhookDispatcher.__init__)
        params = list(sig.parameters)
        assert params[1] == "url"
        assert "secret" in params

    def test_slack_dispatcher_init_signature(self) -> None:
        sig = inspect.signature(SlackDispatcher.__init__)
        assert "bot_token" in sig.parameters
        assert "channel" in sig.parameters

    def test_telegram_dispatcher_init_signature(self) -> None:
        sig = inspect.signature(TelegramDispatcher.__init__)
        assert "bot_token" in sig.parameters
        assert "chat_id" in sig.parameters

    def test_email_dispatcher_init_signature(self) -> None:
        sig = inspect.signature(EmailDispatcher.__init__)
        params = list(sig.parameters)
        assert params[1] == "host"
        assert "port" in params
        assert "username" in params
        assert "password" in params
        assert "to_address" in params

    def test_event_model_has_expected_fields(self) -> None:
        assert "alert_rule_id" in AlertEvent.model_fields
        assert "channel" in AlertEvent.model_fields
        assert "scope" in AlertEvent.model_fields
        assert "current_spend" in AlertEvent.model_fields
        assert "threshold" in AlertEvent.model_fields
        assert "triggered_at" in AlertEvent.model_fields

    def test_rule_model_has_expected_fields(self) -> None:
        assert "name" in AlertRule.model_fields
        assert "threshold" in AlertRule.model_fields
        assert "channel" in AlertRule.model_fields
        assert "config" in AlertRule.model_fields
        assert "cooldown_seconds" in AlertRule.model_fields
        assert "enabled" in AlertRule.model_fields

    def test_rule_default_cooldown_is_five_minutes(self) -> None:
        assert AlertRule.model_fields["cooldown_seconds"].default == 300

    def test_channel_type_enum_values(self) -> None:
        assert {c.value for c in ChannelType} == {
            "webhook",
            "slack",
            "telegram",
            "email",
        }

    def test_dispatch_log_model_has_expected_fields(self) -> None:
        assert "alert_rule_id" in AlertDispatchLog.model_fields
        assert "channel" in AlertDispatchLog.model_fields
        assert "delivery_status" in AlertDispatchLog.model_fields
        assert "response_code" in AlertDispatchLog.model_fields
        assert "error_message" in AlertDispatchLog.model_fields
        assert "dispatched_at" in AlertDispatchLog.model_fields


# --------------------------------------------------------------------------
# AlertDispatcher routing
# --------------------------------------------------------------------------


class TestAlertDispatcherRouting:
    @pytest.mark.asyncio
    async def test_routes_webhook_event_to_webhook_adapter(self) -> None:
        adapter = mock.Mock(dispatch=mock.AsyncMock(return_value=True))
        dispatcher = AlertDispatcher(adapters={"webhook": adapter})
        ok = await dispatcher.dispatch(make_event(channel="webhook"))
        assert ok is True
        adapter.dispatch.assert_awaited_once()
        assert adapter.dispatch.await_args.args[0].channel == "webhook"

    @pytest.mark.asyncio
    async def test_routes_slack_event_to_slack_adapter(self) -> None:
        adapter = mock.Mock(dispatch=mock.AsyncMock(return_value=True))
        dispatcher = AlertDispatcher(adapters={"slack": adapter})
        ok = await dispatcher.dispatch(make_event(channel="slack"))
        assert ok is True
        adapter.dispatch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_routes_telegram_event_to_telegram_adapter(self) -> None:
        adapter = mock.Mock(dispatch=mock.AsyncMock(return_value=True))
        dispatcher = AlertDispatcher(adapters={"telegram": adapter})
        ok = await dispatcher.dispatch(make_event(channel="telegram"))
        assert ok is True
        adapter.dispatch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_routes_email_event_to_email_adapter(self) -> None:
        adapter = mock.Mock(dispatch=mock.AsyncMock(return_value=True))
        dispatcher = AlertDispatcher(adapters={"email": adapter})
        ok = await dispatcher.dispatch(make_event(channel="email"))
        assert ok is True
        adapter.dispatch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_true_on_successful_dispatch(self) -> None:
        adapter = mock.Mock(dispatch=mock.AsyncMock(return_value=True))
        dispatcher = AlertDispatcher(adapters={"webhook": adapter})
        assert await dispatcher.dispatch(make_event()) is True

    @pytest.mark.asyncio
    async def test_returns_false_when_adapter_fails(self) -> None:
        adapter = mock.Mock(dispatch=mock.AsyncMock(return_value=False))
        dispatcher = AlertDispatcher(adapters={"webhook": adapter})
        assert await dispatcher.dispatch(make_event()) is False

    @pytest.mark.asyncio
    async def test_unknown_channel_returns_false(self) -> None:
        dispatcher = AlertDispatcher(adapters={})
        ok = await dispatcher.dispatch(make_event(channel="pigeon"))
        assert ok is False

    @pytest.mark.asyncio
    async def test_default_registry_has_all_four_channels(self) -> None:
        dispatcher = AlertDispatcher()
        assert set(dispatcher.adapters) == {
            "webhook",
            "slack",
            "telegram",
            "email",
        }
        for name, adapter in dispatcher.adapters.items():
            assert isinstance(adapter, ChannelAdapter), name


# --------------------------------------------------------------------------
# WebhookDispatcher — HMAC-SHA256 signed POST via the existing SignedWebhook
# --------------------------------------------------------------------------


class TestWebhookDispatcher:
    @pytest.mark.asyncio
    async def test_post_signed_envelope_with_signature_header(self) -> None:
        client = mock.Mock()
        client.post = mock.AsyncMock(
            return_value=mock.Mock(status_code=200, is_error=False)
        )
        dispatcher = WebhookDispatcher(
            url="https://hooks.example.com/alert",
            secret="s3cret",
            client=client,
        )
        ok = await dispatcher.dispatch(make_event())
        assert ok is True
        client.post.assert_awaited_once()
        args, kwargs = client.post.await_args
        assert args[0] == "https://hooks.example.com/alert"
        # The wire body is the envelope JSON object itself — passing a dict
        # to json= (never a pre-dumped string, regression B4).
        body = kwargs["json"]
        assert isinstance(body, dict), (
            f"json= must receive the envelope dict, got {type(body).__name__}"
        )
        assert body["event"] == "budget.alert"
        assert body["payload"]["alert_rule_id"] == "rule-1"
        assert body["payload"]["current_spend"] == 95.0
        header = kwargs["headers"]["X-Signature-256"]
        assert header == SignedWebhook.build(
            "s3cret", body["event"], body["payload"], body["timestamp"]
        )["signature"]

    @pytest.mark.asyncio
    async def test_signature_is_hmac_sha256(self) -> None:
        client = mock.Mock()
        client.post = mock.AsyncMock(
            return_value=mock.Mock(status_code=200, is_error=False)
        )
        dispatcher = WebhookDispatcher(
            url="https://hooks.example.com/alert",
            secret="s3cret",
            client=client,
        )
        await dispatcher.dispatch(make_event())
        body = client.post.await_args.kwargs["json"]
        assert isinstance(body, dict), "wire body must be the envelope dict (B4)"
        header = client.post.await_args.kwargs["headers"]["X-Signature-256"]
        assert header.startswith("sha256=")
        material = (
            f"{body['timestamp']}.{body['event']}."
            + json.dumps(
                body["payload"], sort_keys=True, separators=(",", ":")
            )
        ).encode()
        expected = hmac.new(b"s3cret", material, hashlib.sha256).hexdigest()
        assert header == f"sha256={expected}"

    @pytest.mark.asyncio
    async def test_http_error_status_returns_false(self) -> None:
        client = mock.Mock()
        client.post = mock.AsyncMock(return_value=mock.Mock(status_code=500))
        dispatcher = WebhookDispatcher(
            url="https://hooks.example.com/alert",
            secret="s3cret",
            client=client,
        )
        assert await dispatcher.dispatch(make_event()) is False

    @pytest.mark.asyncio
    async def test_network_exception_returns_false(self) -> None:
        client = mock.Mock()
        client.post = mock.AsyncMock(side_effect=ConnectionError("boom"))
        dispatcher = WebhookDispatcher(
            url="https://hooks.example.com/alert",
            secret="s3cret",
            client=client,
        )
        assert await dispatcher.dispatch(make_event()) is False

    @pytest.mark.asyncio
    async def test_uses_signed_webhook_from_market_features(self) -> None:
        """Pin that the dispatcher builds envelopes with the EXISTING class."""
        client = mock.Mock()
        client.post = mock.AsyncMock(
            return_value=mock.Mock(status_code=200, is_error=False)
        )
        dispatcher = WebhookDispatcher(
            url="https://hooks.example.com/alert",
            secret="s3cret",
            client=client,
        )
        with mock.patch(
            "llm_budget_gateway.dispatch_engine.SignedWebhook.build",
            wraps=SignedWebhook.build,
        ) as build:
            await dispatcher.dispatch(make_event())
            assert build.call_count == 1


# --------------------------------------------------------------------------
# SlackDispatcher / TelegramDispatcher / EmailDispatcher
# --------------------------------------------------------------------------


class TestSlackDispatcher:
    @pytest.mark.asyncio
    async def test_posts_message_to_slack_api(self) -> None:
        client = mock.Mock()
        client.post = mock.AsyncMock(
            return_value=mock.Mock(status_code=200, is_error=False)
        )
        dispatcher = SlackDispatcher(
            bot_token="xoxb-test", channel="#alerts", client=client
        )
        ok = await dispatcher.dispatch(make_event())
        assert ok is True
        client.post.assert_awaited_once()
        args, kwargs = client.post.await_args
        assert args[0] == "https://slack.com/api/chat.postMessage"
        assert kwargs["json"]["channel"] == "#alerts"
        assert kwargs["headers"]["Authorization"] == "Bearer xoxb-test"

    @pytest.mark.asyncio
    async def test_slack_error_response_returns_false(self) -> None:
        client = mock.Mock()
        client.post = mock.AsyncMock(return_value=mock.Mock(status_code=429))
        dispatcher = SlackDispatcher(
            bot_token="xoxb-test", channel="#alerts", client=client
        )
        assert await dispatcher.dispatch(make_event()) is False


class TestTelegramDispatcher:
    @pytest.mark.asyncio
    async def test_posts_message_to_telegram_bot_api(self) -> None:
        client = mock.Mock()
        client.post = mock.AsyncMock(
            return_value=mock.Mock(status_code=200, is_error=False)
        )
        dispatcher = TelegramDispatcher(
            bot_token="123:abc", chat_id="42", client=client
        )
        ok = await dispatcher.dispatch(make_event())
        assert ok is True
        client.post.assert_awaited_once()
        args, kwargs = client.post.await_args
        assert args[0] == "https://api.telegram.org/bot123:abc/sendMessage"
        assert kwargs["json"]["chat_id"] == "42"

    @pytest.mark.asyncio
    async def test_telegram_error_status_returns_false(self) -> None:
        client = mock.Mock()
        client.post = mock.AsyncMock(return_value=mock.Mock(status_code=400))
        dispatcher = TelegramDispatcher(
            bot_token="123:abc", chat_id="42", client=client
        )
        assert await dispatcher.dispatch(make_event()) is False


class TestEmailDispatcher:
    @pytest.mark.asyncio
    async def test_sends_email_via_smtp(self) -> None:
        smtp = mock.Mock()
        smtp.login = mock.Mock()
        smtp.send_message = mock.Mock(return_value={})
        smtp.quit = mock.Mock()
        dispatcher = EmailDispatcher(
            host="smtp.example.com",
            port=587,
            username="user",
            password="pass",
            to_address="ops@example.com",
        )
        with mock.patch(
            "llm_budget_gateway.dispatch_engine.smtplib.SMTP",
            return_value=smtp,
        ) as smtp_cls:
            ok = await dispatcher.dispatch(make_event())
        assert ok is True
        smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=10)
        smtp.login.assert_called_once_with("user", "pass")
        smtp.send_message.assert_called_once()
        msg = smtp.send_message.call_args.args[0]
        assert msg["To"] == "ops@example.com"

    @pytest.mark.asyncio
    async def test_email_uses_starttls_when_configured(self) -> None:
        smtp = mock.Mock()
        smtp.login = mock.Mock()
        smtp.send_message = mock.Mock(return_value={})
        smtp.quit = mock.Mock()
        dispatcher = EmailDispatcher(
            host="smtp.example.com",
            port=587,
            username="user",
            password="pass",
            to_address="ops@example.com",
            use_tls=True,
        )
        with mock.patch(
            "llm_budget_gateway.dispatch_engine.smtplib.SMTP",
            return_value=smtp,
        ):
            await dispatcher.dispatch(make_event())
        smtp.starttls.assert_called_once()

    @pytest.mark.asyncio
    async def test_email_smtp_failure_returns_false(self) -> None:
        dispatcher = EmailDispatcher(
            host="smtp.example.com",
            port=587,
            to_address="ops@example.com",
        )
        with mock.patch(
            "llm_budget_gateway.dispatch_engine.smtplib.SMTP",
            side_effect=smtplib.SMTPException("refused"),
        ):
            assert await dispatcher.dispatch(make_event()) is False


# --------------------------------------------------------------------------
# Cooldown / dedup — same rule cannot fire twice inside the window
# --------------------------------------------------------------------------


class TestCooldownBehavior:
    @pytest.mark.asyncio
    async def test_dispatch_honors_cooldown_second_call(self) -> None:
        adapter = mock.Mock(dispatch=mock.AsyncMock(return_value=True))
        clock = mock.Mock(side_effect=[1000.0, 1000.0, 1000.0])
        dispatcher = AlertDispatcher(
            adapters={"webhook": adapter},
            cooldown_seconds=300,
            clock=clock,
        )
        first = await dispatcher.dispatch(make_event())
        second = await dispatcher.dispatch(make_event())
        assert first is True
        assert second is False  # suppressed by cooldown
        adapter.dispatch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatch_fires_again_after_cooldown_expires(self) -> None:
        adapter = mock.Mock(dispatch=mock.AsyncMock(return_value=True))
        clock = mock.Mock(side_effect=[1000.0, 1000.0, 1301.0])
        dispatcher = AlertDispatcher(
            adapters={"webhook": adapter},
            cooldown_seconds=300,
            clock=clock,
        )
        first = await dispatcher.dispatch(make_event())
        second = await dispatcher.dispatch(make_event())
        assert first is True
        assert second is True
        assert adapter.dispatch.await_count == 2

    @pytest.mark.asyncio
    async def test_zero_cooldown_disables_dedup(self) -> None:
        adapter = mock.Mock(dispatch=mock.AsyncMock(return_value=True))
        clock = mock.Mock(side_effect=[1000.0, 1000.0])
        dispatcher = AlertDispatcher(
            adapters={"webhook": adapter},
            cooldown_seconds=0,
            clock=clock,
        )
        assert await dispatcher.dispatch(make_event()) is True
        assert await dispatcher.dispatch(make_event()) is True
        assert adapter.dispatch.await_count == 2

    @pytest.mark.asyncio
    async def test_default_cooldown_is_five_minutes(self) -> None:
        assert AlertDispatcher.cooldown_seconds == 300


# --------------------------------------------------------------------------
# Retry with exponential backoff
# --------------------------------------------------------------------------


class TestRetryBehavior:
    @pytest.mark.asyncio
    async def test_retries_failed_dispatch_up_to_three_times(self) -> None:
        adapter = mock.Mock(dispatch=mock.AsyncMock(return_value=False))
        clock = mock.Mock(side_effect=[1000.0, 1000.0, 1000.0, 1000.0])
        dispatcher = AlertDispatcher(
            adapters={"webhook": adapter},
            cooldown_seconds=0,
            clock=clock,
        )
        ok = await dispatcher.dispatch(make_event())
        assert ok is False
        assert adapter.dispatch.await_count == 3

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self) -> None:
        adapter = mock.Mock(
            dispatch=mock.AsyncMock(side_effect=[False, True])
        )
        clock = mock.Mock(side_effect=[1000.0, 1000.0, 1000.0])
        dispatcher = AlertDispatcher(
            adapters={"webhook": adapter},
            cooldown_seconds=0,
            clock=clock,
        )
        ok = await dispatcher.dispatch(make_event())
        assert ok is True
        assert adapter.dispatch.await_count == 2

    @pytest.mark.asyncio
    async def test_backoff_increases_exponentially(self) -> None:
        adapter = mock.Mock(dispatch=mock.AsyncMock(return_value=False))
        clock = mock.Mock(side_effect=[1000.0, 1000.0, 1000.0, 1000.0])
        dispatcher = AlertDispatcher(
            adapters={"webhook": adapter},
            cooldown_seconds=0,
            clock=clock,
        )
        with mock.patch(
            "llm_budget_gateway.dispatch_engine.asyncio.sleep",
            new=mock.AsyncMock(),
        ) as sleep:
            await dispatcher.dispatch(make_event())
            sleeps = [c.args[0] for c in sleep.await_args_list]
        assert sleeps == [1.0, 2.0]

    @pytest.mark.asyncio
    async def test_alert_stays_pending_after_three_failures(self) -> None:
        """After 3 failed attempts the alert remains 'pending' (per AC-7)."""
        adapter = mock.Mock(dispatch=mock.AsyncMock(return_value=False))
        clock = mock.Mock(side_effect=[1000.0, 1000.0, 1000.0, 1000.0])
        dispatcher = AlertDispatcher(
            adapters={"webhook": adapter},
            cooldown_seconds=0,
            clock=clock,
        )
        with mock.patch(
            "llm_budget_gateway.dispatch_engine.asyncio.sleep",
            new=mock.AsyncMock(),
        ):
            ok = await dispatcher.dispatch(make_event())
        log = AlertDispatchLog(
            alert_rule_id="rule-1",
            channel="webhook",
            delivery_status="pending" if not ok else "delivered",
            response_code=None,
            dispatched_at=1000.0,
        )
        assert ok is False
        assert log.delivery_status == "pending"

    @pytest.mark.asyncio
    async def test_retry_count_is_configurable(self) -> None:
        adapter = mock.Mock(dispatch=mock.AsyncMock(return_value=False))
        clock = mock.Mock(side_effect=[1000.0, 1000.0, 1000.0])
        dispatcher = AlertDispatcher(
            adapters={"webhook": adapter},
            retries=1,
            cooldown_seconds=0,
            clock=clock,
        )
        with mock.patch(
            "llm_budget_gateway.dispatch_engine.asyncio.sleep",
            new=mock.AsyncMock(),
        ):
            ok = await dispatcher.dispatch(make_event())
        assert ok is False
        assert adapter.dispatch.await_count == 2  # initial + 1 retry


# --------------------------------------------------------------------------
# Integration with evaluate_alerts — async, non-blocking dispatch
# --------------------------------------------------------------------------


class TestEvaluateAlertsIntegration:
    @pytest.mark.asyncio
    async def test_evaluate_alerts_dispatches_triggered_alerts(self) -> None:
        cp = ControlPlane(":memory:", clock=lambda: 1000)
        cp.configure_workspace("t1", "admin", "Prod")
        cp.set_budget("t1", "admin", "global", 100.0)
        cp.create_alert("t1", "admin", "high spend", 0.5, "webhook:test")
        rid = cp.reserve("t1", "k", "req1", 80.0)
        cp.reconcile(rid["id"], 80.0)
        # spent 80/100 -> ratio 0.8 >= threshold 0.5 -> triggered
        assert cp.evaluate_alerts("t1")[0]["state"] == "triggered"

        adapter = mock.Mock(dispatch=mock.AsyncMock(return_value=True))
        dispatcher = AlertDispatcher(adapters={"webhook": adapter})
        triggered = cp.evaluate_alerts("t1", dispatch=dispatcher)
        assert triggered[0]["state"] == "triggered"
        adapter.dispatch.assert_awaited_once()
        event = adapter.dispatch.await_args.args[0]
        assert event.alert_rule_id == triggered[0]["id"]
        assert event.threshold == 0.5
        assert event.current_spend == 80.0

    @pytest.mark.asyncio
    async def test_evaluate_alerts_does_not_dispatch_ready_alerts(self) -> None:
        cp = ControlPlane(":memory:", clock=lambda: 1000)
        cp.configure_workspace("t1", "admin", "Prod")
        cp.set_budget("t1", "admin", "global", 100.0)
        cp.create_alert("t1", "admin", "safe", 0.9, "webhook:test")
        adapter = mock.Mock(dispatch=mock.AsyncMock(return_value=True))
        dispatcher = AlertDispatcher(adapters={"webhook": adapter})
        ready = cp.evaluate_alerts("t1", dispatch=dispatcher)
        assert ready[0]["state"] == "ready"
        adapter.dispatch.assert_not_awaited()

    def test_evaluate_alerts_returns_sync_when_dispatch_omitted(self) -> None:
        """Default behavior is unchanged: evaluate_alerts(t) returns list."""
        cp = ControlPlane(":memory:", clock=lambda: 1000)
        cp.configure_workspace("t1", "admin", "Prod")
        cp.set_budget("t1", "admin", "global", 100.0)
        cp.create_alert("t1", "admin", "high spend", 0.5, "webhook:test")
        rid = cp.reserve("t1", "k", "req1", 80.0)
        cp.reconcile(rid["id"], 80.0)
        out = cp.evaluate_alerts("t1")
        assert isinstance(out, list)
        assert out[0]["state"] == "triggered"

    @pytest.mark.asyncio
    async def test_evaluate_alerts_dispatch_is_non_blocking(self) -> None:
        """dispatch() runs in the background: the evaluate call returns
        before the adapter coroutine completes."""
        gate = asyncio.Event()
        started = asyncio.Event()

        async def slow_dispatch(event) -> bool:
            started.set()
            await gate.wait()
            return True

        adapter = mock.Mock(dispatch=slow_dispatch)
        dispatcher = AlertDispatcher(adapters={"webhook": adapter})
        cp = ControlPlane(":memory:", clock=lambda: 1000)
        cp.configure_workspace("t1", "admin", "Prod")
        cp.set_budget("t1", "admin", "global", 100.0)
        cp.create_alert("t1", "admin", "high spend", 0.5, "webhook:test")
        rid = cp.reserve("t1", "k", "req1", 80.0)
        cp.reconcile(rid["id"], 80.0)

        # The dispatch task was started (not awaited inline)...
        result = cp.evaluate_alerts("t1", dispatch=dispatcher)
        await asyncio.wait_for(started.wait(), timeout=1.0)
        assert result[0]["state"] == "triggered"
        # ...and the adapter coroutine is still blocked on our gate,
        # proving evaluate_alerts did not await it to completion.
        assert gate.is_set() is False
        gate.set()
        # let the background task finish cleanly
        await asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_evaluate_alerts_uses_async_task(self) -> None:
        """The integration must fire dispatch via asyncio.create_task."""
        cp = ControlPlane(":memory:", clock=lambda: 1000)
        cp.configure_workspace("t1", "admin", "Prod")
        cp.set_budget("t1", "admin", "global", 100.0)
        cp.create_alert("t1", "admin", "high spend", 0.5, "webhook:test")
        rid = cp.reserve("t1", "k", "req1", 80.0)
        cp.reconcile(rid["id"], 80.0)
        adapter = mock.Mock(dispatch=mock.AsyncMock(return_value=True))
        dispatcher = AlertDispatcher(adapters={"webhook": adapter})
        with mock.patch(
            "llm_budget_gateway.control_plane.asyncio.create_task",
            wraps=asyncio.create_task,
        ) as create_task:
            cp.evaluate_alerts("t1", dispatch=dispatcher)
            assert create_task.call_count >= 1


# --------------------------------------------------------------------------
# Regression — cooldown dedup must be race-condition safe (BLOCKER-3)
# --------------------------------------------------------------------------


class TestCooldownRaceSafety:
    """Two concurrent dispatch() calls for the SAME rule inside the cooldown
    window must result in exactly ONE adapter call.

    Regression for BLOCKER-3: the check-then-set on ``_last_fired`` was not
    atomic, so ``asyncio.gather`` of two dispatches both passed the cooldown
    check and both fired. Reachable in production because ``evaluate_alerts``
    fires dispatch via fire-and-forget background tasks.
    """

    @pytest.mark.asyncio
    async def test_concurrent_dispatches_fire_once(self) -> None:
        """Two concurrent dispatches of the same rule must dedup to ONE
        adapter call even when the adapter yields (real network I/O)."""
        calls = 0

        class SuspendingAdapter:
            async def dispatch(self, event) -> bool:
                nonlocal calls
                calls += 1
                await asyncio.sleep(0)  # yield — lets the sibling interleave
                return True

        dispatcher = AlertDispatcher(
            adapters={"webhook": SuspendingAdapter()},
            cooldown_seconds=300,
            clock=mock.Mock(return_value=1000.0),
        )
        results = await asyncio.gather(
            dispatcher.dispatch(make_event()),
            dispatcher.dispatch(make_event()),
        )
        assert results == [True, False]
        assert calls == 1, (
            "concurrent dispatches of the same rule must dedup to one "
            f"adapter call, got {calls}"
        )

    @pytest.mark.asyncio
    async def test_concurrent_dispatches_different_rules_both_fire(self) -> None:
        """The lock must not block different rules: two different rules
        dispatched concurrently both fire."""
        calls = 0

        class SuspendingAdapter:
            async def dispatch(self, event) -> bool:
                nonlocal calls
                calls += 1
                await asyncio.sleep(0)
                return True

        dispatcher = AlertDispatcher(
            adapters={"webhook": SuspendingAdapter()},
            cooldown_seconds=300,
            clock=mock.Mock(return_value=1000.0),
        )
        results = await asyncio.gather(
            dispatcher.dispatch(make_event(alert_rule_id="rule-a")),
            dispatcher.dispatch(make_event(alert_rule_id="rule-b")),
        )
        assert results == [True, True]
        assert calls == 2


# --------------------------------------------------------------------------
# Regression — wire body must be the envelope JSON object (BLOCKER-4)
# --------------------------------------------------------------------------


class TestWebhookWireBody:
    """The transmitted body must be the envelope JSON OBJECT, and the
    X-Signature-256 header must verify against the transmitted envelope.

    Regression for BLOCKER-4: json.dumps() was passed to ``json=``, so the
    wire body was a JSON string literal wrapping the envelope (double
    encoding) and the signature header no longer signed the transmitted
    bytes. Verified through a REAL httpx client with a capture transport.
    """

    @pytest.mark.asyncio
    async def test_real_httpx_client_wire_body_is_envelope_object(self) -> None:
        import httpx

        captured = {}

        class CaptureTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(
                self, request: httpx.Request
            ) -> httpx.Response:
                captured["body"] = request.content
                captured["headers"] = dict(request.headers)
                return httpx.Response(200, json={"ok": True})

        secret = "s3cret"
        event = make_event()
        dispatcher = WebhookDispatcher(
            url="https://hooks.example.com/alert",
            secret=secret,
            client=httpx.AsyncClient(transport=CaptureTransport()),
            clock=lambda: 1000,
        )
        ok = await dispatcher.dispatch(event)
        assert ok is True

        body = json.loads(captured["body"].decode("utf-8"))
        assert isinstance(body, dict), (
            "wire body must parse as a JSON object (not a string literal "
            "wrapping the envelope)"
        )
        assert body["event"] == "budget.alert"
        assert body["payload"]["alert_rule_id"] == "rule-1"

        # The signature header must verify against the transmitted envelope.
        # (httpx normalizes header names to lowercase on the wire.)
        header = captured["headers"].get(
            "X-Signature-256", captured["headers"].get("x-signature-256", "")
        )
        assert header, "X-Signature-256 header missing from captured request"
        assert header == SignedWebhook.build(
            secret, body["event"], body["payload"], body["timestamp"]
        )["signature"]
        assert SignedWebhook.verify(secret, body) is True

    @pytest.mark.asyncio
    async def test_signature_verifies_against_serialized_wire_body(self) -> None:
        """HMAC must sign the transmitted bytes: rebuilding the envelope from
        the wire body and verifying must match the sent header."""
        import httpx

        captured = {}

        class CaptureTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(
                self, request: httpx.Request
            ) -> httpx.Response:
                captured["body"] = request.content
                captured["headers"] = dict(request.headers)
                return httpx.Response(200, json={"ok": True})

        secret = "s3cret"
        dispatcher = WebhookDispatcher(
            url="https://hooks.example.com/alert",
            secret=secret,
            client=httpx.AsyncClient(transport=CaptureTransport()),
            clock=lambda: 1000,
        )
        await dispatcher.dispatch(make_event(current_spend=42.0))

        wire = json.loads(captured["body"].decode("utf-8"))
        assert isinstance(wire, dict)
        expected = SignedWebhook.build(
            secret,
            wire["event"],
            wire["payload"],
            wire["timestamp"],
        )["signature"]
        header = captured["headers"].get(
            "X-Signature-256", captured["headers"].get("x-signature-256", "")
        )
        assert header, "X-Signature-256 header missing from captured request"
        assert header == expected
