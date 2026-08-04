"""Ten autonomous product iterations, each protected by focused tests."""

import sqlite3

import pytest

from llm_budget_gateway.product_extensions import ProductExtensions


@pytest.fixture
def x():
    return ProductExtensions(sqlite3.connect(":memory:"))


def test_iteration_1_key_rotation_and_revocation(x):
    k = x.rotate_key("app1")
    assert k["api_key"].startswith("gw_")
    assert x.revoke_key(k["id"])["status"] == "revoked"


def test_iteration_2_budget_headroom(x):
    x.set_budget("route:support", 100, 5)
    b = x.add_spend("route:support", 82)
    assert b["remaining_usd"] == 18
    assert b["percent_used"] == 82


def test_iteration_3_alert_rules(x):
    x.create_alert("Cost warning", "cost", 80)
    assert x.alerts()[0]["enabled"]


def test_iteration_4_environments(x):
    x.create_environment("Development", "http://localhost:8000/v1", True)
    x.create_environment("Production", "https://gateway.example/v1")
    assert x.environments()[0]["default"]


def test_iteration_5_saved_role_views(x):
    v = x.save_view("Incidents", "operator", {"status": "error"})
    assert x.views("operator")[0] == v


def test_iteration_6_provider_checks(x):
    c = x.provider_check("openai", False, 350)
    assert not c["healthy"]
    assert x.recommendations()[0]["severity"] == "critical"


def test_iteration_7_route_rollback(x):
    x.snapshot_route("r1", 3, {"targets": ["a", "b"]})
    assert x.rollback_route("r1", 3)["payload"]["targets"] == ["a", "b"]


def test_iteration_8_archival(x):
    assert x.archive("route", "r1", {"name": "old"})["status"] == "archived"


def test_iteration_9_export_import(x):
    x.set_budget("global", 200)
    x.create_alert("Latency", "latency", 1000)
    bundle = x.export_bundle()
    y = ProductExtensions(sqlite3.connect(":memory:"))
    assert y.import_bundle(bundle)["imported"] == 2


def test_iteration_10_recommendations_and_audit(x):
    x.set_budget("global", 10)
    x.add_spend("global", 9)
    items = x.recommendations()
    assert items[0]["kind"] == "budget"
    assert x.audit()[0]["action"] == "budget.set"


def test_validation_edges(x):
    with pytest.raises(ValueError):
        x.set_budget("bad", 0)
    with pytest.raises(ValueError):
        x.create_alert("x", "unknown", 1)
    with pytest.raises(ValueError):
        x.create_environment("bad", "file:///tmp")
    with pytest.raises(KeyError):
        x.revoke_key("missing")


@pytest.mark.asyncio
async def test_extension_http_contracts():
    import httpx

    from llm_budget_gateway.console_api import create_console_app

    app = create_console_app(
        product_connection=sqlite3.connect(":memory:", check_same_thread=False)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://console"
    ) as client:
        assert (
            await client.post("/v1/product/applications/a1/keys/rotate")
        ).status_code == 200
        assert (
            await client.put("/v1/product/budgets/global", json={"limit_usd": 100})
        ).status_code == 200
        assert (
            await client.post(
                "/v1/product/alerts",
                json={"name": "cost", "metric": "cost", "threshold": 80},
            )
        ).status_code == 201
        assert (
            await client.post(
                "/v1/product/environments",
                json={
                    "name": "dev",
                    "base_url": "http://localhost:8000/v1",
                    "default": True,
                },
            )
        ).status_code == 201
        assert (
            await client.post(
                "/v1/product/views",
                json={
                    "name": "mine",
                    "role": "operator",
                    "filters": {"status": "error"},
                },
            )
        ).status_code == 201
        assert (await client.get("/v1/product/export")).status_code == 200
        assert (await client.get("/v1/product/recommendations")).status_code == 200
        assert (await client.get("/v1/product/audit")).status_code == 200
