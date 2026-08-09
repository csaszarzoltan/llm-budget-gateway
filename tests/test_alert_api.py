"""Pre-development tests for the Alert Rules REST API and Alert History.

Interface tests (app factory, route registration): pass immediately.
Behavioral tests (CRUD round-trips, validation errors, history
pagination): RED until the developer implements the handlers in
``src/llm_budget_gateway/alert_api.py``.

Contract (planning spec acceptance criteria, aligned with the sibling
dispatch-engine pre-tests and the repo's ``create_*_app`` factory
convention — see ``control_api``, ``security_api``, ``evaluation_api``):

- ``create_alerts_app(db_path) -> FastAPI``; routes mounted at:
  ``POST /api/alerts`` (201, body includes rule id),
  ``GET /api/alerts`` (``{"items": [...]}``),
  ``GET /api/alerts/{id}`` (404 on missing),
  ``DELETE /api/alerts/{id}`` (204, 404 on missing),
  ``GET /api/alerts/history`` (``{"items": [...], "total": N}``,
  paginated + filterable by alert_rule_id / channel / delivery_status).
- Channel config validation on POST: webhook needs ``url``; slack needs
  ``bot_token`` + ``channel``; telegram needs ``bot_token`` + ``chat_id``;
  email needs ``host``, ``username``, ``to_address``. Missing required
  fields → 422.
"""

from __future__ import annotations

import sqlite3

import httpx
import pytest
from fastapi import FastAPI

# ============================================================
# Interface tests — app factory, route registration
# ============================================================


class TestAlertApiInterface:
    """Verify the alert API module structure before behavioral tests."""

    def test_alert_api_module_importable(self):
        from llm_budget_gateway import alert_api  # noqa: F401

    def test_create_alerts_app_exists(self):
        from llm_budget_gateway.alert_api import create_alerts_app

        assert callable(create_alerts_app)

    def test_create_alerts_app_returns_fastapi_app(self, tmp_path):
        from llm_budget_gateway.alert_api import create_alerts_app

        app = create_alerts_app(str(tmp_path / "alerts.db"))
        assert isinstance(app, FastAPI)

    def test_router_has_expected_routes(self, tmp_path):
        """All five alert routes registered at the spec paths."""
        from llm_budget_gateway.alert_api import create_alerts_app

        app = create_alerts_app(str(tmp_path / "alerts.db"))
        paths = {getattr(r, "path", None) for r in app.routes}
        assert "/api/alerts" in paths
        assert "/api/alerts/{rule_id}" in paths
        assert "/api/alerts/history" in paths


# ============================================================
# Helpers
# ============================================================


async def _get_client(tmp_path) -> httpx.AsyncClient:
    """Fresh httpx AsyncClient against an isolated per-test alert app."""
    from llm_budget_gateway.alert_api import create_alerts_app

    app = create_alerts_app(str(tmp_path / "alerts.db"))
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _webhook_payload(**overrides) -> dict:
    payload = {
        "name": "Team Budget Warning",
        "threshold": 0.80,
        "channel": "webhook",
        "config": {"url": "https://hooks.example.com/alert"},
        "cooldown_seconds": 300,
        "enabled": True,
    }
    payload.update(overrides)
    return payload


def _slack_payload(**overrides) -> dict:
    payload = {
        "name": "Slack Alert",
        "threshold": 0.90,
        "channel": "slack",
        "config": {
            "bot_token": "xoxb-fake-token",
            "channel": "#budget-alerts",
        },
        "cooldown_seconds": 300,
        "enabled": True,
    }
    payload.update(overrides)
    return payload


def _telegram_payload(**overrides) -> dict:
    payload = {
        "name": "Telegram Alert",
        "threshold": 0.75,
        "channel": "telegram",
        "config": {
            "bot_token": "123456:ABC-DEF",
            "chat_id": "-1001234567890",
        },
        "cooldown_seconds": 300,
        "enabled": True,
    }
    payload.update(overrides)
    return payload


def _email_payload(**overrides) -> dict:
    payload = {
        "name": "Email Alert",
        "threshold": 0.85,
        "channel": "email",
        "config": {
            "host": "smtp.example.com",
            "port": 587,
            "username": "alerts@example.com",
            "password": "secret",
            "to_address": "ops@example.com",
        },
        "cooldown_seconds": 300,
        "enabled": True,
    }
    payload.update(overrides)
    return payload


