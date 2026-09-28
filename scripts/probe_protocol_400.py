"""Why does a request still hit a model that is parked for 30 days?

Live evidence: `@opencode-go/glm-5.3-flash` is parked in `model_cooldowns`
with `{"type": "terminal", "status_code": 400, ...ModelProtocolUnsupported...}`
and 43196 minutes remaining, yet `cost_records` shows 400s against it
afterwards — each a single row, `prompt_tokens=0`, its own request_id, with
no other candidate around it in the same request.

So it is not the route chain: `probe_route` and `_run_candidate_chain` both
consult `model_in_cooldown(route, model)`. Something resolves the model name
and calls the provider WITHOUT consulting the cooldown table. The candidates
are the direct-model paths, which is where a client asking for a concrete
model (rather than a route) lands.

This probe asks for the concrete model directly, and separately asks for the
route, so the two can be told apart by what the gateway returns.

Run: python scripts/probe_protocol_400.py
"""
from __future__ import annotations

import json
import sqlite3
import time
import urllib.error
import urllib.request

GATEWAY = "http://127.0.0.1:8000/v1/messages"
KEY = "gw_dktUxnLbj6dPI0xklC_Cq77I0l1mbw"
PARKED = "@opencode-go/glm-5.3-flash"
DB = "/home/zoltan/llm-budget-gateway/gateway.db"


def _call(model: str, label: str) -> None:
    body = {
        "model": model,
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "ping"}],
    }
    req = urllib.request.Request(
        GATEWAY,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {KEY}",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp.read()
        print(f"  {label:34s} HTTP 200 in {time.monotonic() - t0:5.1f}s")
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode()[:150]
        print(
            f"  {label:34s} HTTP {exc.code} in {time.monotonic() - t0:5.1f}s  "
            f"{body_text}"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  {label:34s} {type(exc).__name__}: {exc}")


def main() -> int:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = c.execute(
        "SELECT until_ts FROM model_cooldowns "
        "WHERE route='hermes-default' AND model=?",
        (PARKED,),
    ).fetchone()
    if row is None:
        print(f"{PARKED} is NOT parked")
    else:
        print(
            f"{PARKED} parked for another "
            f"{(int(row[0]) - time.time()) / 60:.0f} min\n"
        )

    print("A concrete model name (what a client asking for one model sends):")
    _call(PARKED, f"model={PARKED}")

    print("\nB the route name (goes through the cooldown-checking chain):")
    _call("hermes-default", "model=hermes-default")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
