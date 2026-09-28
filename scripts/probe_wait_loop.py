"""Is the connection dropped WHILE the client waits, or is the wait itself fine?

The symptom, again: an agent "stops and waits" and never continues when a
long command has to be waited on. Two very different causes look identical
from the outside:

  A  the connection dies during the wait — the next request fails to connect
     or comes back on a dead socket, so the client gives up;
  B  the wait is fine and the NEXT request is the one that stalls.

They are distinguishable, and the distinction decides the fix, so measure it
rather than infer it. A realistic agent loop is simulated: turn 1 asks for a
tool, the "terminal" runs for WAIT seconds, then turn 2 posts the result.
Everything is timed per request, and the socket state is checked between.

Run: python scripts/probe_wait_loop.py [WAIT_SECONDS]
"""
from __future__ import annotations

import json
import socket
import sys
import time
import urllib.error
import urllib.request

GATEWAY = "http://127.0.0.1:8000/v1/chat/completions"
KEY = "gw_dktUxnLbj6dPI0xklC_Cq77I0l1mbw"
ROUTE = "hermes-default"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "terminal",
            "description": "run a shell command",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    }
]


def _connect_probe() -> str:
    """Is the gateway still accepting connections at all?"""
    t0 = time.monotonic()
    try:
        with socket.create_connection(("127.0.0.1", 8000), timeout=10):
            return f"connect OK in {(time.monotonic() - t0) * 1000:.0f}ms"
    except Exception as exc:  # noqa: BLE001
        return f"connect FAILED: {type(exc).__name__}: {exc}"


def turn(messages: list[dict], tools=None, label: str = "") -> dict:
    body: dict = {
        "model": ROUTE,
        "max_tokens": 400,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        body["tools"] = tools
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
    res: dict = {"text": "", "models": set(), "done": False, "frames": 0,
                 "err": None, "tools": [], "elapsed": 0.0, "http": None}
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            res["http"] = resp.status
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                res["frames"] += 1
                payload = line[5:].strip()
                if payload == "[DONE]":
                    res["done"] = True
                    continue
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    res["err"] = f"unparseable: {payload[:70]}"
                    continue
                if chunk.get("model"):
                    res["models"].add(chunk["model"])
                for c in chunk.get("choices", []) or []:
                    d = c.get("delta", {}) or {}
                    if d.get("content"):
                        res["text"] += str(d["content"])
                    for tc in d.get("tool_calls") or []:
                        fn = tc.get("function", {}) or {}
                        if fn.get("name"):
                            res["tools"].append(fn["name"])
    except urllib.error.HTTPError as exc:
        res["http"] = exc.code
        res["err"] = exc.read().decode()[:150]
    except Exception as exc:  # noqa: BLE001
        res["err"] = f"{type(exc).__name__}: {exc}"
    res["elapsed"] = time.monotonic() - t0
    return res


def show(label: str, r: dict) -> None:
    ok = r["err"] is None and r["done"]
    print(
        f"  [{'OK ' if ok else 'HIBA'}] {label:26s} HTTP {r['http']} "
        f"{r['elapsed']:6.1f}s frames={r['frames']:<4d} done={r['done']} "
        f"models={sorted(r['models'])}"
    )
    if r["tools"]:
        print(f"          tool={r['tools']}")
    if r["err"]:
        print(f"          {r['err']}")


def main() -> int:
    wait = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    print(f"agent loop with a {wait}s 'terminal command' in the middle\n")

    t1 = turn([{"role": "user", "content": "Use the terminal tool to run: sleep"}],
              TOOLS)
    show("turn 1 (tool call)", t1)

    print(f"\n  ... client runs the command for {wait}s (the gateway is idle) ...")
    print(f"  {time.strftime('%H:%M:%S')}  {_connect_probe()}")
    for marker in range(1, 4):
        time.sleep(wait / 4)
        print(f"  {time.strftime('%H:%M:%S')}  t+{wait * marker // 4:>3}s  {_connect_probe()}")

    t2 = turn(
        [
            {"role": "user", "content": "Use the terminal tool to run: sleep"},
            {"role": "assistant", "content": t1["text"] or "", "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "terminal", "arguments": '{"command":"sleep"}'}}]},
            {"role": "tool", "tool_call_id": "call_1",
             "content": f"done after {wait}s"},
            {"role": "user", "content": "Reply with just: continued"},
        ],
        TOOLS,
    )
    show(f"turn 2 (after {wait}s wait)", t2)
    print(f"\n  answer: {t2['text'][:80]!r}")
    if t1["models"] and t2["models"] and t1["models"] != t2["models"]:
        print(f"  MODEL SWITCHED mid-session: {sorted(t1['models'])} -> {sorted(t2['models'])}")
        print("  ^ and the client still got a well-formed, closed stream above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
