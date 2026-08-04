"""Route Studio lifecycle, dependency, version and simulation contracts."""

from __future__ import annotations

import sqlite3

import httpx
import pytest

from llm_budget_gateway.console_api import create_console_app
from llm_budget_gateway.product_console import ProductConsoleStore


def target(model: str, priority: int = 10) -> dict[str, object]:
    return {
        "model": model,
        "priority": priority,
        "timezone": "Europe/Zurich",
        "start": "00:00",
        "end": "23:59",
        "required_capabilities": [],
    }


def test_archive_restore_delete_and_dependencies() -> None:
    store = ProductConsoleStore(sqlite3.connect(":memory:"))
    route = store.create_route("support-global", [target("@a/model")])
    app = store.create_application("Support", "support-global")
    deps = store.route_dependencies(route["id"])
    assert deps["blocking"] is True and deps["applications"][0]["id"] == app["id"]
    with pytest.raises(ValueError, match="dependencies"):
        store.archive_route(route["id"])
    store.db.execute(
        "UPDATE pc_apps SET default_route='replacement' WHERE id=?", (app["id"],)
    )
    store.db.commit()
    archived = store.archive_route(route["id"])
    assert archived["status"] == "archived"
    assert store.restore_route(route["id"])["status"] == "draft"
    store.archive_route(route["id"])
    with pytest.raises(ValueError, match="confirmation"):
        store.delete_route(route["id"], confirmation="wrong")
    store.delete_route(route["id"], confirmation="support-global")
    with pytest.raises(KeyError):
        store.route(route["id"])


def test_duplicate_route_and_version_history() -> None:
    store = ProductConsoleStore(sqlite3.connect(":memory:"))
    route = store.create_route("coding", [target("@a/cheap")])
    store.update_route(route["id"], [target("@a/premium"), target("@b/fallback", 20)])
    history = store.route_versions(route["id"])
    assert [x["version"] for x in history] == [2, 1]
    copy = store.duplicate_route(route["id"], "coding-copy")
    assert copy["name"] == "coding-copy" and len(copy["targets"]) == 2


def test_route_validation_and_simulation() -> None:
    store = ProductConsoleStore(sqlite3.connect(":memory:"))
    route = store.create_route(
        "eu-support", [target("@eu/primary"), target("@eu/fallback", 20)]
    )
    validation = store.validate_route(route["id"])
    assert validation["valid"] is True and validation["estimated_daily_cost_usd"] >= 0
    simulation = store.simulate_route(
        route["id"], capabilities=[], budget_remaining_usd=10
    )
    assert simulation["selected_model"] == "@eu/primary"
    assert simulation["decision_path"][0]["kind"] == "target"


@pytest.mark.asyncio
async def test_route_lifecycle_api_integration() -> None:
    app = create_console_app(
        product_connection=sqlite3.connect(":memory:", check_same_thread=False)
    )
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/v1/product/routes",
            json={"name": "studio-route", "targets": [target("@a/model")]},
        )
        rid = created.json()["id"]
        assert (
            await client.get(f"/v1/product/routes/{rid}/versions")
        ).status_code == 200
        assert (await client.post(f"/v1/product/routes/{rid}/validate")).json()[
            "valid"
        ] is True
        assert (
            await client.post(
                f"/v1/product/routes/{rid}/simulate",
                json={"capabilities": [], "budget_remaining_usd": 1},
            )
        ).status_code == 200
        assert (
            await client.post(f"/v1/product/routes/{rid}/archive")
        ).status_code == 200
        assert (
            await client.post(f"/v1/product/routes/{rid}/restore")
        ).status_code == 200
        await client.post(f"/v1/product/routes/{rid}/archive")
        deleted = await client.request(
            "DELETE", f"/v1/product/routes/{rid}", json={"confirmation": "studio-route"}
        )
        assert deleted.status_code == 204
