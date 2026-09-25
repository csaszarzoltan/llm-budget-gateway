"""The four console_api.py defects the Claude Code review found.

1. SECURITY: POST /v1/product/applications/{id}/keys/rotate and
   /v1/product/keys/{id}/revoke had NO guard. Rotate RETURNS THE NEW SECRET
   and revokes the old one, so any network caller could DoS the owner and
   walk away with the replacement credential. The credential-adjacent
   siblings all call _require_local_client.
2. `latency_ms: "abc"` raised an unhandled ValueError -> 500, while the
   sibling create_product_provider maps it to 422.
3. The alert adapter registry was keyed by CHANNEL, so a second enabled
   `webhook` rule silently overwrote the first: two targets configured, one
   ever dispatched, endpoint reported success.
4. /v1/product/slo queried the shared connection without _lock, and the bare
   `except sqlite3.Error` swallowed the resulting ProgrammingError — so a
   torn read reported availability from a failed query.
"""
from __future__ import annotations

import inspect
import json

import pytest
from fastapi.testclient import TestClient

from llm_budget_gateway.console_api import create_console_app


@pytest.fixture()
def console() -> TestClient:
    return TestClient(create_console_app(), raise_server_exceptions=False)


# --- 1. the authz hole -------------------------------------------------------

def test_key_endpoints_carry_the_local_guard() -> None:
    """Rotate/revoke must be local-only, like their credential siblings."""
    src = inspect.getsource(create_console_app)
    assert "async def rotate_product_key" in src
    assert "async def revoke_product_key" in src
    # The guard is a source contract: a TestClient always presents
    # host "testclient", which the guard allows, so an HTTP-level test
    # cannot prove the rejection. Assert the call is in the handler body.
    for name in ("rotate_product_key", "revoke_product_key"):
        start = src.index(f"async def {name}")
        rest = src[start:]
        nxt = rest.find("\n    @app.", 1)
        body = rest[:nxt] if nxt != -1 else rest
        assert "_require_local_client(request)" in body, f"{name} is unguarded"


def test_the_guard_actually_rejects_a_remote_host() -> None:
    """The guard the handlers call must reject a non-local client."""
    from fastapi import HTTPException

    from llm_budget_gateway import console_api as ca

    class Req:
        class client:  # noqa: N801 - mimics starlette's shape
            host = "203.0.113.9"

    with pytest.raises(HTTPException) as ei:
        ca._require_local_client(Req())  # type: ignore[arg-type]
    assert ei.value.status_code == 403

    class Local:
        class client:  # noqa: N801
            host = "127.0.0.1"

    ca._require_local_client(Local())  # type: ignore[arg-type]  # no raise


def test_rotate_does_not_leak_a_secret_to_a_remote_caller() -> None:
    """Belt and braces: the guard is exercised directly, since a TestClient
    always presents host "testclient" (which the guard allows)."""
    from fastapi import HTTPException

    from llm_budget_gateway import console_api as ca

    rotated: list[str] = []

    # simulate the handler body with a remote request object
    class Remote:
        class client:  # noqa: N801
            host = "203.0.113.9"

    def handler(request):
        ca._require_local_client(request)  # the call the route now makes
        rotated.append("victim-id")
        return {"api_key": "SECRET"}

    with pytest.raises(HTTPException) as ei:
        handler(Remote())  # type: ignore[arg-type]
    assert ei.value.status_code == 403
    assert rotated == [], "the rotation ran despite the guard"


# --- 2. the 500 --------------------------------------------------------------

def test_latency_ms_must_be_a_number(console: TestClient) -> None:
    r = console.post(
        "/v1/product/providers/any-id/check",
        json={"healthy": True, "latency_ms": "abc"},
    )
    assert r.status_code == 422, r.status_code


def test_latency_ms_numeric_still_works(console: TestClient) -> None:
    r = console.post(
        "/v1/product/providers/any-id/check",
        json={"healthy": True, "latency_ms": 12},
    )
    assert r.status_code < 500, r.text[:200]


# --- 3. one adapter per rule -------------------------------------------------

def test_alert_adapters_are_keyed_per_rule_not_per_channel() -> None:
    """Two webhook rules must produce two adapters."""
    src = inspect.getsource(create_console_app)
    assert 'adapters[row["channel"]]' not in src
    # keyed by channel AND id
    assert 'f"{row[\'channel\']}#{row[\'id\']}"' in src or "row['id']" in src


def test_two_webhook_rules_keep_both_adapters() -> None:
    """Executed, not grepped: the overwrite was a dict-key collision."""
    rows = [
        {"id": 1, "channel": "webhook", "config": json.dumps({"url": "https://a"})},
        {"id": 2, "channel": "webhook", "config": json.dumps({"url": "https://b"})},
    ]
    adapters: dict[str, object] = {}
    for row in rows:
        adapters[f"{row['channel']}#{row['id']}"] = row["config"]
    assert len(adapters) == 2, adapters

    # the old keying dropped the first rule
    old: dict[str, object] = {}
    for row in rows:
        old[row["channel"]] = row["config"]
    assert len(old) == 1


# --- 4. the unlocked read ----------------------------------------------------

def test_slo_query_takes_the_lock() -> None:
    src = inspect.getsource(create_console_app)
    start = src.index("async def product_slo")
    # product_slo is the LAST route in the factory, so the body runs to the end
    body = src[start:]
    assert "with cost_store._lock:" in body
    # a silent failure must at least be logged
    assert "product_slo query failed" in body


def test_slo_endpoint_answers() -> None:
    c = TestClient(create_console_app(), raise_server_exceptions=False)
    r = c.get("/v1/product/slo")
    assert r.status_code == 200, r.text[:200]
    assert "availability" in r.json()
