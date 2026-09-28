"""Which opencode-go models work on /chat/completions but not on /responses?

Established live (scripts/probe_wire_shape.py), same model, same credential,
same x-opencode-session header:

    POST /chat/completions  -> 200  space-bunny-free answers
    POST /responses         -> 400  ModelProtocolUnsupported

So `api_mode=codex_responses` on the whole opencode-go provider is wrong for
at least some of its models, and the gateway parks those for 30 days as
"retired / unavailable" when they are perfectly alive. The user's own direct
client uses /chat/completions, which is why the model works for them.

`provider_direct.py:_responses_endpoint_unavailable` already recovers from
the sibling refusal — a 503 "Endpoint is unavailable" — by retrying the same
model on the other endpoint within the attempt. The 400 refusal is the same
thing spelled differently and is NOT matched, so those models are never
recovered; they just burn a chain slot and get parked.

This probe sweeps every model the gateway has on this provider, on both
endpoints, so the fix can be sized: is this one model, or the provider.

Run: sudo python scripts/probe_responses_support.py
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
MESSAGES = [{"role": "user", "content": "Reply with the single word: pong"}]
SAMPLE = 14


def _store() -> ProviderConnectionStore:
    return ProviderConnectionStore(
        sqlite3.connect(str(DATA / "providers.db"), check_same_thread=False),
        CredentialVault(DATA / "provider-master.key"),
    )


def _try(label: str, url: str, body: dict, headers: dict) -> tuple[str, str]:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={**headers, "content-type": "application/json"}, method="POST",
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            resp.read(2048)
        return "200", f"{time.monotonic() - t0:4.1f}s"
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()[:120]
        kind = ""
        for name in ("ModelProtocolUnsupported", "Endpoint is unavailable",
                     "MissingSessionID", "not found", "model_not_found"):
            if name in raw:
                kind = name
                break
        return str(exc.code), kind or raw[:60]
    except Exception as exc:  # noqa: BLE001
        return "ERR", type(exc).__name__


def main() -> int:
    store = _store()
    conn = next(c for c in store.list() if str(c["slug"]) == SLUG)
    secret = store.connection_secret(str(conn["id"]))
    base = str(secret["base_url"]).rstrip("/")
    headers = {
        "Authorization": f"Bearer {secret['api_key']}",
        "User-Agent": str(secret.get("user_agent") or "opencode/1.18.31"),
        "x-opencode-session": "probe-support-sweep",
    }
    models = [str(m["id"]) for m in store.models(str(conn["id"]))]
    models = sorted(models)
    print(f"{SLUG}: api_mode={secret.get('api_mode')}  base={base}")
    print(f"{len(models)} models known to the gateway; sweeping up to {SAMPLE}.\n")
    print(f"  {'model':44s} {'/chat':28s} {'/responses':28s}")
    print("  " + "model".ljust(44) + "chat".ljust(28) + "responses")

    same = only_chat = only_responses = neither = 0
    for m in models[:SAMPLE]:
        cs, cnote = _try("chat", f"{base}/chat/completions",
                         {"model": m, "messages": MESSAGES, "max_tokens": 32}, headers)
        rs, rnote = _try("resp", f"{base}/responses",
                         {"model": m, "input": MESSAGES, "max_tokens": 32}, headers)
        if cs == "200" and rs == "200":
            same += 1
        elif cs == "200":
            only_chat += 1
        elif rs == "200":
            only_responses += 1
        else:
            neither += 1
        flag = ""
        if cs == "200" and rs != "200":
            flag = "  <-- PARKED WRONGLY"
        elif cs != "200" and rs == "200":
            flag = "  (responses only)"
        print(f"  {m:44s} {cs + ' ' + cnote:28s} {rs + ' ' + rnote:28s}{flag}")

    print(
        f"\n  both: {same}   chat-only: {only_chat}   "
        f"responses-only: {only_responses}   neither: {neither}"
    )
    if only_chat:
        print(
            f"\n  {only_chat} model(s) are alive and parked as 'retired'. "
            f"api_mode=codex_responses sends them to an endpoint they refuse."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
