"""Probe: does @opencode-go/mimo-v2.5 / @opencode-go2/mimo-v2.5 accept image_url content?

Decrypts the vault, builds a tiny OpenAI-compatible chat request with a 1x1 red PNG
and streams it through the SAME direct transport the gateway uses (provider_direct.py).
"""
from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

import httpx

from llm_budget_gateway.provider_connections import CredentialVault, ProviderConnectionStore

REPO = Path.home() / "llm-budget-gateway"
data_dir = REPO / ".gateway-console"

# 1x1 red PNG
PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q1A0"
    "WQAAAABJRU5ErkJggg=="
)

store = ProviderConnectionStore(
    __import__("sqlite3").connect(data_dir / "providers.db", check_same_thread=False),
    CredentialVault(data_dir / "provider-master.key"),
)

body = {
    "model": "mimo-v2.5",
    "messages": [
        {"role": "user", "content": [
            {"type": "text", "text": "What color is this image? Reply with one word."},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG_B64}"}},
        ]},
    ],
    "max_tokens": 20,
    "stream": True,
}

ok = True
for slug in ("opencode-go", "opencode-go2"):
    conn = next((c for c in store.list() if c["slug"] == slug), None)
    if conn is None:
        print(f"[{slug}] NOT REGISTERED")
        continue
    secret = store.connection_secret(conn["id"])
    base = str(secret.get("base_url", "")).rstrip("/")
    key = secret.get("api_key", "")
    print(f"[{slug}] base={base} key={key[:6]}...")
    try:
        r = httpx.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "User-Agent": "opencode/1.14.41"},
            json=body,
            timeout=httpx.Timeout(40.0, connect=15.0),
        )
        status = r.status_code
        if status == 200:
            lines = []
            for line in r.iter_lines():
                if line and line.startswith("data:"):
                    lines.append(line[5:].strip())
            first = next((l for l in lines if l and l != "[DONE]"), "")
            print(f"  -> 200 VISION OK; chunks={len(lines)}; first-chunk={first[:120]}")
        else:
            text = r.read().decode("utf-8", errors="replace")
            print(f"  -> {status} {text[:300]}")
            ok = False
    except Exception as exc:  # noqa: BLE001
        print(f"  -> ERROR {type(exc).__name__}: {exc}")
        ok = False

sys.exit(0 if ok else 1)
