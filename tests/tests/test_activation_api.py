import httpx
import pytest

from llm_budget_gateway.activation_api import create_activation_app


@pytest.mark.asyncio
async def test_activation_api_auth_dispatch_errors_and_health():
    app = create_activation_app("k")
    h = {"Authorization": "Bearer k", "X-Tenant-Id": "t"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://x"
    ) as c:
        assert (await c.get("/health")).json() == {"status": "ok"}
        assert (
            await c.post("/v1/activation/setup-progress", json={})
        ).status_code == 401
        r = await c.post(
            "/v1/activation/service-profile", headers=h, json={"profile": "developer"}
        )
        assert r.status_code == 200 and r.json()["count"] == 3
        assert (
            await c.post("/v1/activation/missing", headers=h, json={})
        ).status_code == 404
        assert (
            await c.post("/v1/activation/service-profile", headers=h, json={})
        ).status_code == 422


@pytest.mark.asyncio
async def test_activation_api_fails_closed_without_key():
    app = create_activation_app("")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://x"
    ) as c:
        assert (
            await c.post(
                "/v1/activation/setup-progress",
                headers={"Authorization": "Bearer x", "X-Tenant-Id": "t"},
                json={},
            )
        ).status_code == 503
