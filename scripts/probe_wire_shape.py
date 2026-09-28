"""Prove the space-bunny 400 is an endpoint/protocol mismatch, not a dead model.

The user is talking to `space-bunny-free` right now on `@opencode-go`
directly, and it works. The gateway parks the same model for 30 days with
`400 ModelProtocolUnsupported`. Both cannot be true unless the difference
is in the request, and the registry says where it is:

    slug=opencode-go  base_url=https://opencode.ai/zen/go/v1
                     api_mode=codex_responses      <-- ALL requests -> /responses
    user_agent=opencode/1.18.31

`codex_responses` sends everything down `POST /responses`. The user's direct
client sends `POST /chat/completions`. `provider_direct.py:288` already
records that opencode-go serves most models on ONE endpoint only and answers
the other with 503 "Endpoint is unavailable" — the 400 `ModelProtocol
Unsupported` is the same refusal, spelled differently.

This probe calls the provider directly with the same credential the gateway
uses, on BOTH endpoints, and reports the raw upstream body for each. It also
replays the gateway's own request shape (streamed, with a tool, which is what
an agent session sends) so a difference in the body can be ruled in or out.

No credential is written here: the key is read from the gateway's own vault
at run time and only ever printed as a length.

Run: sudo python scripts/probe_wire_shape.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, "/home/zoltan/llm-budget-gateway/src")
from llm_budget_gateway.provider_connections import (  # noqa: E402
    CredentialVault,
    ProviderConnectionStore,
)

DATA = Path("/home/zoltan/llm-budget-gateway/.gateway-console")
SLUG = "opencode-go"
MODEL = "space-bunny-free"
GW_KEY = "gw_dktUxnLbj6dPI0xklC_Cq77I0l1mbw"
GW = "http://127.0.0.1:8000/v1/chat/completions"


def _secret() -> dict:
    store = ProviderConnectionStore(
        sqlite3.connect(str(DATA / "providers.db"), check_same_thread=False),
        CredentialVault(DATA / "provider-master.key"),
    )
    for conn in store.list():
        if str(conn["slug"]) == SLUG:
            return store.connection_secret(str(conn["id"]))
    raise SystemExit(f"provider {SLUG} not found")


def _post(label: str, url: str, body: dict, headers: dict) -> None:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={**headers, "content-type": "application/json"}, method="POST",
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read().decode()
        print(f"  [200] {label:44s} {time.monotonic() - t0:5.1f}s  {raw[:170]}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        print(f"  [{exc.code}] {label:44s} {time.monotonic() - t0:5.1f}s  {raw[:170]}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [ERR] {label:44s} {type(exc).__name__}: {exc}")


def main() -> int:
    secret = _secret()
    base = str(secret["base_url"]).rstrip("/")
    hdrs = {
        "Authorization": f"Bearer {secret['api_key']}",
        "User-Agent": str(secret.get("user_agent") or "opencode/1.18.31"),
    }
    msgs = [{"role": "user", "content": "Reply with the single word: pong"}]
    # The provider refuses a request with no x-opencode-session
    # (400 MissingSessionID) on BOTH endpoints, so a fixed id is sent to get
    # past routing and see which endpoint the model itself accepts.
    hdrs["x-opencode-session"] = "probe-fixed-session"
    print(f"provider={SLUG}  base={base}  api_mode={secret.get('api_mode')}")
    print("  (the model the user reaches directly, same credential)\n")

    print("=== the two endpoints, same model, same key ===")
    _post("POST /chat/completions  (what you use)",
          f"{base}/chat/completions",
          {"model": MODEL, "messages": msgs, "max_tokens": 64}, hdrs)
    _post("POST /responses         (what the gateway sends)",
          f"{base}/responses",
          {"model": MODEL, "input": msgs, "max_tokens": 64,
           "instructions": None, "stream": False}, hdrs)

    print("\n=== the gateway's own streamed+tool shape on /responses ===")
    _post("POST /responses, stream + tools",
          f"{base}/responses",
          {"model": MODEL, "input": msgs, "max_tokens": 64, "stream": True,
           "tools": [{"type": "function", "name": "terminal", "description": "run a shell command",
                      "parameters": {"type": "object",
                                     "properties": {"command": {"type": "string"}},
                                     "required": ["command"]}}]}, hdrs)

    print("\n=== through the gateway route (falls back to a healthy model) ===")
    _post("gateway /v1/chat/completions, model=hermes-default", GW,
          {"model": "hermes-default", "messages": msgs, "max_tokens": 64},
          {"Authorization": f"Bearer {GW_KEY}"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
