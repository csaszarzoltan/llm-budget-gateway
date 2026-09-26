"""Measure the chunk-to-chunk gap on a real hermes-default stream.

The gateway enforces `_iter_with_timeout(deadline=target_timeout=90)` as a
CHUNK-TO-CHUNK deadline, not a total budget. A reasoning model legitimately
goes silent for minutes between events, so a 90s gap kills a stream that
has already delivered tens of thousands of tokens to the client — the
"Claude Code stopped and waits" symptom.

This prints every inter-chunk gap over a real long stream so the right
threshold is a measurement, not a guess.
"""
from __future__ import annotations

import json
import time
import urllib.request

GATEWAY = "http://127.0.0.1:8000"
KEY = "gw_dktUxnLbj6dPI0xklC_Cq77I0l1mbw"
MODEL = "hermes-default"
PROMPT = (
    "Think carefully and at length about the design of a distributed rate "
    "limiter, then explain it in detail."
)


def main() -> None:
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": True,
        "max_tokens": 20000,
    }
    req = urllib.request.Request(
        f"{GATEWAY}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    started = time.perf_counter()
    gaps: list[tuple[float, int]] = []
    last = started
    frames = 0
    chars = 0
    with urllib.request.urlopen(req, timeout=900) as resp:
        print("status", resp.status, "content-type", resp.headers.get("content-type"))
        for raw in resp:
            now = time.perf_counter()
            line = raw.decode("utf-8", "replace")
            if not line.strip():
                continue
            gaps.append((now - last, len(line)))
            last = now
            frames += 1
            chars += len(line)
    total = time.perf_counter() - started
    gaps.sort(reverse=True)
    print(f"frames={frames} chars={chars} total={total:.1f}s")
    print("top 10 inter-chunk gaps (seconds, line length):")
    for g, n in gaps[:10]:
        print(f"  {g:8.1f}s  len={n}")
    if gaps:
        allg = sorted(d for d, _ in gaps)
        p = lambda q: allg[min(len(allg) - 1, int(len(allg) * q))]  # noqa: E731
        print(f"median={p(0.5):.1f}s p95={p(0.95):.1f}s max={allg[-1]:.1f}s")


if __name__ == "__main__":
    main()
