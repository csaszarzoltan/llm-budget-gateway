import httpx
import pytest

from llm_budget_gateway.security_api import create_security_app


@pytest.mark.asyncio
async def test_all_security_flows(tmp_path):
    app = create_security_app(str(tmp_path / "s.db"), "k")
    tr = httpx.ASGITransport(app=app)
    h = {"Authorization": "Bearer k", "X-Tenant-Id": "t"}
    async with httpx.AsyncClient(transport=tr, base_url="http://x") as c:
        assert (await c.post("/v1/security/posture", json={})).status_code == 401
        assert (
            await c.post(
                "/v1/security/secrets/scan", headers=h, json={"text": "sk_abcdefgh"}
            )
        ).json()["count"] == 1
        assert (
            await c.post(
                "/v1/security/replays/reserve",
                headers=h,
                json={"event_id": "e", "ttl": 10},
            )
        ).json()["accepted"]
        assert not (
            await c.post(
                "/v1/security/providers/evaluate",
                headers=h,
                json={"provider": {"name": "p"}, "requirements": {"gdpr": True}},
            )
        ).json()["allowed"]
        assert (
            await c.post(
                "/v1/security/changes/assess", headers=h, json={"changes": ["auth"]}
            )
        ).json()["severity"] in {"high", "medium"}
        assert (
            await c.post(
                "/v1/security/posture", headers=h, json={"auth_configured": True}
            )
        ).json()["score"] > 0


@pytest.mark.asyncio
async def test_ui_and_fail_closed(tmp_path):
    app = create_security_app(str(tmp_path / "s.db"), "")
    tr = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=tr, base_url="http://x") as c:
        page = await c.get("/security")
        assert page.status_code == 200
        for x in (
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
            assert x in page.text
        assert (await c.post("/v1/security/posture", json={})).status_code == 503


def test_openapi(tmp_path):
    paths = set(create_security_app(str(tmp_path / "s.db"), "k").openapi()["paths"])
    assert {
        "/security",
        "/v1/security/secrets/scan",
        "/v1/security/replays/reserve",
        "/v1/security/providers/evaluate",
        "/v1/security/changes/assess",
        "/v1/security/posture",
    } <= paths
