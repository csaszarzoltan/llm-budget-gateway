import httpx
import pytest

from llm_budget_gateway.resilience_api import create_resilience_app


@pytest.mark.asyncio
async def test_all_flows(tmp_path):
    app = create_resilience_app(str(tmp_path / "r.db"), "k")
    tr = httpx.ASGITransport(app=app)
    h = {"Authorization": "Bearer k", "X-Tenant-Id": "t"}
    async with httpx.AsyncClient(transport=tr, base_url="http://x") as c:
        assert (await c.post("/v1/resilience/concurrency", json={})).status_code == 401
        assert (
            await c.post(
                "/v1/resilience/concurrency",
                headers=h,
                json={"current": 5, "p95_ms": 50, "error_rate": 0, "target_ms": 100},
            )
        ).json()["limit"] == 6
        item = (
            await c.post(
                "/v1/resilience/dead-letters",
                headers=h,
                json={"payload": {"job": 1}, "error": "x"},
            )
        ).json()
        assert (
            await c.post(f"/v1/resilience/dead-letters/{item['id']}/replay", headers=h)
        ).json()["state"] == "replayed"
        assert (
            "active"
            in (
                await c.post(
                    "/v1/resilience/maintenance",
                    headers=h,
                    json={
                        "weekday": 0,
                        "start_minute": 0,
                        "duration_minutes": 1,
                        "now_epoch": 0,
                    },
                )
            ).json()
        )
        assert not (
            await c.post("/v1/resilience/config/diagnose", headers=h, json={})
        ).json()["valid"]
        assert (
            await c.post(
                "/v1/resilience/incidents/timeline",
                headers=h,
                json={"events": [{"timestamp": 1, "kind": "outage"}]},
            )
        ).json()["severity"] == "high"


@pytest.mark.asyncio
async def test_ui_fail_closed(tmp_path):
    app = create_resilience_app(str(tmp_path / "r.db"), "")
    tr = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=tr, base_url="http://x") as c:
        p = await c.get("/resilience")
        assert p.status_code == 200
        for x in (
            "prefers-color-scheme:dark",
            "@media(max-width:560px)",
            "focus-visible",
            "aria-live",
            "skeleton",
            "empty",
            "error",
            "toast",
            "Skip to main content",
        ):
            assert x in p.text
        assert (
            await c.post("/v1/resilience/config/diagnose", json={})
        ).status_code == 503


def test_openapi(tmp_path):
    paths = set(create_resilience_app(str(tmp_path / "r.db"), "k").openapi()["paths"])
    assert len([x for x in paths if x.startswith("/v1/resilience")]) == 6
