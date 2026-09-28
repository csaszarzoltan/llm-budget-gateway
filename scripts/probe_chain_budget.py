"""Is the route chain budget spending itself on THINKING time?

`GATEWAY_ROUTE_TIMEOUT_BUDGET=115s` is a budget for the whole fallback
chain, and it is checked with `elapsed = time.perf_counter() - chain_started`
(verhe idea gw:1418). If one candidate thinks for 100s and then fails, the
next candidate starts with ~15s of budget left — so a chain that would
normally have answered 20s later is cut short, and the client sees a
timeout instead of an answer.

The reported symptom is exactly that shape: with the gateway the agent stops
early, with a direct provider it keeps going. This probe measures whether
the budget is consumed by think-time rather than by failures, which is the
difference between "the gateway caps the wait" and "the gateway caps the
work".

Read-only: it inspects recorded latencies against the configured budget and
then makes one live streaming call to see which model actually answers and
how long it took.

Run: python scripts/probe_chain_budget.py
"""
from __future__ import annotations

import json
import sqlite3
import time
import urllib.error
import urllib.request

DB = "/home/zoltan/llm-budget-gateway/gateway.db"
GATEWAY = "http://127.0.0.1:8000/v1/chat/completions"
KEY = "gw_dktUxnLbj6dPI0xklC_Cq77I0l1mbw"
BUDGET = 115.0


def recorded() -> None:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    print(f"=== recorded latencies vs the {BUDGET:.0f}s chain budget ===")
    rows = c.execute(
        "SELECT latency_ms, status_code, model FROM cost_records "
        "WHERE timestamp > ? AND latency_ms IS NOT NULL",
        (time.time() - 7200,),
    ).fetchall()
    lat = sorted(float(r[0]) / 1000 for r in rows)
    if not lat:
        print("  no rows")
        return
    over = [x for x in lat if x > BUDGET]
    print(f"  {len(lat)} recorded requests, slowest {lat[-1]:.0f}s")
    print(f"  over the {BUDGET:.0f}s budget: {len(over)} ({100 * len(over) / len(lat):.1f}%)")
    for p in (50, 90, 99):
        idx = min(len(lat) - 1, int(len(lat) * p / 100))
        print(f"  p{p}: {lat[idx]:.1f}s")
    print("  slowest 5:")
    for r in sorted(rows, key=lambda r: -float(r[0] or 0))[:5]:
        print(
            f"    {float(r[0]) / 1000:7.1f}s  {r[1]}  {r[2]}"
        )
    near = [x for x in lat if BUDGET * 0.5 < x <= BUDGET]
    print(
        f"\n  {len(near)} requests in the {BUDGET * 0.5:.0f}-{BUDGET:.0f}s band — "
        f"these are the ones a budget would have cut at the next fallback."
    )


def live() -> None:
    print("\n=== one live streamed call through the route ===")
    body = {
        "model": "hermes-default",
        "max_tokens": 300,
        "messages": [{"role": "user", "content": "Reply with just: pong"}],
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    req = urllib.request.Request(
        GATEWAY,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {KEY}",
            "content-type": "application/json",
            "accept": "text/event-stream",
        },
        method="POST",
    )
    t0 = time.monotonic()
    first = models = usage = None
    done = False
    try:
        with urllib.request.urlopen(req, timeout=400) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                now = time.monotonic()
                if first is None:
                    first = now - t0
                payload = line[5:].strip()
                if payload == "[DONE]":
                    done = True
                    break
                chunk = json.loads(payload)
                if chunk.get("model"):
                    models = chunk["model"]
                if chunk.get("usage"):
                    usage = chunk["usage"]
    except urllib.error.HTTPError as exc:
        print(f"  HTTP {exc.code}: {exc.read().decode()[:120]}")
        return
    print(
        f"  served={models}  first_byte={first:.1f}s  total={time.monotonic() - t0:.1f}s  "
        f"done={done}  out={(usage or {}).get('completion_tokens')}"
    )


def main() -> int:
    recorded()
    live()
    print("\nRun the gateway log and look for 'chain budget' lines:")
    print("  journalctl -u llm-budget-gateway | grep 'chain budget' | tail -20")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
