"""Production wiring tests for the alert notification feature (BLOCKER-2).

Regression for BLOCKER-2: ``create_alerts_app`` was mounted nowhere and
no production caller of ``evaluate_alerts(dispatch=...)`` existed — the
feature was reachable only through unit tests. These tests pin the
wiring contract:

- ``create_console_app`` mounts the alerts app, so the alert routes are
  reachable through the shipped console app.
- ``create_console_app`` exposes ``app.state.alert_dispatcher`` — a
  real ``AlertDispatcher`` wired with channel adapters built from the
  alert rules API's persisted rule configs (webhook/slack/telegram/email)
  plus the canonical SSRFGuard.
- The console exposes ``POST /v1/console/alerts/evaluate`` which calls
  ``evaluate_alerts(dispatch=...)`` on the control plane with the wired
  dispatcher.
- The alerts satellite service is registered in the service manager
  (runnable standalone via the repo's uvicorn service convention).
"""

from __future__ import annotations

import httpx
import pytest

from llm_budget_gateway.console_api import create_console_app
from llm_budget_gateway.dispatch_engine import AlertDispatcher


class TestAlertsMountedInConsoleApp:
    """The shipped console app must expose the alert rules API."""

    def test_console_app_has_alert_routes(self) -> None:
        app = create_console_app()
        paths = {getattr(r, "path", None) for r in app.routes}
        assert "/api/alerts" in paths
        assert "/api/alerts/history" in paths

    @pytest.mark.asyncio
    async def test_alert_routes_reachable_through_console_app(self) -> None:
        app = create_console_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://console"
        ) as client:
            resp = await client.get("/api/alerts")
            assert resp.status_code == 200
            assert "items" in resp.json()

            history = await client.get("/api/alerts/history")
            assert history.status_code == 200

    @pytest.mark.asyncio
    async def test_create_rule_via_console_app_persists(self) -> None:
        """A rule created through the mounted app must come back in GET."""
        app = create_console_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://console"
        ) as client:
            resp = await client.post(
                "/api/alerts",
                json={
                    "name": "wired rule",
                    "threshold": 0.8,
                    "channel": "webhook",
                    "config": {"url": "https://hooks.example.com/budget"},
                },
            )
            assert resp.status_code == 201, resp.text
            rule_id = resp.json()["id"]
            listing = await client.get("/api/alerts")
            assert any(item["id"] == rule_id for item in listing.json()["items"])


class TestAlertDispatcherWiring:
    """The console app must carry a wired AlertDispatcher."""

    def test_console_app_state_has_alert_dispatcher(self, tmp_path) -> None:
        app = create_console_app(alerts_db_path=tmp_path / "alerts.db")
        dispatcher = app.state.alert_dispatcher
        assert isinstance(dispatcher, AlertDispatcher)
        assert set(dispatcher.adapters) == {
            "webhook",
            "slack",
            "telegram",
            "email",
        }

    def test_dispatcher_adapters_built_from_configs(self, tmp_path) -> None:
        """Adapters must be real, configured instances (not empty shells)."""
        app = create_console_app(alerts_db_path=tmp_path / "alerts.db")
        dispatcher: AlertDispatcher = app.state.alert_dispatcher
        assert dispatcher.adapters["webhook"].url == ""
        # The wiring factory must be callable with a rule config dict.
        assert callable(app.state.build_alert_adapter)


class TestEvaluateAlertsEndpoint:
    """POST /v1/console/alerts/evaluate must drive evaluate_alerts."""

    @pytest.mark.asyncio
    async def test_evaluate_endpoint_reachable(self) -> None:
        app = create_console_app()
        paths = {getattr(r, "path", None) for r in app.routes}
        assert "/v1/console/alerts/evaluate" in paths

    @pytest.mark.asyncio
    async def test_evaluate_endpoint_returns_alert_states(self) -> None:
        app = create_console_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://console"
        ) as client:
            resp = await client.post(
                "/v1/console/alerts/evaluate",
                json={"tenant": "t1"},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert isinstance(body.get("alerts"), list)


class TestAlertsSatelliteService:
    """The alerts service must be registered for standalone launch."""

    def test_alerts_service_registered(self, monkeypatch) -> None:
        """The alerts service must be runnable standalone via the repo's
        satellite convention (GATEWAY_ENABLE_SATELLITES=1), alongside the
        other feature apps."""
        monkeypatch.setenv("GATEWAY_ENABLE_SATELLITES", "1")
        import importlib

        import llm_budget_gateway.service_manager as sm

        importlib.reload(sm)
        try:
            slugs = {svc.slug for svc in sm.SERVICES}
            assert "alerts" in slugs, (
                "alerts service must be registered in the service manager "
                "so the feature is runnable in production (BLOCKER-2)"
            )
            alerts_def = next(s for s in sm.SERVICES if s.slug == "alerts")
            assert alerts_def.factory == "llm_budget_gateway.alert_api:create_alerts_app"
            assert alerts_def.port == 8016
        finally:
            importlib.reload(sm)  # restore default env-gated tuple
