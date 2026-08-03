"""REST API tests for create_mcp_governance_app.

Normative per docs/architecture/mcp-governance.md §7. Interface tests pass
immediately (factory signature); behavioral tests exercise the endpoints over
real HTTP (httpx.ASGITransport, matching tests/test_fleet_api.py). In the RED
phase the factory raises NotImplementedError, so every behavioral test fails
with NotImplementedError until the implementer wires the app.
"""

import inspect

import httpx

from llm_budget_gateway.mcp_governance import create_mcp_governance_app

AUTH_HEADERS = {"Authorization": "Bearer k", "X-Tenant-Id": "t"}

SERVER_BODY = {
    "name": "github-mcp",
    "transport": "http",
    "endpoint": "https://mcp.example.com/mcp",
    "version": "1.0.0",
    "description": "GitHub tooling",
    "tools": [
        {
            "name": "create_issue",
            "description": "Create a GitHub issue",
            "input_schema": {"type": "object", "properties": {"title": {"type": "string"}}},
            "enabled": True,
        },
        {
            "name": "get_repo",
            "description": "Read repository metadata",
            "input_schema": {"type": "object", "properties": {"owner": {"type": "string"}}},
            "enabled": True,
        },
    ],
    "config": {"auth": "bearer"},
}

POLICY_BODY = {
    "scope_kind": "user",
    "scope_key": "alice",
    "server_id": "srv123",
    "tool_name": "create_issue",
    "effect": "allow",
    "description": "Alice may create issues",
}

BUDGET_BODY = {
    "scope_kind": "user",
    "scope_key": "alice",
    "server_id": "srv123",
    "tool_name": "create_issue",
    "hard_limit": 5.0,
    "window": "30d",
}

EXPECTED_PATHS = {
    "/mcp",
    "/v1/mcp/servers",
    "/v1/mcp/servers/{server_id}",
    "/v1/mcp/servers/{server_id}/tools",
    "/v1/mcp/policies",
    "/v1/mcp/policies/{policy_id}",
    "/v1/mcp/budgets",
    "/v1/mcp/budgets/{budget_id}",
    "/v1/mcp/audit",
    "/v1/mcp/approvals",
    "/v1/mcp/approvals/{approval_id}/approve",
    "/v1/mcp/approvals/{approval_id}/reject",
    "/v1/mcp/report",
}


class TestFactoryInterface:
    def test_factory_callable(self):
        assert callable(create_mcp_governance_app)

    def test_factory_signature(self):
        sig = inspect.signature(create_mcp_governance_app)
        assert sig.parameters["api_key"].default is None
        assert sig.parameters["conn"].kind is inspect.Parameter.KEYWORD_ONLY
        assert sig.parameters["conn"].default is None


