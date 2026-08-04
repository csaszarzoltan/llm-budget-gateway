import httpx
import pytest

from llm_budget_gateway.collaboration_api import create_collaboration_app


@pytest.mark.asyncio
async def test_flows(tmp_path):
    app = create_collaboration_app(str(tmp_path / "c.db"), "k")
    tr = httpx.ASGITransport(app=app)
    h = {"Authorization": "Bearer k", "X-Tenant-Id": "t"}
    async with httpx.AsyncClient(transport=tr, base_url="http://x") as c:
        assert (
            await c.post("/v1/collaboration/roles/authorize", json={})
        ).status_code == 401
        assert (
            await c.post(
                "/v1/collaboration/roles/authorize",
                headers=h,
                json={"role": "admin", "permission": "members:write", "scopes": ["*"]},
            )
        ).json()["allowed"]
        inv = (
            await c.post(
                "/v1/collaboration/invitations",
                headers=h,
                json={"email": "a@b.com", "role": "developer"},
            )
        ).json()
        assert (
            await c.post(
                "/v1/collaboration/invitations/accept", json={"token": inv["token"]}
            )
        ).status_code == 200
        assert (
            await c.post(
                "/v1/collaboration/keys/lifecycle",
                headers=h,
                json={"created_at": 0, "last_used_at": 0, "now": 1},
            )
        ).json()["action"] == "keep"
        assert (
            await c.post(
                "/v1/collaboration/members/budget",
                headers=h,
                json={
                    "spent": 1,
                    "request_cost": 1,
                    "limit": 10,
                    "active_keys": 1,
                    "max_keys": 2,
                },
            )
        ).json()["request_allowed"]
        assert (
            await c.post(
                "/v1/collaboration/approvals/delegate",
                headers=h,
                json={
                    "requester": "r",
                    "approver": "a",
                    "delegations": [
                        {"owner": "o", "delegate": "a", "starts": 0, "expires": 2}
                    ],
                    "now": 1,
                },
            )
        ).json()["allowed"]


@pytest.mark.asyncio
async def test_ui(tmp_path):
    app = create_collaboration_app(str(tmp_path / "c.db"), "")
    tr = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=tr, base_url="http://x") as c:
        p = await c.get("/collaboration")
        assert p.status_code == 200
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
            assert x in p.text
        assert (
            await c.post("/v1/collaboration/roles/authorize", json={})
        ).status_code == 503


def test_openapi(tmp_path):
    paths = set(
        create_collaboration_app(str(tmp_path / "c.db"), "k").openapi()["paths"]
    )
    assert len([x for x in paths if x.startswith("/v1/collaboration")]) == 6
