import httpx
import pytest

from llm_budget_gateway.evaluation_api import create_evaluation_app


@pytest.mark.asyncio
async def test_quality_api_all_user_flows(tmp_path):
    app = create_evaluation_app(str(tmp_path / "q.db"), "secret")
    tr = httpx.ASGITransport(app=app)
    h = {"Authorization": "Bearer secret", "X-Tenant-Id": "acme"}
    async with httpx.AsyncClient(transport=tr, base_url="http://test") as c:
        assert (await c.post("/v1/quality/evaluations", json={})).status_code == 401
        ev = (
            await c.post(
                "/v1/quality/evaluations",
                headers=h,
                json={"name": "smoke", "output": "hello", "rules": {"equals": "hello"}},
            )
        ).json()
        assert ev["passed"]
        assert (
            len((await c.get("/v1/quality/evaluations", headers=h)).json()["items"])
            == 1
        )
        gate = (
            await c.post(
                "/v1/quality/release-gates",
                headers=h,
                json={"scores": [0.9, 1], "minimum": 0.8, "max_regression": 0.1},
            )
        ).json()
        assert gate["passed"]
        trace = (
            await c.post(
                "/v1/quality/traces/resolve",
                headers=h,
                json={"headers": {"X-Gateway-Trace-Id": "trace_123"}},
            )
        ).json()
        assert trace["trace_id"] == "trace_123"
        batch = (
            await c.post(
                "/v1/quality/batches/manifest",
                headers=h,
                json={
                    "requests": [{"custom_id": "1", "model": "m", "estimated_cost": 2}]
                },
            )
        ).json()
        assert batch["count"] == 1
        report = (
            await c.post(
                "/v1/quality/audit-reports",
                headers=h,
                json={"findings": [{"result": "pass"}], "generated_at": 10},
            )
        ).json()
        assert report["sha256"]
        verify = (
            await c.post("/v1/quality/audit-reports/verify", headers=h, json=report)
        ).json()
        assert verify["valid"]


@pytest.mark.asyncio
async def test_quality_dashboard_and_unconfigured_auth(tmp_path):
    app = create_evaluation_app(str(tmp_path / "q.db"), "")
    tr = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=tr, base_url="http://test") as c:
        page = await c.get("/quality")
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
        assert (await c.get("/v1/quality/evaluations")).status_code == 503


def test_quality_openapi_contract(tmp_path):
    paths = set(create_evaluation_app(str(tmp_path / "q.db"), "k").openapi()["paths"])
    assert {
        "/quality",
        "/v1/quality/evaluations",
        "/v1/quality/release-gates",
        "/v1/quality/traces/resolve",
        "/v1/quality/batches/manifest",
        "/v1/quality/audit-reports",
        "/v1/quality/audit-reports/verify",
    } <= paths