class TestApiBehavior:
    """RED-phase: the factory raises NotImplementedError until implemented."""

    def test_auth_401_no_key(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(tr, "POST", "/v1/mcp/servers", json=SERVER_BODY)
        assert r.status_code == 401

    def test_auth_401_wrong_key(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        h = {"Authorization": "Bearer wrong", "X-Tenant-Id": "t"}
        r = run_sync_request(tr, "POST", "/v1/mcp/servers", headers=h, json=SERVER_BODY)
        assert r.status_code == 401

    def test_fail_closed_503_no_key_configured(self):
        app = create_mcp_governance_app("")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(tr, "POST", "/v1/mcp/servers", json=SERVER_BODY)
        assert r.status_code == 503

    def test_register_server_201(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(tr, "POST", "/v1/mcp/servers", headers=AUTH_HEADERS, json=SERVER_BODY)
        assert r.status_code == 201
        assert r.json()["server_id"]

    def test_get_servers_list(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(tr, "GET", "/v1/mcp/servers", headers=AUTH_HEADERS)
        assert r.status_code == 200
        assert r.json()["object"] == "list"

    def test_get_server_by_id_404(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(tr, "GET", "/v1/mcp/servers/nope", headers=AUTH_HEADERS)
        assert r.status_code == 404

    def test_duplicate_server_409(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        run_sync_request(tr, "POST", "/v1/mcp/servers", headers=AUTH_HEADERS, json=SERVER_BODY)
        r = run_sync_request(tr, "POST", "/v1/mcp/servers", headers=AUTH_HEADERS, json=SERVER_BODY)
        assert r.status_code == 409

    def test_register_invalid_body_422(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(tr, "POST", "/v1/mcp/servers", headers=AUTH_HEADERS, json={"name": "x"})
        assert r.status_code == 422

    def test_get_tools(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(tr, "GET", "/v1/mcp/servers/srv123/tools", headers=AUTH_HEADERS)
        assert r.status_code == 200
        assert r.json()["object"] == "list"

    def test_create_policy_201(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(tr, "POST", "/v1/mcp/policies", headers=AUTH_HEADERS, json=POLICY_BODY)
        assert r.status_code == 201
        assert r.json()["policy_id"]

    def test_list_policies(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(tr, "GET", "/v1/mcp/policies", headers=AUTH_HEADERS)
        assert r.status_code == 200
        assert r.json()["object"] == "list"

    def test_delete_policy_204(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(tr, "DELETE", "/v1/mcp/policies/nope", headers=AUTH_HEADERS)
        assert r.status_code == 204

    def test_create_budget_201(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(tr, "POST", "/v1/mcp/budgets", headers=AUTH_HEADERS, json=BUDGET_BODY)
        assert r.status_code == 201
        assert r.json()["budget_id"]

    def test_list_budgets(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(tr, "GET", "/v1/mcp/budgets", headers=AUTH_HEADERS)
        assert r.status_code == 200
        assert r.json()["object"] == "list"

    def test_audit_query(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(tr, "GET", "/v1/mcp/audit?caller=alice&limit=10", headers=AUTH_HEADERS)
        assert r.status_code == 200
        assert r.json()["object"] == "list"

    def test_approve_approval(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(
            tr, "POST", "/v1/mcp/approvals/aprv1/approve",
            headers=AUTH_HEADERS, json={"actor": "bob"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "approved"

    def test_approve_unknown_404(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(
            tr, "POST", "/v1/mcp/approvals/nope/approve",
            headers=AUTH_HEADERS, json={"actor": "bob"},
        )
        assert r.status_code == 404

    def test_report_shape(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(tr, "GET", "/v1/mcp/report", headers=AUTH_HEADERS)
        assert r.status_code == 200
        body = r.json()
        for key in (
            "total_servers",
            "active_servers",
            "retired_servers",
            "total_tools",
            "tools_with_policy",
            "tools_with_budget",
            "pending_approvals",
            "ssrf_blocks_24h",
            "pii_redactions_24h",
            "budget_breaches_24h",
            "risk_tier",
        ):
            assert key in body

    def test_dashboard_markers(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(tr, "GET", "/mcp")
        assert r.status_code == 200
        for marker in (
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
            assert marker in r.text

    def test_openapi_paths(self):
        app = create_mcp_governance_app("k")
        paths = app.openapi()["paths"]
        assert set(paths) >= EXPECTED_PATHS

    # -- M4: list endpoints must support limit/offset pagination. --------

    def test_list_servers_pagination_truncates(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        for i in range(3):
            body = dict(SERVER_BODY, name=f"srv-{i}")
            r = run_sync_request(tr, "POST", "/v1/mcp/servers", headers=AUTH_HEADERS, json=body)
            assert r.status_code == 201
        r = run_sync_request(tr, "GET", "/v1/mcp/servers?limit=2", headers=AUTH_HEADERS)
        assert r.status_code == 200
        assert len(r.json()["data"]) == 2
        r = run_sync_request(tr, "GET", "/v1/mcp/servers?limit=2&offset=2", headers=AUTH_HEADERS)
        assert len(r.json()["data"]) == 1
        # default limit is generous: all 3 come back without params
        r = run_sync_request(tr, "GET", "/v1/mcp/servers", headers=AUTH_HEADERS)
        assert len(r.json()["data"]) == 3

    def test_list_servers_pagination_rejects_bad_limit(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(tr, "GET", "/v1/mcp/servers?limit=0", headers=AUTH_HEADERS)
        assert r.status_code == 422

    def test_list_tools_pagination_truncates(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        r = run_sync_request(tr, "POST", "/v1/mcp/servers", headers=AUTH_HEADERS, json=SERVER_BODY)
        server_id = r.json()["server_id"]
        r = run_sync_request(
            tr, "GET", f"/v1/mcp/servers/{server_id}/tools?limit=1", headers=AUTH_HEADERS
        )
        assert r.status_code == 200
        assert len(r.json()["data"]) == 1
        r = run_sync_request(
            tr, "GET", f"/v1/mcp/servers/{server_id}/tools?limit=1&offset=1",
            headers=AUTH_HEADERS,
        )
        assert len(r.json()["data"]) == 1

    def test_list_policies_pagination_truncates(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        for i in range(3):
            body = dict(POLICY_BODY, scope_key=f"alice{i}")
            r = run_sync_request(tr, "POST", "/v1/mcp/policies", headers=AUTH_HEADERS, json=body)
            assert r.status_code == 201
        r = run_sync_request(tr, "GET", "/v1/mcp/policies?limit=2", headers=AUTH_HEADERS)
        assert r.status_code == 200
        assert len(r.json()["data"]) == 2

    def test_list_budgets_pagination_truncates(self):
        app = create_mcp_governance_app("k")
        tr = httpx.ASGITransport(app=app)
        for i in range(3):
            body = dict(BUDGET_BODY, scope_key=f"alice{i}")
            r = run_sync_request(tr, "POST", "/v1/mcp/budgets", headers=AUTH_HEADERS, json=body)
            assert r.status_code == 201
        r = run_sync_request(tr, "GET", "/v1/mcp/budgets?limit=2", headers=AUTH_HEADERS)
        assert r.status_code == 200
        assert len(r.json()["data"]) == 2

    def test_list_approvals_pagination_truncates(self):
        from llm_budget_gateway.mcp_governance import open_mcp_db
        from llm_budget_gateway.mcp_governance.rules import ApprovalStore
        from llm_budget_gateway.mcp_governance.schemas import ApprovalRequest

        conn = open_mcp_db(":memory:")
        try:
            store = ApprovalStore(conn)
            for i in range(3):
                store.insert(
                    ApprovalRequest(
                        approval_id=f"aprv{i}",
                        server_id="srv1",
                        tool_name="t1",
                        caller="alice",
                        scope_kind="user",
                        scope_key="alice",
                        args_redacted={},
                        args_hash=f"h{i}",
                        status="pending",
                        requested_at=100 + i,
                        decided_at=None,
                        decided_by=None,
                        expires_at=None,
                    )
                )
            app = create_mcp_governance_app("k", conn=conn)
            tr = httpx.ASGITransport(app=app)
            r = run_sync_request(tr, "GET", "/v1/mcp/approvals?limit=2", headers=AUTH_HEADERS)
            assert r.status_code == 200
            assert len(r.json()["data"]) == 2
            r = run_sync_request(tr, "GET", "/v1/mcp/approvals?limit=2&offset=2", headers=AUTH_HEADERS)
            assert len(r.json()["data"]) == 1
        finally:
            conn.close()


def run_sync_request(transport, method, url, **kwargs):
    """Run one HTTP request synchronously against an ASGI transport."""
    import asyncio

    async def _run():
        async with httpx.AsyncClient(transport=transport, base_url="http://x") as c:
            return await c.request(method, url, **kwargs)

    return asyncio.run(_run())
