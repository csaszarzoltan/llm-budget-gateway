"""TDD: live probe of a published route — POST /_admin/probe-route.

The static ``test_route`` only checks schedule/capabilities and can report
a healthy chain while every upstream is actually failing (muse-spark case,
2026-08-23). The probe walks the real candidate chain with a tiny message
and reports per-target outcome, so the cockpit shows what the flow does.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient

from llm_budget_gateway.config import Settings


def _seed_product_db(tmp_path: Path) -> Path:
    """Create a product.db with one active route 'smart' (2 targets)."""
    from llm_budget_gateway.product_console import ProductConsoleStore

    db_path = tmp_path / "product.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    store = ProductConsoleStore(conn)
    targets = [
        {
            "priority": 10,
            "model": "@prov-a/muse",
            "timeout_seconds": 90,
            "timezone": "UTC",
            "start": "00:00",
            "end": "23:59",
        },
        {
            "priority": 20,
            "model": "@prov-b/muse",
            "timeout_seconds": 90,
            "timezone": "UTC",
            "start": "00:00",
            "end": "23:59",
        },
    ]
    route = store.create_route("smart", targets)
    store.publish_route(route["id"])
    return db_path


def _make_app(tmp_path: Path) -> object:
    """create_app with product.db present; direct client attached."""
    db_path = _seed_product_db(tmp_path)
    data_dir = tmp_path / ".gateway-console"
    data_dir.mkdir()
    (data_dir / "providers.db").touch()
    (data_dir / "provider-master.key").touch()

    settings = Settings(database_url=f"sqlite:///{tmp_path}/gateway.db")

    from llm_budget_gateway.gateway_proxy import GatewayProxy
    from llm_budget_gateway.product_console import ProductConsoleStore

    settings_obj = settings
    # Build a minimal real proxy (same constructor main uses).
    from llm_budget_gateway.budget_enforcement import BudgetEnforcer
    from llm_budget_gateway.cost_tracking import (
        CostCalculator,
        CostStore,
        CostTracker,
        PriceMap,
    )
    from llm_budget_gateway.model_fallback import FallbackManager

    store = CostStore(str(tmp_path / "gateway.db"))
    tracker = CostTracker(store=store, calculator=CostCalculator(PriceMap()))
    enforcer = BudgetEnforcer(configs=[], cost_tracker=tracker)
    manager = FallbackManager(configs=[])
    proxy = GatewayProxy(
        settings=settings_obj,
        cost_tracker=tracker,
        budget_enforcer=enforcer,
        fallback_manager=manager,
    )
    proxy.attach_product_console(
        ProductConsoleStore(sqlite3.connect(str(db_path), check_same_thread=False))
    )

    direct = Mock()
    outcomes: dict = {}

    def _resolve(model: str) -> None:
        if model not in outcomes:
            raise LookupError(model)

    direct.resolve = _resolve

    async def _forward(model: str, body: dict, kind: str = "chat", **kw):  # noqa: ANN001
        result = outcomes[model]
        if isinstance(result, Exception):
            raise result
        return result

    direct.forward = _forward
    proxy.attach_direct_client(direct)

    from fastapi import FastAPI as _FastAPI

    app = _FastAPI()
    # Register the probe endpoint under test against OUR proxy instance.
    from llm_budget_gateway.main import register_probe_route_endpoint

    register_probe_route_endpoint(app, proxy)
    return app, outcomes


def test_probe_route_reports_first_working_target(tmp_path) -> None:
    """When the first target succeeds the probe stops there."""

    app, outcomes = _make_app(tmp_path)
    outcomes["@prov-a/muse"] = (
        200,
        {"choices": [{"message": {"content": "pong"}}], "model": "@prov-a/muse"},
        "@prov-a/muse",
    )
    client = TestClient(app)
    resp = client.post(
        "/_admin/probe-route",
        json={"route": "smart"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["served_by"] == "@prov-a/muse"
    assert body["attempts"][0]["model"] == "@prov-a/muse"
    assert body["attempts"][0]["status"] == "success"


def test_probe_walks_chain_after_upstream_error(tmp_path) -> None:
    """First target 500s → probe falls through to the second and reports both."""
    from llm_budget_gateway.provider_direct import UpstreamProviderError

    app, outcomes = _make_app(tmp_path)
    outcomes["@prov-a/muse"] = UpstreamProviderError(500, "prov-a failed")

    outcomes["@prov-b/muse"] = (
        200,
        {"choices": [{"message": {"content": "pong"}}], "model": "@prov-b/muse"},
        "@prov-b/muse",
    )
    client = TestClient(app)
    resp = client.post("/_admin/probe-route", json={"route": "smart"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["served_by"] == "@prov-b/muse"
    statuses = [a["status"] for a in body["attempts"]]
    assert statuses == ["error", "success"]
    assert "500" in body["attempts"][0]["detail"]


def test_probe_reports_when_every_target_fails(tmp_path) -> None:
    """All targets fail → ok False, per-attempt errors surfaced (no fake OK)."""
    from llm_budget_gateway.provider_direct import UpstreamProviderError

    app, outcomes = _make_app(tmp_path)
    outcomes["@prov-a/muse"] = UpstreamProviderError(500, "a down")
    outcomes["@prov-b/muse"] = UpstreamProviderError(404, "b down")
    client = TestClient(app)
    resp = client.post("/_admin/probe-route", json={"route": "smart"})
    assert resp.status_code == 502
    body = resp.json()
    assert body["ok"] is False
    assert len(body["attempts"]) == 2
    assert body["attempts"][1]["detail"].find("404") >= 0
