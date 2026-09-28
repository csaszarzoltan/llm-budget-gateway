"""Does the parked model work when called DIRECTLY on the same provider?

The UI says `@opencode-go/space-bunny-free` is parked for 30 days with
`400 ModelProtocolUnsupported`. The user, however, is talking to the very
same model right now on the very same provider — directly, not through the
gateway. Both cannot be the whole story: if the model serves requests
directly, then what the gateway sends is the thing it refuses.

So the model is fine and the WIRE FORMAT is the suspect. `ModelProtocol
Unsupported` is not a model error, it is a shape error: the provider parsed
the request and rejected the protocol/endpoint it arrived on. The gateway
has a documented history of exactly this — `provider_direct.py:288` records
that opencode-go "serves most models on ONE endpoint only" and answers the
other with 503 "Endpoint is unavailable", which is why chat-capable models
must not be sent down the provider-wide `api_mode=codex_responses` path.

This probe sends the same model to the same provider both ways, so the
difference is visible rather than inferred:

  A  direct POST to the provider's own endpoint (what the user does)
  B  the same request through the gateway's route
  C  the same direct POST, but with the other endpoint shape

It reads the provider's base_url and key from the gateway's own store so no
credential is written into this file.

Run: python scripts/probe_direct_vs_gateway.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.error
import urllib.request

MODEL = "space-bunny-free"
SLUG = "opencode-go"
GW_KEY = "gw_dktUxnLbj6dPI0xklC_Cq77I0l1mbw"
GW = "http://127.0.0.1:8000/v1/chat/completions"
PROD = "/home/zoltan/llm-budget-gateway/.gateway-console/product.db"


def _provider() -> dict | None:
    """Read the provider's own base_url/api_mode and key from the store."""
    if not os.path.exists(PROD):
        return None
    con = sqlite3.connect(f"file:{PROD}?mode=ro", uri=True)
    try:
        cur = con.execute("SELECT * FROM pc_providers")
    except sqlite3.OperationalError:
        return None
    cols = [c[0] for c in cur.description]
    for row in cur:
        d = dict(zip(cols, row, strict=False))
        if d.get("slug") == SLUG or SLUG in str(d.get("name", "")).lower():
            return d
    return None


def _post(url: str, body: dict, headers: dict, timeout: float = 120.0) -> str:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={**headers, "content-type": "application/json"}, method="POST",
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode()
        return f"HTTP 200 in {time.monotonic() - t0:.1f}s  {data[:160]}"
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code} in {time.monotonic() - t0:.1f}s  {exc.read().decode()[:160]}"
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


def main() -> int:
    prov = _provider()
    if not prov:
        print(f"provider {SLUG!r} not found in pc_providers — cannot compare directly.")
        print("The comparison needs the provider's own base_url; without it this")
        print("probe would guess, and a guessed endpoint proves nothing.")
        return 1

    base = str(prov.get("base_url") or "").rstrip("/")
    key = str(prov.get("api_key") or "")
    print(f"provider {SLUG}: base={base}")
    print(f"  api_mode={prov.get('api_mode')!r}  enabled={prov.get('enabled')!r}")
    if not base or not key:
        print("  base_url or api_key not readable from the store — stopping.")
        return 1

    headers = {"Authorization": f"Bearer {key}"}
    msgs = [{"role": "user", "content": "Reply with the single word: pong"}]

    print("\nA direct to the provider, chat_completions shape:")
    print("  " + _post(f"{base}/chat/completions",
                      {"model": MODEL, "messages": msgs, "max_tokens": 32}, headers))

    print("\nB direct to the provider, responses shape (the other endpoint):")
    print("  " + _post(f"{base}/responses",
                      {"model": MODEL, "input": msgs, "max_tokens": 32}, headers))

    print("\nC through the gateway route:")
    print("  " + _post(GW, {"model": "hermes-default", "messages": msgs,
                            "max_tokens": 32},
                      {"Authorization": f"Bearer {GW_KEY}", "accept": "application/json"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
