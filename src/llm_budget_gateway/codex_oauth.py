"""Independent Codex OAuth for the gateway (own vault, no Hermes reuse).

Implements the ChatGPT Plus / Codex backend:
  POST https://auth.openai.com/oauth/token  client_id=app_EMoamEEZ73f0CkXaXp7hrann
  base https://chatgpt.com/backend-api/codex  (POST /responses, store:false)
  wire: Authorization Bearer + ChatGPT-Account-ID + residency + originator

Single-use refresh_token rotation is handled by refresh_codex_token (pure,
no store mutation here; caller persists the new pair atomically).
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any

import httpx

CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 120
CODEX_RATE_LIMITED_CODE = "codex_rate_limited"
CODEX_CLI_VERSION = "0.160.1"


class CodexAuthError(RuntimeError):
    def __init__(self, message: str, *, code: str = "", relogin_required: bool = False, retry_after: float | None = None):
        super().__init__(message)
        self.code = code
        self.relogin_required = relogin_required
        self.retry_after = retry_after


def _b64url_decode(s: str) -> bytes:
    # JWT parts are base64url without padding
    s = s.strip()
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)


def decode_jwt_claims(token: str) -> dict[str, Any]:
    """Decode JWT payload without verification (gateway only reads exp + auth claims)."""
    if not isinstance(token, str) or token.count(".") < 2:
        return {}
    try:
        parts = token.split(".")
        payload = _b64url_decode(parts[1])
        data = json.loads(payload)
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


def is_access_token_expiring(token: Any, skew_seconds: int = CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS) -> bool:
    """True when exp <= now + skew (or when exp is missing/unparseable -> treat as expiring)."""
    if not isinstance(token, str) or not token.strip():
        return True
    claims = decode_jwt_claims(token)
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        return True
    return float(exp) <= (time.time() + max(0, int(skew_seconds)))


def codex_account_headers(access_token: str) -> dict[str, str]:
    """Workspace headers derived from the JWT auth claims."""
    headers: dict[str, str] = {}
    if not isinstance(access_token, str) or not access_token.strip():
        return headers
    try:
        claims = decode_jwt_claims(access_token)
        auth = claims.get("https://api.openai.com/auth", {})
        if not isinstance(auth, dict):
            return headers
        acct = auth.get("chatgpt_account_id")
        if isinstance(acct, str) and acct.strip():
            headers["ChatGPT-Account-ID"] = acct.strip()
        residency = auth.get("chatgpt_data_residency") or auth.get("chatgpt_compute_residency")
        if isinstance(residency, str) and residency.strip():
            headers["x-openai-internal-codex-residency"] = residency.strip()
    except Exception:
        pass
    return headers


def _is_official_codex_base_url(base_url: str) -> bool:
    try:
        from urllib.parse import urlparse
        u = urlparse(base_url or "")
        path = (u.path or "").rstrip("/")
        return (
            u.scheme == "https"
            and (u.hostname or "") == "chatgpt.com"
            and u.port in (None, 443)
            and (path == "/backend-api/codex" or path.startswith("/backend-api/codex/"))
        )
    except Exception:
        return False


def codex_oauth_headers(access_token: str, *, base_url: str = CODEX_BASE_URL) -> dict[str, str]:
    """Full wire headers for chatgpt.com/backend-api/codex (auth + identity)."""
    headers: dict[str, str] = {}
    if isinstance(access_token, str) and access_token.strip():
        headers["Authorization"] = f"Bearer {access_token.strip()}"
    # account / residency from JWT
    headers.update(codex_account_headers(access_token))
    # originator identity
    if _is_official_codex_base_url(base_url):
        headers["originator"] = "hermes-agent"
        try:
            from llm_budget_gateway import __version__  # noqa: F401
            headers["User-Agent"] = f"HermesAgent/{__version__}"
        except Exception:
            headers["User-Agent"] = "HermesAgent/0.1.0"
    else:
        headers["originator"] = "codex_cli_rs"
        headers.setdefault("User-Agent", "codex_cli_rs/0.160.1")
    return headers


def refresh_codex_token(*, refresh_token: str, timeout_seconds: float = 20.0) -> dict[str, Any]:
    """POST refresh_token grant to auth.openai.com; return new {access_token, refresh_token}."""
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise CodexAuthError("missing refresh_token", code="codex_auth_missing_refresh_token", relogin_required=True)
    with httpx.Client(timeout=httpx.Timeout(max(5.0, float(timeout_seconds)))) as client:
        resp = client.post(
            CODEX_OAUTH_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token.strip(),
                "client_id": CODEX_CLIENT_ID,
            },
        )
    if resp.status_code == 429:
        retry_after = None
        try:
            retry_after = int(resp.headers.get("retry-after", "") or 0) or None
        except Exception:
            retry_after = None
        raise CodexAuthError(
            "Codex provider quota exhausted (429)",
            code=CODEX_RATE_LIMITED_CODE,
            relogin_required=False,
            retry_after=retry_after,
        )
    if resp.status_code != 200:
        # try to surface structured error
        code = "codex_refresh_failed"
        msg = f"Codex token refresh failed with status {resp.status_code}."
        try:
            body = resp.json()
            if isinstance(body, dict):
                err = body.get("error")
                if isinstance(err, dict):
                    c2 = err.get("code") or err.get("type")
                    if isinstance(c2, str) and c2.strip():
                        code = c2.strip()
                    m2 = err.get("message")
                    if isinstance(m2, str) and m2.strip():
                        msg = f"Codex token refresh failed: {m2.strip()}"
                elif isinstance(err, str) and err.strip():
                    code = err.strip()
        except Exception:
            pass
        relogin = code in {"invalid_grant", "invalid_token", "invalid_request", "refresh_token_reused"} or resp.status_code in {401, 403}
        raise CodexAuthError(msg, code=code, relogin_required=relogin)
    try:
        payload = resp.json()
    except Exception as exc:
        raise CodexAuthError("Codex token refresh returned invalid JSON.", code="codex_refresh_invalid_json") from exc
    if not isinstance(payload, dict):
        raise CodexAuthError("Codex token refresh response was missing access_token.", code="codex_refresh_missing_access_token")
    at = payload.get("access_token")
    if not isinstance(at, str) or not at.strip():
        raise CodexAuthError("Codex token refresh response was missing access_token.", code="codex_refresh_missing_access_token")
    out: dict[str, Any] = {"access_token": at.strip()}
    nxt = payload.get("refresh_token")
    if isinstance(nxt, str) and nxt.strip():
        out["refresh_token"] = nxt.strip()
    else:
        out["refresh_token"] = refresh_token.strip()
    return out


def codex_device_flow(*, timeout_seconds: float = 20.0) -> dict:
    """Start Codex device-code flow (user visits URL, enters code)."""
    import httpx as _httpx
    # OpenAI device authorization — uses same client_id
    resp = _httpx.Client(timeout=_httpx.Timeout(max(5.0, float(timeout_seconds)))).post(
        "https://auth.openai.com/oauth/authorize",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_id": CODEX_CLIENT_ID,
            "scope": "openid profile email offline_access",
            "response_type": "device_code",
        },
    )
    # Fallback: many deployments use browser PKCE instead; expose the browser authorize URL
    return {"authorize_url": f"https://auth.openai.com/oauth/authorize?response_type=code&client_id={CODEX_CLIENT_ID}&redirect_uri=http://localhost:1455/auth/callback&scope=openid+profile+email+offline_access&code_challenge=challenge&code_challenge_method=S256&state=state"}


def codex_browser_authorize_url(*, redirect_uri: str = "http://localhost:1455/auth/callback", state: str = "gw-state", code_challenge: str = "challenge") -> str:
    from urllib.parse import urlencode
    return f"https://auth.openai.com/oauth/authorize?" + urlencode({
        "response_type": "code",
        "client_id": CODEX_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": "openid profile email offline_access",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    })
