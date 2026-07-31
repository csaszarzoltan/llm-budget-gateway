import httpx
import pytest

from llm_budget_gateway.assurance_api import create_assurance_app

P = {
    "risk-tier": {"impact": 1, "autonomy": 0, "sensitive": False},
    "control-test": {"passed": 1, "total": 1},
    "evaluation-gate": {"metrics": {"q": 1}, "thresholds": {"q": 0.9}},
    "calibration": {"confidence": [1], "correct": [True]},
    "refusal-quality": {"expected": [True], "actual": [True]},
    "fairness-gap": {"rates": {"a": 1, "b": 1}},
    "robustness": {"baseline": 1, "perturbed": 1},
    "hallucination-rate": {"unsupported": 0, "claims": 1},
    "provenance": {"model": "m", "prompt": "p", "dataset": "d", "policy": "x"},
    "change-approval": {"risk": "low", "approvers": []},
    "incident-severity": {"users": 0, "data_exposure": False, "financial_loss": 0},
    "corrective-action": {"completed": 1, "total": 1, "overdue": 0},
    "vendor-risk": {"security": 5, "transparency": 5, "resilience": 5},
    "data-quality": {"completeness": 1, "freshness": 1, "validity": 1},
    "drift-alert": {"baseline": 1, "current": 1, "tolerance": 0},
    "red-team-coverage": {"tested": ["a"], "required": ["a"]},
    "evidence-freshness": {"collected_at": 0, "now": 0, "max_age": 1},
    "maturity": {"domains": {"a": 5}},
    "assurance-report": {"findings": []},
    "benefit-realization": {"planned": 1, "realized": 1, "cost": 0},
}


@pytest.mark.asyncio
async def test_all_api_flows():
    app = create_assurance_app("k")
    tr = httpx.ASGITransport(app=app)
    h = {"Authorization": "Bearer k", "X-Tenant-Id": "t"}
    async with httpx.AsyncClient(transport=tr, base_url="http://x") as c:
        assert (await c.post("/v1/assurance/risk-tier", json={})).status_code == 401
        for n, p in P.items():
            assert (
                await c.post("/v1/assurance/" + n, headers=h, json=p)
            ).status_code == 200, n
        assert (
            await c.post("/v1/assurance/missing", headers=h, json={})
        ).status_code == 404
        assert (
            await c.post("/v1/assurance/risk-tier", headers=h, json={})
        ).status_code == 422


@pytest.mark.asyncio
async def test_ui_and_fail_closed():
    app = create_assurance_app("")
    tr = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=tr, base_url="http://x") as c:
        p = await c.get("/assurance")
        assert p.status_code == 200
        for x in (
            "data-theme=dark",
            "@media(max-width:560px)",
            "focus-visible",
            "aria-live",
            "skeleton",
            "empty",
            "error",
            "toast",
            "Skip to main content",
            "Theme changed",
        ):
            assert x in p.text
        assert (await c.post("/v1/assurance/risk-tier", json={})).status_code == 503


def test_openapi():
    assert "/v1/assurance/{capability}" in create_assurance_app("k").openapi()["paths"]
