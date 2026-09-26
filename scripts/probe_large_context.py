"""Reproduce a large-context request against the LIVE gateway.

RESULT (2026-09-25, hermes-default): context size is NOT the problem. First
byte at 400 / 20k / 80k / 240k prompt tokens came in 6.3s / 8.7s / 11.4s /
11.6s — the prefill is fast.

What actually killed long Claude Code turns was a different clock: the route
target's timeout_seconds (90) doubled as the chunk-to-chunk stream deadline,
and a reasoning model's measured mid-stream thinking gap was 61s. See
scripts/measure_stream_gaps.py and the stream_idle_timeout setting.

Kept as the regression check for that: if a future change makes large-context
first byte degrade again, this is what catches it.
"""
from __future__ import annotations

import json
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
    # The route IS the model: Claude Code sends the alias `hermes-default` in
    # the `model` field, and the gateway resolves it through the UI-managed
    # route chain. Passing a concrete provider model here returns 404
    # "unknown route" — there is no separate route parameter in the body.
    model = "hermes-default"
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
