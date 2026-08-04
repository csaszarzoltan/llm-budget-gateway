import httpx
import pytest

from llm_budget_gateway.fleet_api import create_fleet_app

PAYLOADS = {
    "identity": {"agent_id": "agent-1", "owner": "o", "purpose": "p", "expires_at": 1},
    "inventory": {"agents": [{"id": "a", "owner": "o", "sanctioned": True}]},
    "lifecycle": {"current": "draft", "proposed": "active"},
    "credential-expiry": {"expires_at": 2, "now": 1, "renewal_window": 0},
    "capability-grant": {
        "capability": "r",
        "granted": ["r"],
        "resource": "x",
        "resources": ["x"],
        "expires_at": 2,
        "now": 1,
    },
    "platform-authorization": {
        "platform": "p",
        "approved_platforms": ["p"],
        "terms_version": "1",
        "accepted_versions": {"p": "1"},
    },
    "kill-switch": {"agent": "a", "team": "t", "stopped": []},
    "policy-simulation": {"current": [True], "proposed": [True]},
    "blast-radius": {"users": 1, "write_systems": 1, "autonomy": 1},
    "responsibility": {"agent_owner": "a", "workflow_owner": "w", "approver": None},
    "evidence": {"artifacts": {"a": "x"}},
    "policy-coverage": {"total": 1, "governed": 1, "observed": 1},
    "shadow-agents": {"observed": ["a"], "registered": ["a"]},
    "cost-ceiling": {"spent": 1, "estimated_next": 1, "ceiling": 2},
    "runaway": {
        "steps": 1,
        "retries": 0,
        "repeated_tool_calls": 0,
        "limits": {"steps": 2, "retries": 2, "repeated_tool_calls": 2},
    },
    "outcome-economics": {
        "total_cost": 1,
        "completed_outcomes": 1,
        "value_per_outcome": 2,
    },
    "model-tier": {
        "complexity": 0.5,
        "tiers": [{"name": "s", "max_complexity": 1, "cost": 1}],
    },
    "tool-costs": {"calls": [{"tool": "x", "cost": 1}]},
    "data-readiness": {"fresh": 1, "permissioned": 1, "classified": 1},
    "reproducibility": {
        "prompt_version": "1",
        "model": "m",
        "tools": ["t"],
        "policy_version": "1",
    },
    "compliance": {"requirements": {"r": ["c"]}, "controls": ["c"]},
}


@pytest.mark.asyncio
async def test_api_flows():
    app = create_fleet_app("k")
    tr = httpx.ASGITransport(app=app)
    h = {"Authorization": "Bearer k", "X-Tenant-Id": "t"}
    async with httpx.AsyncClient(transport=tr, base_url="http://x") as c:
        assert (await c.post("/v1/fleet/identity", json={})).status_code == 401
        for name, payload in PAYLOADS.items():
            assert (
                await c.post("/v1/fleet/" + name, headers=h, json=payload)
            ).status_code == 200, name
        assert (
            await c.post("/v1/fleet/missing", headers=h, json={})
        ).status_code == 404
        assert (
            await c.post("/v1/fleet/identity", headers=h, json={})
        ).status_code == 422


@pytest.mark.asyncio
async def test_ui_and_fail_closed():
    app = create_fleet_app("")
    tr = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=tr, base_url="http://x") as c:
        p = await c.get("/fleet")
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
        assert (await c.post("/v1/fleet/identity", json={})).status_code == 503


def test_openapi():
    assert "/v1/fleet/{capability}" in create_fleet_app("k").openapi()["paths"]
