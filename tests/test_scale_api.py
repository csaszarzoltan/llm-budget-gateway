import httpx
import pytest

from llm_budget_gateway.scale_api import create_scale_app


@pytest.mark.asyncio
async def test_scale_api_auth_dispatch_validation_unknown_and_health():
    app = create_scale_app("k")
    h = {"Authorization": "Bearer k", "X-Tenant-Id": "t"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://x"
    ) as c:
        assert (await c.get("/health")).json() == {"status": "ok"}
        assert (
            await c.post("/v1/scale/replication-quorum", json={})
        ).status_code == 401
        r = await c.post(
            "/v1/scale/replication-quorum",
            headers=h,
            json={"replicas": 3, "available": 2},
        )
        assert r.status_code == 200 and r.json()["writable"]
        assert (
            await c.post("/v1/scale/missing", headers=h, json={})
        ).status_code == 404
        assert (
            await c.post("/v1/scale/replication-quorum", headers=h, json={})
        ).status_code == 422


@pytest.mark.asyncio
async def test_scale_api_fails_closed_without_server_key():
    app = create_scale_app("")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://x"
    ) as c:
        assert (
            await c.post(
                "/v1/scale/replication-quorum",
                headers={"Authorization": "Bearer x", "X-Tenant-Id": "t"},
                json={},
            )
        ).status_code == 503
