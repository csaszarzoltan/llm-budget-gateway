import httpx
import pytest

from llm_budget_gateway.optimization_api import create_optimization_app


@pytest.mark.asyncio
async def test_all_flows(tmp_path):
    app = create_optimization_app(str(tmp_path / "o.db"), "k")
    tr = httpx.ASGITransport(app=app)
    h = {"Authorization": "Bearer k", "X-Tenant-Id": "t"}
    async with httpx.AsyncClient(transport=tr, base_url="http://x") as c:
        assert (
            await c.post("/v1/optimization/prompts/compress", json={})
        ).status_code == 401
        assert (
            await c.post(
                "/v1/optimization/prompts/compress", headers=h, json={"text": "a  b"}
            )
        ).json()["text"] == "a b"
        assert (
            await c.post(
                "/v1/optimization/savings/attribute",
                headers=h,
                json={"baseline_cost": 10, "actual_cost": 8, "drivers": {"cache": 2}},
            )
        ).json()["realized_savings"] == 2
        assert (
            await c.post(
                "/v1/optimization/cache/recommend",
                headers=h,
                json={"reuse_probability": 0.8, "volatility": 0.2, "sensitive": False},
            )
        ).json()["cache"]
        assert (
            await c.post(
                "/v1/optimization/budget/forecast",
                headers=h,
                json={
                    "daily_costs": [1],
                    "elapsed_days": 1,
                    "period_days": 30,
                    "budget": 50,
                },
            )
        ).json()["risk"] == "healthy"
        await c.post(
            "/v1/optimization/experiments",
            headers=h,
            json={"name": "r", "variant": "a", "cost": 1, "latency": 1, "quality": 1},
        )
        assert (
            await c.post(
                "/v1/optimization/experiments/r/winner",
                headers=h,
                json={"minimum_quality": 0.9},
            )
        ).json()["winner"]["variant"] == "a"


@pytest.mark.asyncio
async def test_ui_fail_closed(tmp_path):
    app = create_optimization_app(str(tmp_path / "o.db"), "")
    tr = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=tr, base_url="http://x") as c:
        x = await c.get("/optimization")
        assert x.status_code == 200
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
            assert token in x.text
        assert (
            await c.post("/v1/optimization/budget/forecast", json={})
        ).status_code == 503


def test_openapi(tmp_path):
    paths = set(create_optimization_app(str(tmp_path / "o.db"), "k").openapi()["paths"])
    assert len([x for x in paths if x.startswith("/v1/optimization")]) == 6
