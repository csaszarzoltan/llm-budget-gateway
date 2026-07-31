import base64
import hashlib
import hmac

import httpx
import pytest

from llm_budget_gateway.agentops_api import create_agentops_app


def payloads():
    body = b"x"
    sig = hmac.new(b"s", b"1." + body, hashlib.sha256).hexdigest()
    return {
        "mcp-registry": {"name": "m", "url": "https://x", "tools": ["a"]},
        "tool-access": {"tool": "a", "allowed": ["a"], "denied": []},
        "delegation-depth": {"current_depth": 0, "maximum_depth": 1},
        "task-lease": {"owner": "a", "claimant": "a", "expires_at": 2, "now": 1},
        "replay-protection": {
            "body_b64": base64.b64encode(body).decode(),
            "timestamp": 1,
            "now": 1,
            "signature": sig,
            "secret": "s",
        },
        "session-affinity": {"session_id": "s", "backends": ["a"]},
        "circuit-breaker": {
            "failures": 0,
            "threshold": 1,
            "opened_at": None,
            "now": 1,
            "cooldown": 1,
        },
        "semantic-cache": {"text": "x", "model": "m", "namespace": "n"},
        "redact": {"text": "safe"},
        "injection-risk": {"text": "safe"},
        "human-approval": {"action": "read", "impact": "low", "approved_by": None},
        "audit-chain": {"previous_hash": "", "event": {"a": 1}},
        "trace-sampling": {"trace_id": "x", "rate": 1},
        "task-cost": {
            "input_tokens": 1,
            "output_tokens": 1,
            "input_rate": 1,
            "output_rate": 1,
            "steps": 1,
        },
        "token-density": {"useful_units": 1, "total_tokens": 1},
        "carbon": {"energy_kwh": 1, "grams_per_kwh": 1},
        "change-risk": {"changed_files": 1, "criticality": 1, "coverage": 1},
        "support-triage": {"severity": 1, "affected_users": 0, "workaround": True},
        "locale": {"requested": ["en"], "supported": ["en"], "default": "en"},
        "residency": {"source": "eu", "destination": "eu", "allowed_pairs": []},
    }


@pytest.mark.asyncio
async def test_all_api_flows():
    app = create_agentops_app("k")
    tr = httpx.ASGITransport(app=app)
    h = {"Authorization": "Bearer k", "X-Tenant-Id": "t"}
    async with httpx.AsyncClient(transport=tr, base_url="http://x") as c:
        assert (await c.post("/v1/agentops/mcp-registry", json={})).status_code == 401
        for name, payload in payloads().items():
            assert (
                await c.post("/v1/agentops/" + name, headers=h, json=payload)
            ).status_code == 200, name
        assert (
            await c.post("/v1/agentops/missing", headers=h, json={})
        ).status_code == 404
        assert (
            await c.post("/v1/agentops/task-cost", headers=h, json={})
        ).status_code == 422


@pytest.mark.asyncio
async def test_ui_and_fail_closed():
    app = create_agentops_app("")
    tr = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=tr, base_url="http://x") as c:
        page = await c.get("/agentops")
        assert page.status_code == 200
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
            assert x in page.text
        assert (await c.post("/v1/agentops/task-cost", json={})).status_code == 503


def test_openapi():
    assert "/v1/agentops/{capability}" in create_agentops_app("k").openapi()["paths"]
