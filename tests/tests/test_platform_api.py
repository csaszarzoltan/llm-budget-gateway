import base64

import httpx
import pytest

from llm_budget_gateway.platform_api import create_platform_app

PAYLOADS = {
    "prompt-catalog": {"name": "p", "version": "1.0.0", "environments": ["prod"]},
    "model-catalog": {
        "name": "m",
        "context_window": 1,
        "capabilities": ["chat"],
        "external": True,
    },
    "usage-tags": {"tags": {"team": "a"}},
    "cost-allocation": {"total": 1, "weights": {"a": 1}},
    "quota-plan": {
        "request_limit": 1,
        "token_limit": 1,
        "expected_requests": 1,
        "expected_tokens": 1,
    },
    "alert-rule": {"metric": 2, "operator": ">", "threshold": 1},
    "slo": {"total": 1, "failures": 0, "target": 0.9},
    "incident-digest": {"events": [{"timestamp": 1, "kind": "outage"}]},
    "retention": {"created_at": 0, "days": 1, "legal_hold": False},
    "dlp": {"text": "safe"},
    "region-route": {
        "providers": [{"name": "p", "healthy": True, "region": "eu", "latency_ms": 1}],
        "allowed_regions": ["eu"],
    },
    "provider-score": {"cost": 0, "latency": 0, "quality": 1, "reliability": 1},
    "canary-plan": {"percentages": [100]},
    "rollback": {"quality_delta": 0, "error_rate": 0, "latency_delta": 0},
    "feedback": {"ratings": [5]},
    "quality-drift": {"baseline": [1], "current": [1], "tolerance": 0},
    "dataset-curate": {"examples": [{"x": 1}]},
    "export-manifest": {"files": {"a": base64.b64encode(b"x").decode()}},
    "contract": {"previous": ["a"], "proposed": ["a"]},
    "adoption-funnel": {"stages": {"view": 1, "use": 1}},
}


@pytest.mark.asyncio
async def test_all_twenty_api_flows_and_errors():
    app = create_platform_app("k")
    tr = httpx.ASGITransport(app=app)
    h = {"Authorization": "Bearer k", "X-Tenant-Id": "t"}
    async with httpx.AsyncClient(transport=tr, base_url="http://x") as c:
        assert (await c.post("/v1/platform/slo", json={})).status_code == 401
        for name, payload in PAYLOADS.items():
            assert (
                await c.post("/v1/platform/" + name, headers=h, json=payload)
            ).status_code == 200, name
        assert (
            await c.post("/v1/platform/missing", headers=h, json={})
        ).status_code == 404
        assert (await c.post("/v1/platform/slo", headers=h, json={})).status_code == 422


@pytest.mark.asyncio
async def test_responsive_accessible_ui_and_fail_closed():
    app = create_platform_app("")
    tr = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=tr, base_url="http://x") as c:
        page = await c.get("/platform")
        assert page.status_code == 200
        for token in (
            "data-theme=dark",
            "@media(max-width:580px)",
            "focus-visible",
            "aria-live",
            "skeleton",
            "empty",
            "error",
            "toast",
            "Skip to main content",
            "Theme changed",
        ):
            assert token in page.text
        assert (await c.post("/v1/platform/slo", json={})).status_code == 503


def test_openapi_contract():
    paths = create_platform_app("k").openapi()["paths"]
    assert "/v1/platform/{capability}" in paths and "/platform" in paths
