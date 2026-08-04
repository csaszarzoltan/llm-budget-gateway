import httpx
import pytest

from llm_budget_gateway.delivery_api import create_delivery_app


@pytest.mark.asyncio
async def test_delivery_api_auth_success_errors_and_health():
    app = create_delivery_app("k")
    h = {"Authorization": "Bearer k", "X-Tenant-Id": "t"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://x"
    ) as c:
        assert (await c.get("/health")).json() == {"status": "ok"}
        assert (
            await c.post("/v1/delivery/environment-readiness", json={})
        ).status_code == 401
        r = await c.post(
            "/v1/delivery/environment-readiness",
            headers=h,
            json={"required": ["A"], "configured": ["A"]},
        )
        assert r.status_code == 200 and r.json()["ready"]
        assert (
            await c.post("/v1/delivery/missing", headers=h, json={})
        ).status_code == 404
        assert (
            await c.post("/v1/delivery/capacity-plan", headers=h, json={})
        ).status_code == 422


@pytest.mark.asyncio
async def test_delivery_api_fails_closed_without_server_key():
    app = create_delivery_app("")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://x"
    ) as c:
        assert (
            await c.post(
                "/v1/delivery/environment-readiness",
                headers={"Authorization": "Bearer x", "X-Tenant-Id": "t"},
                json={},
            )
        ).status_code == 503
