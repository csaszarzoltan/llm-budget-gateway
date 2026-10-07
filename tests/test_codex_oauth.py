"""RED: independent Codex OAuth for the gateway (own store, no Hermes reuse)."""

from __future__ import annotations

import base64
import json
import sys
import time

import pytest

SRC = "/home/zoltan/llm-budget-gateway/src"
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from llm_budget_gateway.codex_oauth import (
    CODEX_CLIENT_ID,
    CODEX_OAUTH_TOKEN_URL,
    CODEX_BASE_URL,
    decode_jwt_claims,
    is_access_token_expiring,
    codex_account_headers,
    codex_oauth_headers,
)


def _jwt(exp: float, account_id: str = "acct-test-123", residency: str = "no_constraint") -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {
                "exp": exp,
                "https://api.openai.com/auth": {
                    "chatgpt_account_id": account_id,
                    "chatgpt_data_residency": residency,
                },
            }
        ).encode()
    ).decode().rstrip("=")
    return f"{header}.{payload}.sig"


def test_constants_pin_official_values() -> None:
    assert CODEX_CLIENT_ID == "app_EMoamEEZ73f0CkXaXp7hrann"
    assert CODEX_OAUTH_TOKEN_URL == "https://auth.openai.com/oauth/token"
    assert CODEX_BASE_URL == "https://chatgpt.com/backend-api/codex"


def test_decode_jwt_claims() -> None:
    tok = _jwt(time.time() + 3600, account_id="acct-999")
    claims = decode_jwt_claims(tok)
    assert claims["exp"] > time.time()
    assert claims["https://api.openai.com/auth"]["chatgpt_account_id"] == "acct-999"


def test_is_expiring_respects_skew() -> None:
    future = _jwt(time.time() + 600)
    past = _jwt(time.time() - 10)
    assert is_access_token_expiring(past, skew_seconds=120) is True
    assert is_access_token_expiring(future, skew_seconds=120) is False
    assert is_access_token_expiring(_jwt(time.time() + 60), skew_seconds=120) is True


def test_codex_account_headers_shape() -> None:
    tok = _jwt(time.time() + 3600, account_id="acct-AAA", residency="eu")
    h = codex_account_headers(tok)
    assert h["ChatGPT-Account-ID"] == "acct-AAA"
    assert h["x-openai-internal-codex-residency"] == "eu"


def test_codex_oauth_headers_include_originator() -> None:
    tok = _jwt(time.time() + 3600, account_id="acct-AAA")
    h = codex_oauth_headers(tok, base_url=CODEX_BASE_URL)
    assert h["Authorization"].startswith("Bearer ")
    assert "ChatGPT-Account-ID" in h
    assert h["originator"] == "hermes-agent"


def test_refresh_pure_posts_correct_grant(monkeypatch) -> None:
    from llm_budget_gateway.codex_oauth import refresh_codex_token

    captured: dict = {}

    class FakeResp:
        status_code = 200
        headers: dict = {}
        def json(self):
            return {"access_token": _jwt(time.time() + 3600), "refresh_token": "rt-new-123"}

    class FakeClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, **kw):
            captured["url"] = url
            captured["data"] = kw.get("data", {})
            return FakeResp()

    monkeypatch.setattr("llm_budget_gateway.codex_oauth.httpx.Client", FakeClient)
    out = refresh_codex_token(refresh_token="rt-old-456", timeout_seconds=5.0)
    assert captured["url"] == CODEX_OAUTH_TOKEN_URL
    assert captured["data"]["grant_type"] == "refresh_token"
    assert captured["data"]["refresh_token"] == "rt-old-456"
    assert captured["data"]["client_id"] == CODEX_CLIENT_ID
    assert out["refresh_token"] == "rt-new-123"
    assert "access_token" in out


def test_provider_endpoint_oauth_codex_headers(monkeypatch) -> None:
    from llm_budget_gateway.provider_direct import DirectProviderClient
    tok = _jwt(time.time() + 3600, account_id="acct-XYZ")
    client = DirectProviderClient(
        {
            "my-codex": {
                "base_url": CODEX_BASE_URL,
                "api_key_env": "__vault_my-codex__",
                "auth": "oauth_codex",
                "api_key": tok,
                "models": ["gpt-5.6"],
                "api_mode": "codex_responses",
            }
        }
    )
    ep = client.resolve("@my-codex/gpt-5.6")
    headers = ep.headers()
    assert headers["Authorization"].startswith("Bearer ")
    assert headers["ChatGPT-Account-ID"] == "acct-XYZ"
    assert headers["originator"] in {"hermes-agent", "codex_cli_rs"}


def test_gateway_has_oauth_vault_store(tmp_path, monkeypatch) -> None:
    from llm_budget_gateway.codex_store import CodexOAuthStore
    store = CodexOAuthStore(tmp_path / "codex-oauth.db", key_path=tmp_path / "codex.key")
    tok = _jwt(time.time() + 3600)
    store.save(provider_id="my-codex", access_token=tok, refresh_token="rt-1", account_id="acct-1")
    loaded = store.load("my-codex")
    assert loaded is not None
    assert loaded["refresh_token"] == "rt-1"
    raw = (tmp_path / "codex-oauth.db").read_bytes()
    assert b"rt-1" not in raw


def test_discovery_supports_codex_responses(monkeypatch) -> None:
    from llm_budget_gateway.provider_connections import _discovery_request
    req = _discovery_request("openai_codex", {"base_url": CODEX_BASE_URL, "api_key": "tok"})
    assert "url" in req and "headers" in req
