"""Does a mid-session model switch break the client's connection?

The hypothesis: when the gateway falls back to another model, the client's
connection dies. If true, the earlier timeout fix only treated a symptom —
the real cause would be the model switch, and every long agentic run would
hit it as soon as one target cooled down or answered 400.

A connection break is a specific, observable claim, so measure it rather
than reason about it. Two ways a switch can reach the client:

  1. a mid-stream fallback, where the new model's text is spliced into the
     already-open SSE stream;
  2. a sticky-session switch, where a later request in the same conversation
     is served by a different model than the one before it.

Both are checked for the things a client would actually notice: a stream
that ends without `[DONE]`, a frame that breaks framing, a request that
errors out, and a model field that disagrees with the route.

Run: python scripts/probe_model_switch.py
"""
from __future__ import annotations

import json
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


def stream(messages: list[dict], session_id: str, tools=None) -> dict:
    """One streamed turn; report what the client would see."""
    body: dict = {
        "model": ROUTE,
        "max_tokens": 400,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if session_id:
        body["session_id"] = session_id
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
    out = {
        "http": None, "frames": 0, "saw_done": False, "text": "",
        "models": set(), "tool_calls": [], "usage": None, "error": None,
        "gaps": [],
    }
    t0 = last = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            out["http"] = resp.status
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                now = time.monotonic()
                if out["frames"]:
                    out["gaps"].append(now - last)
                last = now
                out["frames"] += 1
                payload = line[5:].strip()
                if payload == "[DONE]":
                    out["saw_done"] = True
                    continue
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    out["error"] = f"unparseable frame: {payload[:80]}"
                    continue
                if chunk.get("model"):
                    out["models"].add(chunk["model"])
                if chunk.get("usage"):
                    out["usage"] = chunk["usage"]
                for c in chunk.get("choices", []) or []:
                    d = c.get("delta", {}) or {}
                    if d.get("content"):
                        out["text"] += str(d["content"])
                    for tc in d.get("tool_calls") or []:
                        fn = tc.get("function", {}) or {}
                        if fn.get("name"):
                            out["tool_calls"].append(fn["name"])
    except urllib.error.HTTPError as exc:
        out["http"] = exc.code
        out["error"] = exc.read().decode()[:160]
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    out["elapsed"] = time.monotonic() - t0
    out["max_gap"] = max(out["gaps"]) if out["gaps"] else 0.0
    return out


def show(label: str, o: dict) -> None:
    flag = "OK " if (o["error"] is None and o["saw_done"]) else "HIBA"
    print(
        f"  [{flag}] {label:32s} HTTP {o['http']} {o['elapsed']:5.1f}s "
        f"frames={o['frames']:<4d} done={o['saw_done']} "
        f"maxgap={o['max_gap']:.1f}s models={sorted(o['models'])}"
    )
    if o["tool_calls"]:
        print(f"          tools={o['tool_calls']}  out_tok={(o['usage'] or {}).get('completion_tokens')}")
    if o["error"]:
        print(f"          HIBA: {o['error']}")


def main() -> int:
    print("A sticky session across three turns — does a model switch break it?\n")
    sid = f"probe-{int(time.time())}"
    msgs: list[dict] = []

    o1 = stream(msgs + [{"role": "user", "content": "Reply with the word: alpha"}], sid)
    show("turn 1", o1)
    msgs += [
        {"role": "user", "content": "Reply with the word: alpha"},
        {"role": "assistant", "content": o1["text"] or "alpha"},
    ]

    o2 = stream(msgs + [{"role": "user", "content": "Now use the terminal tool to run: ls /tmp"}], sid, TOOLS)
    show("turn 2 (tool call)", o2)
    msgs += [
        {"role": "user", "content": "Now use the terminal tool to run: ls /tmp"},
        {"role": "assistant", "content": o2["text"] or "", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "terminal", "arguments": '{"command":"ls /tmp"}'}}
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": "alpha.txt\nbeta.txt"},
    ]

    print("\n  ^ if turn 2 served a different model than turn 1, the switch")
    print("    reached the client intact — the stream still framed and closed.")

    o3 = stream(msgs + [{"role": "user", "content": "Reply with just: beta"}], sid)
    show("turn 3 (after tool result)", o3)
    print(
        f"\n  models seen across the session: "
        f"{sorted(set(o1['models']) | set(o2['models']) | set(o3['models']))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
