import httpx
import pytest

from llm_budget_gateway.operations_api import create_operations_app


@pytest.mark.asyncio
async def test_operations_api_all_user_flows(tmp_path):
    app = create_operations_app(str(tmp_path / "o.db"), "secret")
    tr = httpx.ASGITransport(app=app)
    h = {"Authorization": "Bearer secret", "X-Tenant-Id": "acme"}
    async with httpx.AsyncClient(transport=tr, base_url="http://test") as c:
        assert (await c.post("/v1/operations/prompts", json={})).status_code == 401
        first = (
            await c.post(
                "/v1/operations/prompts",
                headers=h,
                json={"name": "support", "template": "Hello"},
            )
        ).json()
        assert first["version"] == 1
        await c.post(
            "/v1/operations/prompts",
            headers=h,
            json={"name": "support", "template": "Hi"},
        )
        assert (
            len(
                (await c.get("/v1/operations/prompts/support", headers=h)).json()[
                    "items"
                ]
            )
            == 2
        )
        assigned = (
            await c.post(
                "/v1/operations/prompts/support/assign",
                headers=h,
                json={"subject": "u1", "versions": [1, 2]},
            )
        ).json()
        assert assigned["version"] in [1, 2]
        retry = (
            await c.post(
                "/v1/operations/retry-decisions",
                headers=h,
                json={"attempt": 1, "elapsed_ms": 0, "status_code": 503},
            )
        ).json()
        assert retry["retry"]
        quota = (
            await c.post(
                "/v1/operations/quota-diagnostics",
                headers=h,
                json={"status_code": 429, "code": "insufficient_quota"},
            )
        ).json()
        assert quota["category"] == "financial_quota"
        models = (
            await c.post(
                "/v1/operations/model-catalog/normalize",
                headers=h,
                json={"models": [{"id": "m", "context_window": 10}]},
            )
        ).json()
        assert models["data"][0]["id"] == "m"
        slo = (
            await c.post(
                "/v1/operations/slo", headers=h, json={"total": 100, "failed": 0}
            )
        ).json()
        assert slo["state"] == "healthy"


@pytest.mark.asyncio
async def test_dashboard_e2e_ui_and_unconfigured_auth(tmp_path):
    app = create_operations_app(str(tmp_path / "o.db"), "")
    tr = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=tr, base_url="http://test") as c:
        page = await c.get("/operations")
        assert page.status_code == 200
        for token in (
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
            assert token in page.text
        assert (
            await c.post("/v1/operations/slo", json={"total": 1, "failed": 0})
        ).status_code == 503


def test_openapi_contains_all_operations_routes(tmp_path):
    paths = set(create_operations_app(str(tmp_path / "o.db"), "k").openapi()["paths"])
    assert {
        "/operations",
        "/v1/operations/prompts",
        "/v1/operations/prompts/{name}",
        "/v1/operations/prompts/{name}/assign",
        "/v1/operations/retry-decisions",
        "/v1/operations/quota-diagnostics",
        "/v1/operations/model-catalog/normalize",
        "/v1/operations/slo",
    } <= paths
