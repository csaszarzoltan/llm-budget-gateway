"""Reproduce the large-context stall against the LIVE gateway.

Hypothesis: hermes-default pins every target at timeout_seconds=90. A
Claude Code request carries 100k+ prompt tokens, so the upstream PREFILL
alone can exceed 90s and the request dies BEFORE the first chunk — while a
5-token "pong" passes easily. That is why the agent works for me and fails
for the user.

This measures first-byte latency as a function of prompt size, and shows
whether the gateway's own timeout fires or the upstream is simply slow.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

GATEWAY = "http://127.0.0.1:8000"
KEY = "gw_dktUxnLbj6dPI0xklC_Cq77I0l1mbw"
FILLER = "The quick brown fox jumps over the lazy dog. " * 40  # ~360 words


def post(path: str, body: dict, stream: bool) -> tuple[int, float, str]:
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{GATEWAY}{path}",
        data=payload,
        headers={
            "Authorization": f"Bearer {KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            if not stream:
                return resp.status, time.perf_counter() - started, resp.read().decode()[:200]
            first_byte = None
            frames = 0
            chars = 0
            for raw in resp:
                if first_byte is None:
                    first_byte = time.perf_counter() - started
                line = raw.decode("utf-8", "replace")
                if line.startswith("data:"):
                    frames += 1
                    chars += len(line)
            total = time.perf_counter() - started
            return resp.status, (first_byte or total), f"frames={frames} chars={chars} total={total:.1f}s"
    except urllib.error.HTTPError as exc:
        return exc.code, time.perf_counter() - started, exc.read().decode()[:300]
    except Exception as exc:  # noqa: BLE001
        return -1, time.perf_counter() - started, f"{type(exc).__name__}: {exc}"


def main() -> None:
    model = sys.argv[2] if len(sys.argv) > 2 else "@opencode-go/space-bunny-free"

    print(f"model alias (the route) = {model}")
    print(f"{'approx_prompt_tokens':>20}  {'status':>6}  {'first_byte_s':>13}  detail")

    for repeats in (1, 50, 200, 600):
        # ~1.3 tokens per filler sentence in this rough estimate
        approx = repeats * 40 * 10
        messages = [
            {"role": "user", "content": FILLER * repeats + "\n\nReply with exactly: pong"}
        ]
        status, first, detail = post(
            "/v1/chat/completions",
            {
                "model": "hermes-default",  # the route IS the model alias Claude Code sends
                "messages": messages,
                "stream": True,
                "max_tokens": 16,
                
            },
            stream=True,
        )
        print(f"{approx:>20}  {status:>6}  {first:>13.1f}  {detail[:60]}")


if __name__ == "__main__":

    main()