# ============================================================
# Behavioral — rules CRUD (RED until handlers implemented)
# ============================================================


class TestAlertRulesCrud:
    """POST/GET/DELETE round-trips for alert rules."""

    @pytest.mark.asyncio
    async def test_create_rule_returns_201_with_id(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            resp = await client.post(
                "/api/alerts", json=_webhook_payload()
            )
            assert resp.status_code == 201, resp.text
            body = resp.json()
            assert "id" in body, "response must include rule id"
            assert body["name"] == "Team Budget Warning"
            assert body["threshold"] == 0.80
            assert body["channel"] == "webhook"

    @pytest.mark.asyncio
    async def test_get_list_returns_all_rules(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            await client.post("/api/alerts", json=_webhook_payload())
            await client.post(
                "/api/alerts",
                json=_slack_payload(name="Slack Rule"),
            )
            resp = await client.get("/api/alerts")
            assert resp.status_code == 200
            body = resp.json()
            items = body.get("items", body if isinstance(body, list) else [])
            assert len(items) >= 2

    @pytest.mark.asyncio
    async def test_get_by_id_returns_single_rule(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            create_resp = await client.post(
                "/api/alerts", json=_webhook_payload(name="Single Rule")
            )
            rule_id = create_resp.json()["id"]
            resp = await client.get(f"/api/alerts/{rule_id}")
            assert resp.status_code == 200
            assert resp.json()["id"] == rule_id
            assert resp.json()["name"] == "Single Rule"

    @pytest.mark.asyncio
    async def test_get_by_id_404_on_missing(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            resp = await client.get("/api/alerts/nonexistent-id")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_rule_returns_204(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            create_resp = await client.post(
                "/api/alerts", json=_webhook_payload()
            )
            rule_id = create_resp.json()["id"]
            resp = await client.delete(f"/api/alerts/{rule_id}")
            assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_rule_404_on_missing(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            resp = await client.delete("/api/alerts/nonexistent-id")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_deleted_rule_not_in_list(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            create_resp = await client.post(
                "/api/alerts",
                json=_webhook_payload(name="To Delete"),
            )
            rule_id = create_resp.json()["id"]
            await client.delete(f"/api/alerts/{rule_id}")
            get_resp = await client.get(f"/api/alerts/{rule_id}")
            assert get_resp.status_code == 404


# ============================================================
# Behavioral — channel config validation (RED until handlers)
# ============================================================


class TestChannelConfigValidation:
    """POST /api/alerts returns 422 when required config fields are missing."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "channel,payload_factory",
        [
            ("webhook", lambda: _webhook_payload(config={})),
            ("slack", lambda: _slack_payload(config={})),
            ("telegram", lambda: _telegram_payload(config={})),
            ("email", lambda: _email_payload(config={})),
        ],
    )
    async def test_missing_required_config_returns_422(
        self, tmp_path, channel, payload_factory
    ):
        async with await _get_client(tmp_path) as client:
            payload = payload_factory()
            resp = await client.post("/api/alerts", json=payload)
            assert resp.status_code in (400, 422), (
                f"channel={channel} with empty config should fail, "
                f"got {resp.status_code}: {resp.text}"
            )

    @pytest.mark.asyncio
    async def test_webhook_missing_url_returns_422(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            resp = await client.post(
                "/api/alerts",
                json=_webhook_payload(
                    config={"wrong_field": "value"},
                ),
            )
            assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_slack_missing_bot_token_returns_422(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            resp = await client.post(
                "/api/alerts",
                json=_slack_payload(config={"channel": "#alerts"}),
            )
            assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_slack_missing_channel_returns_422(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            resp = await client.post(
                "/api/alerts",
                json=_slack_payload(config={"bot_token": "xoxb-fake"}),
            )
            assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_telegram_missing_chat_id_returns_422(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            resp = await client.post(
                "/api/alerts",
                json=_telegram_payload(config={"bot_token": "123:ABC"}),
            )
            assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_email_missing_host_returns_422(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            resp = await client.post(
                "/api/alerts",
                json=_email_payload(
                    config={
                        "username": "a@b.com",
                        "to_address": "c@d.com",
                    }
                ),
            )
            assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_invalid_channel_value_returns_422(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            resp = await client.post(
                "/api/alerts",
                json={
                    "name": "bad",
                    "threshold": 0.5,
                    "channel": "carrier_pigeon",
                    "config": {},
                },
            )
            assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_threshold_out_of_range_returns_422(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            resp = await client.post(
                "/api/alerts",
                json=_webhook_payload(threshold=1.5),
            )
            assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_negative_threshold_returns_422(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            resp = await client.post(
                "/api/alerts",
                json=_webhook_payload(threshold=-0.1),
            )
            assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_missing_name_returns_422(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            resp = await client.post(
                "/api/alerts",
                json=_webhook_payload(name=""),
            )
            assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_missing_required_body_fields_returns_422(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            resp = await client.post("/api/alerts", json={})
            assert resp.status_code in (400, 422)


# ============================================================
# Behavioral — all four channel types create successfully
# ============================================================


class TestMultiChannelCreate:
    """POST /api/alerts with complete config for each channel type."""

    @pytest.mark.asyncio
    async def test_webhook_create(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            resp = await client.post(
                "/api/alerts", json=_webhook_payload()
            )
            assert resp.status_code == 201
            assert resp.json()["channel"] == "webhook"

    @pytest.mark.asyncio
    async def test_slack_create(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            resp = await client.post(
                "/api/alerts", json=_slack_payload()
            )
            assert resp.status_code == 201
            assert resp.json()["channel"] == "slack"

    @pytest.mark.asyncio
    async def test_telegram_create(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            resp = await client.post(
                "/api/alerts", json=_telegram_payload()
            )
            assert resp.status_code == 201
            assert resp.json()["channel"] == "telegram"

    @pytest.mark.asyncio
    async def test_email_create(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            resp = await client.post(
                "/api/alerts", json=_email_payload()
            )
            assert resp.status_code == 201
            assert resp.json()["channel"] == "email"


# ============================================================
# Behavioral — Alert History API
# ============================================================


class TestAlertHistoryApi:
    """GET /api/alerts/history — paginated dispatch logs."""

    @pytest.mark.asyncio
    async def test_history_returns_200_with_items(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            resp = await client.get("/api/alerts/history")
            assert resp.status_code == 200
            body = resp.json()
            assert "items" in body or "events" in body, (
                f"history response must have 'items' or 'events' key: "
                f"{list(body.keys())}"
            )

    @pytest.mark.asyncio
    async def test_history_default_pagination(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            resp = await client.get("/api/alerts/history")
            assert resp.status_code == 200
            body = resp.json()
            assert "total" in body, (
                "history response must include 'total' field"
            )
            items = body.get("items", body.get("events", []))
            # default page_size should be ≤ 100
            assert len(items) <= 100

    @pytest.mark.asyncio
    async def test_history_respects_page_size(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            resp = await client.get(
                "/api/alerts/history?page=1&page_size=5"
            )
            assert resp.status_code == 200
            body = resp.json()
            items = body.get("items", body.get("events", []))
            assert len(items) <= 5

    @pytest.mark.asyncio
    async def test_history_filters_by_alert_rule_id(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            resp = await client.get(
                "/api/alerts/history?alert_rule_id=nonexistent"
            )
            assert resp.status_code == 200
            body = resp.json()
            items = body.get("items", body.get("events", []))
            assert len(items) == 0

    @pytest.mark.asyncio
    async def test_history_filters_by_channel(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            resp = await client.get(
                "/api/alerts/history?channel=webhook"
            )
            assert resp.status_code == 200
            body = resp.json()
            items = body.get("items", body.get("events", []))
            for item in items:
                ch = item.get("channel", "")
                assert ch == "webhook" or ch == "", (
                    f"filtered results should only contain channel=webhook, "
                    f"got {ch}"
                )

    @pytest.mark.asyncio
    async def test_history_filters_by_delivery_status(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            resp = await client.get(
                "/api/alerts/history?delivery_status=delivered"
            )
            assert resp.status_code == 200
            body = resp.json()
            items = body.get("items", body.get("events", []))
            for item in items:
                status = item.get("delivery_status", "")
                assert status in ("delivered", ""), (
                    f"filtered results should have delivery_status=delivered, "
                    f"got {status}"
                )

    @pytest.mark.asyncio
    async def test_history_empty_when_no_alerts_fired(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            resp = await client.get("/api/alerts/history")
            assert resp.status_code == 200
            body = resp.json()
            total = body.get("total", 0)
            assert isinstance(total, int)


# ============================================================
# Regression — persistence across fresh connections (BLOCKER-1)
# ============================================================


class TestPersistenceAcrossConnections:
    """Writes must be durable: a FRESH sqlite3 connection on the same DB
    file must observe create_rule / record_dispatch / delete_rule effects.

    Regression for BLOCKER-1: the app previously never committed, so every
    INSERT lived in an uncommitted transaction visible only to the app's own
    connection. A process restart / pooled second worker lost every rule and
    every history entry.
    """

    @pytest.mark.asyncio
    async def test_created_rule_is_visible_to_fresh_connection(self, tmp_path):
        from llm_budget_gateway.alert_api import create_alerts_app

        db_path = str(tmp_path / "alerts.db")
        app = create_alerts_app(db_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post("/api/alerts", json=_webhook_payload())
            assert resp.status_code == 201
            rule_id = resp.json()["id"]

        # A fresh connection == new process / second worker.
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT id, name FROM alert_rules WHERE id = ?", (rule_id,)
            ).fetchone()
        finally:
            conn.close()
        assert row is not None, "rule must survive a fresh connection (B1)"
        assert row[1] == "Team Budget Warning"

    @pytest.mark.asyncio
    async def test_dispatch_log_is_visible_to_fresh_connection(self, tmp_path):
        from llm_budget_gateway.alert_api import create_alerts_app
        from llm_budget_gateway.alert_models import AlertDispatchLog

        db_path = str(tmp_path / "alerts.db")
        app = create_alerts_app(db_path)
        app.state.record_dispatch(
            AlertDispatchLog(
                alert_rule_id="rule-1",
                channel="webhook",
                delivery_status="delivered",
                response_code=200,
                dispatched_at=1000.0,
            )
        )

        conn = sqlite3.connect(db_path)
        try:
            count = conn.execute(
                "SELECT count(*) FROM alert_dispatch_log"
            ).fetchone()[0]
        finally:
            conn.close()
        assert count == 1, "dispatch log must survive a fresh connection (B1)"

    @pytest.mark.asyncio
    async def test_deleted_rule_is_gone_for_fresh_connection(self, tmp_path):
        from llm_budget_gateway.alert_api import create_alerts_app

        db_path = str(tmp_path / "alerts.db")
        app = create_alerts_app(db_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            create_resp = await client.post(
                "/api/alerts", json=_webhook_payload()
            )
            rule_id = create_resp.json()["id"]
            del_resp = await client.delete(f"/api/alerts/{rule_id}")
            assert del_resp.status_code == 204

        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT id FROM alert_rules WHERE id = ?", (rule_id,)
            ).fetchone()
        finally:
            conn.close()
        assert row is None, "delete must survive a fresh connection (B1)"


# ============================================================
# Regression — cooldown validation (BLOCKER-2)
# ============================================================


class TestCooldownValidation:
    """cooldown_seconds must be a non-negative integer (0 = dedup disabled)."""

    @pytest.mark.asyncio
    async def test_negative_cooldown_returns_422(self, tmp_path):
        async with await _get_client(tmp_path) as client:
            resp = await client.post(
                "/api/alerts", json=_webhook_payload(cooldown_seconds=-5)
            )
            assert resp.status_code == 422, (
                f"negative cooldown must be rejected with 422, got "
                f"{resp.status_code}: {resp.text}"
            )

    @pytest.mark.asyncio
    async def test_zero_cooldown_accepted(self, tmp_path):
        """ge=0 semantics: 0 is valid and means dedup disabled."""
        async with await _get_client(tmp_path) as client:
            resp = await client.post(
                "/api/alerts", json=_webhook_payload(cooldown_seconds=0)
            )
            assert resp.status_code == 201, resp.text
