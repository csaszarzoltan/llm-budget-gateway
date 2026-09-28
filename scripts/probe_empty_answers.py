"""Why does `@nvidia/z-ai/glm-5.3` return HTTP 200 with zero tokens?

Measured on the live gateway, last 4 hours:

    26 of 27 empty answers are this model
    200  in=0  out=0   7-36s
    200  in=0  out=0  24.2s
    200  in=0  out=0   8.1s

A 200 with no output is worse than an error: the fallback manager records it
as a SUCCESS with $0 of output and does NOT try the next candidate, so the
client receives an empty answer. The gateway already has a guard for this on
the STREAM path ("upstream returned an empty stream (no first chunk)" ->
502) and on the direct-responses path. This probe asks the same model both
ways, so the gap is visible:

  A  non-streaming /v1/messages  through the route, pinned to the model
  B  a direct non-streaming call so the raw upstream body can be inspected

A model that answers 200/empty is a defect to route around (or report), not
something to fix in this script — so this script only reports.

Run: python scripts/probe_empty_answers.py
"""
from __future__ import annotations

import json
import sqlite3
import time
import urllib.error
import urllib.request

GATEWAY = "http://127.0.0.1:8000/v1/messages"
KEY = "gw_dktUxnLbj6dPI0xklC_Cq77I0l1mbw"
MODEL = "@nvidia/z-ai/glm-5.3"
DB = "/home/zoltan/llm-budget-gateway/gateway.db"


def _call(model: str, label: str) -> dict | None:
    body = {
        "model": model,
        "max_tokens": 200,
        "messages": [{"role": "user", "content": "Reply with the single word: pong"}],
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
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
        took = time.monotonic() - t0
        text = "".join(
            c.get("text", "") for c in data.get("content", []) if isinstance(c, dict)
        )
        usage = data.get("usage", {}) or {}
        print(
            f"  {label:28s} HTTP 200 in {took:5.1f}s  stop={data.get('stop_reason')}"
            f"  out={usage.get('output_tokens')}  text={text[:60]!r}"
        )
        return data
    except urllib.error.HTTPError as exc:
        took = time.monotonic() - t0
        print(
            f"  {label:28s} HTTP {exc.code} in {took:5.1f}s  "
            f"{exc.read().decode()[:120]}"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  {label:28s} {type(exc).__name__}: {exc}")
    return None


def main() -> int:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    print("=== what the DB recorded for this model, last 2 hours ===")
    for ts, sc, pt, ct, lm in c.execute(
        "SELECT timestamp,status_code,prompt_tokens,completion_tokens,latency_ms "
        "FROM cost_records WHERE model=? AND timestamp>? ORDER BY timestamp DESC LIMIT 8",
        (MODEL, time.time() - 7200),
    ):
        import datetime

        print(
            f"  {datetime.datetime.fromtimestamp(ts).strftime('%H:%M:%S')} "
            f"{sc} in={pt} out={ct} {float(lm or 0) / 1000:.1f}s"
        )

    print("\n=== live, pinned to the model ===")
    _call(MODEL, "pinned model, non-stream")
    print("\n=== live, through the route ===")
    _call("hermes-default", "route=hermes-default")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
