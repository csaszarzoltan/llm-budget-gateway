import httpx
import pytest

from llm_budget_gateway.adoption_api import create_adoption_app


@pytest.mark.asyncio
async def test_adoption_api_auth_dispatch_validation_unknown_and_health():
    app = create_adoption_app("k")
    h = {"Authorization": "Bearer k", "X-Tenant-Id": "t"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://x"
    ) as c:
        assert (await c.get("/health")).json() == {"status": "ok"}
        assert (
            await c.post("/v1/adoption/cohort-retention", json={})
        ).status_code == 401
        r = await c.post(
            "/v1/adoption/cohort-retention",
            headers=h,
            json={"cohort_size": 10, "active_users": 8},
        )
        assert r.status_code == 200 and r.json()["retention"] == 0.8
        assert (
            await c.post("/v1/adoption/missing", headers=h, json={})
        ).status_code == 404
        assert (
            await c.post("/v1/adoption/cohort-retention", headers=h, json={})
        ).status_code == 422


@pytest.mark.asyncio
async def test_adoption_api_fails_closed_without_key():
    app = create_adoption_app("")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://x"
    ) as c:
        assert (
            await c.post(
                "/v1/adoption/cohort-retention",
                headers={"Authorization": "Bearer x", "X-Tenant-Id": "t"},
                json={},
            )
        ).status_code == 503
