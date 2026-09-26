"""Live probe: does a non-streaming answer that thinks for >90s survive?

This is the decisive check for the reported symptom. The route target
declares `timeout_seconds: 90`, and a scalar httpx timeout — plus the
`asyncio.wait_for(..., timeout=effective_timeout)` around the whole
non-streaming call — made 90s the wall-clock budget for think + answer
combined. A reasoning model that thinks for 100s and then answers perfectly
was killed, the `timeout` error parked the model for 90s, and every later
request skipped it.

With `split_timeout` the read phase is floored at
`stream_idle_timeout` (300s) and only the handshake is bounded by 90s, so a
long think must now return 200 with a real answer.

Run: python scripts/probe_long_think.py
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

GATEWAY = "http://127.0.0.1:8000/v1/messages"
KEY = "gw_dktUxnLbj6dPI0xklC_Cq77I0l1mbw"

# A prompt that reliably makes the reasoning models think for a long time
# before answering. The point is total elapsed time, not the answer.
PROMPT = (
    "Think very carefully, step by step, before answering. Work through this "
    "deliberately and at length: compare the tradeoffs of at least four "
    "different approaches to designing a durable job queue with retries and "
    "dead-letter handling, considering ordering guarantees, idempotency, "
    "backpressure, observability, and failure recovery. Then give a final "
    "recommendation. " * 4
)


def main() -> int:
    print(f"route=hermes-default  (target timeout_seconds=90, idle=300)")
    print(f"prompt ~{len(PROMPT) // 4} chars\n")
    for attempt in (1, 2):
        body = {
            "model": "hermes-default",
            "max_tokens": 4000,
            "messages": [{"role": "user", "content": PROMPT}],
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
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = json.loads(resp.read())
            took = time.monotonic() - t0
            text = "".join(
                c.get("text", "") for c in data.get("content", [])
                if isinstance(c, dict)
            )
            usage = data.get("usage", {}) or {}
            over = "  <-- OVER 90s" if took > 90 else ""
            print(
                f"attempt {attempt}: HTTP 200 in {took:6.1f}s  "
                f"stop={data.get('stop_reason')}  "
                f"out={usage.get('output_tokens')} tok  "
                f"text={len(text)} chars{over}"
            )
            print(f"  first words: {text[:90]!r}")
        except urllib.error.HTTPError as exc:
            took = time.monotonic() - t0
            detail = exc.read().decode()[:200]
            print(f"attempt {attempt}: HTTP {exc.code} after {took:6.1f}s  {detail}")
        except Exception as exc:  # noqa: BLE001
            took = time.monotonic() - t0
            print(f"attempt {attempt}: {type(exc).__name__} after {took:6.1f}s  {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
