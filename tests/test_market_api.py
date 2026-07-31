import httpx
import pytest

from llm_budget_gateway.market_api import create_market_app


@pytest.mark.asyncio
async def test_all_api_flows_and_auth(tmp_path):
    app = create_market_app(str(tmp_path / "m.db"), "secret")
    t = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=t, base_url="http://test") as c:
        assert (
            await c.post("/v1/intelligence/redact", json={"text": "a@b.com"})
        ).status_code == 401
        h = {"X-Tenant-Id": "acme"}
        red = (
            await c.post("/v1/intelligence/redact", headers=h, json={"text": "a@b.com"})
        ).json()
        assert red["count"] == 1
        req = {
            "request": {"model": "m", "prompt": "hi"},
            "response": {"answer": "ok"},
            "ttl": 60,
        }
        assert (
            await c.post("/v1/intelligence/cache", headers=h, json=req)
        ).status_code == 200
        lookup = (
            await c.post(
                "/v1/intelligence/cache/lookup",
                headers=h,
                json={"request": req["request"]},
            )
        ).json()
        assert lookup == {"hit": True, "value": {"answer": "ok"}}
        hook = (
            await c.post(
                "/v1/intelligence/webhooks/sign",
                headers=h,
                json={"event": "cost.spike", "payload": {"cost": 4}, "timestamp": 10},
            )
        ).json()
        assert hook["signature"].startswith("sha256=")
        anomaly = (
            await c.post(
                "/v1/intelligence/anomalies",
                headers=h,
                json={"history": [1, 1, 1], "current": 4},
            )
        ).json()
        assert anomaly["anomaly"] is True
        route = (
            await c.post(
                "/v1/intelligence/route",
                headers=h,
                json={
                    "candidates": [
                        {"model": "m", "cost": 1, "quality": 0.9, "latency_ms": 50}
                    ],
                    "min_quality": 0.8,
                },
            )
        ).json()
        assert route["model"] == "m"


@pytest.mark.asyncio
async def test_dashboard_e2e_accessibility_contract(tmp_path):
    app = create_market_app(str(tmp_path / "m.db"))
    t = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=t, base_url="http://test") as c:
        r = await c.get("/intelligence")
        assert r.status_code == 200
        for token in (
            "prefers-color-scheme:dark",
            "@media(max-width:560px)",
            "aria-live",
            "focus-visible",
            "skeleton",
            "empty",
            "error",
            "toast",
            "Skip to main content",
        ):
            assert token in r.text


def test_openapi_lists_every_new_endpoint(tmp_path):
    paths = create_market_app(str(tmp_path / "m.db")).openapi()["paths"]
    assert {
        "/v1/intelligence/redact",
        "/v1/intelligence/cache",
        "/v1/intelligence/cache/lookup",
        "/v1/intelligence/webhooks/sign",
        "/v1/intelligence/anomalies",
        "/v1/intelligence/route",
        "/intelligence",
    } <= set(paths)
