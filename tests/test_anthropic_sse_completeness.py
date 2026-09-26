"""The Anthropic SSE the gateway emits is missing events the SDK needs.

Claude Code, running against this gateway, stalls: it sits "thinking" and,
when it is waiting for a tool, it never continues. A direct call to the same
model does not. The gateway is the only difference, so the difference is in
what the gateway sends.

`openai_sse_to_anthropic_sse` maps one OpenAI chunk at a time and, at the
end, emits `message_stop` — and nothing else. Measured on a live gateway
call ("Say hi in three words", which returned 599 bytes):

    message_start          usage = {input_tokens: 0, output_tokens: 0}
    content_block_start
    content_block_delta
    message_stop

Three things are missing, and all three are load-bearing for the SDK:

  * NO `message_delta`. Anthropic delivers the final `stop_reason` and the
    real `usage` there. The client waits on it for a completion signal;
    without it the turn has no terminator, and the client falls back to its
    own internal timer — the visible stall.
  * NO `content_block_stop` — the text block is never closed.
  * `message_start` carries ZERO token counts, so the client believes the
    conversation so far cost nothing. With
    CLAUDE_CODE_MAX_CONTEXT_TOKENS=262144 the accounting drifts by the whole
    conversation.
"""
from __future__ import annotations

import json

from llm_budget_gateway.anthropic_adapter import openai_sse_to_anthropic_sse

MESSAGE_ID = "msg_test"


def _chunk(**delta) -> dict:
    return {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
    }


def _final(reason: str = "stop", usage: dict | None = None) -> dict:
    ev = {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "m",
        "choices": [{"index": 0, "delta": {}, "finish_reason": reason}],
    }
    if usage:
        ev["usage"] = usage
    return ev


def _all(chunks: list[dict]) -> list[dict]:
    """Drive the adapter over a chunk sequence the way the endpoint does.

    The adapter returns alternating "event: <name>" and bare-JSON lines —
    `_format_anthropic_sse` in main.py is what prepends `data:`. So a line
    is a payload when it does NOT start with "event:".
    """
    out: list[dict] = []
    for i, ch in enumerate(chunks):
        last = i == len(chunks) - 1
        for line in openai_sse_to_anthropic_sse(
            ch, message_id=MESSAGE_ID, model="m", emit_done=last
        ):
            if line.startswith("event:"):
                continue
            payload = line.strip()
            if payload:
                out.append(json.loads(payload))
    return out


def _types(chunks: list[dict]) -> list[str]:
    return [e["type"] for e in _all(chunks)]


def test_the_stream_ends_with_message_delta() -> None:
    types = _types([_chunk(role="assistant"), _chunk(content="hi"), _final()])
    assert "message_delta" in types, (
        "the stream has no message_delta, so the SDK never receives a final "
        f"stop_reason/usage and the turn has no completion signal; got {types}"
    )


def test_message_delta_carries_the_stop_reason() -> None:
    events = _all([_chunk(role="assistant"), _chunk(content="hi"), _final("stop")])
    delta = next(e for e in events if e["type"] == "message_delta")
    assert delta.get("delta", {}).get("stop_reason"), f"no stop_reason: {delta}"


def test_a_tool_block_the_adapter_opens_is_closed() -> None:
    """A tool block the adapter opened must be closed; the text block (0) is
    the wrapper's to close."""
    types = _types(
        [_chunk(role="assistant"), _tool_chunk(), _final("tool_calls")]
    )
    assert "content_block_stop" in types, f"tool block never closed; got {types}"


def test_message_delta_comes_last_before_message_stop() -> None:
    types = _types([_chunk(role="assistant"), _chunk(content="hi"), _final()])
    assert types[-1] == "message_stop", types
    assert types[-2] == "message_delta", (
        f"message_delta must be the last event before message_stop: {types}"
    )


def test_the_final_usage_is_reported() -> None:
    """The client needs real token counts, not the placeholder zeros in
    message_start, or its context accounting drifts by the whole turn."""
    usage = {"prompt_tokens": 1234, "completion_tokens": 56, "total_tokens": 1290}
    events = _all(
        [
            _chunk(role="assistant"),
            _chunk(content="hi"),
            _final("stop", usage=usage),
        ]
    )
    delta = next(e for e in events if e["type"] == "message_delta")
    assert delta.get("usage", {}).get("output_tokens") == 56, delta
    assert delta.get("usage", {}).get("input_tokens") == 1234, delta


def test_every_event_gets_its_own_data_line() -> None:
    """Each SSE event is its own block: `event: X` + `data: {...}`.

    `_format_anthropic_sse` pairs ONE event name with ONE data line, so a
    translation that emits several events in one call collapses them into a
    single block — the later ones end up as bare JSON with no `data:` prefix
    and no event name, which no SSE client can parse. Verified live before
    the fix, where the closing sequence came out as `event: message_stop`
    followed by three unprefixed JSON lines.
    """
    from llm_budget_gateway.anthropic_adapter import _format_anthropic_sse

    lines = openai_sse_to_anthropic_sse(
        _tool_chunk(),
        message_id=MESSAGE_ID,
        model="m",
    ) + openai_sse_to_anthropic_sse(
        _final("tool_calls", usage={"prompt_tokens": 13, "completion_tokens": 7}),
        message_id=MESSAGE_ID,
        model="m",
        emit_done=True,
    )
    blocks = [
        b
        for b in _format_anthropic_sse(lines).strip().split("\n\n")
        if b.strip()
    ]
    assert len(blocks) == 5, (
        "expected five separate SSE blocks (content_block_start, "
        f"content_block_delta, content_block_stop, message_delta, "
        f"message_stop) but got {len(blocks)}:\n" + "\n---\n".join(blocks)
    )
    for block in blocks:
        assert block.count("data:") == 1, f"block has no/2 data lines:\n{block}"
        assert "event:" in block, f"block has no event name:\n{block}"
        json.loads(
            [ln for ln in block.splitlines() if ln.startswith("data:")][0][5:].strip()
        )


def test_text_still_arrives_intact() -> None:
    """The fix must not truncate content — this already worked, so it guards
    the change."""
    events = _all(
        [
            _chunk(role="assistant"),
            _chunk(content="Hey "),
            _chunk(content="there, "),
            _chunk(content="friend!"),
            _final(),
        ]
    )
    text = "".join(
        e["delta"].get("text", "")
        for e in events
        if e["type"] == "content_block_delta"
    )
    assert text == "Hey there, friend!", repr(text)


def _tool_chunk(index: int = 0, name: str = "Bash", args: str = '{"command":"ls"}') -> dict:
    return {
        "id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "m",
        "choices": [
            {
                "index": 0,
                "delta": {
                    "tool_calls": [
                        {
                            "index": index,
                            "id": f"call_{index}",
                            "function": {"name": name, "arguments": args},
                        }
                    ]
                },
                "finish_reason": None,
            }
        ],
    }


def test_a_tool_call_still_closes_its_block() -> None:
    """Tool blocks must close too, or a waiting client never gets its turn
    back — the 'it stops when it waits for something' symptom."""
    events = _all([_chunk(role="assistant"), _tool_chunk(), _final("tool_calls")])
    types = [e["type"] for e in events]
    assert "content_block_start" in types, types
    assert "content_block_stop" in types, f"the tool_use block is never closed: {types}"
    assert types[-2] == "message_delta", types
    stop = next(e for e in events if e["type"] == "message_delta")
    # Anthropic's vocabulary is "tool_use"; OpenAI's is "tool_calls". The
    # adapter has to translate, and this is the value the SDK reads to know
    # it should hand control back for the tool to run.
    assert stop["delta"]["stop_reason"] == "tool_use", stop



def _tool_chunk(index: int = 0, name: str = "Bash", args: str = '{"command":"ls"}') -> dict:
    return {
        "id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "m",
        "choices": [
            {
                "index": 0,
                "delta": {
                    "tool_calls": [
                        {
                            "index": index,
                            "id": f"call_{index}",
                            "function": {"name": name, "arguments": args},
                        }
                    ]
                },
                "finish_reason": None,
            }
        ],
    }


def _drive(chunks: list[dict]) -> list[dict]:
    """Run the adapter over a chunk sequence, emit_done on the last one."""
    lines: list[str] = []
    for i, ch in enumerate(chunks):
        lines += openai_sse_to_anthropic_sse(
            ch, message_id=MESSAGE_ID, model="m", emit_done=(i == len(chunks) - 1)
        )
    return [json.loads(ln.strip()) for ln in lines if not ln.startswith("event:")]


def test_every_tool_block_the_adapter_opens_is_closed() -> None:
    """`emit_done` closed only index 0 — a block the adapter never opened.

    A turn that streams text and then a tool call opens a tool_use block;
    the closing sequence stopped at 0, leaving the tool block unterminated.
    Claude Code cannot parse an unterminated tool_use block and answers
    "The model's tool call could not be parsed (retry also failed)" —
    reproduced live, and it is the 'it stops when it waits for something'
    symptom. Every block this adapter opened must get its own stop.
    """
    events = _drive(
        [
            _chunk(role="assistant"),
            _chunk(content="let me look"),
            _tool_chunk(0, "Read", '{"file_path":"/etc/hostname"}'),
            _final("tool_calls"),
        ]
    )
    opened = {e["index"] for e in events if e["type"] == "content_block_start"}
    closed = {e["index"] for e in events if e["type"] == "content_block_stop"}
    assert opened, "the fixture opened no tool block — the test proves nothing"
    assert opened == closed, (
        f"the adapter opened block(s) {sorted(opened)} but closed "
        f"{sorted(closed)}; an unterminated tool_use block is unparseable"
    )


def test_two_concurrent_tool_blocks_both_close() -> None:
    events = _drive(
        [
            _chunk(role="assistant"),
            _tool_chunk(0, "Read", '{"file_path":"/a"}'),
            _tool_chunk(1, "Bash", '{"command":"ls"}'),
            _final("tool_calls"),
        ]
    )
    opened = {e["index"] for e in events if e["type"] == "content_block_start"}
    closed = {e["index"] for e in events if e["type"] == "content_block_stop"}
    assert len(opened) == 2, opened
    assert opened == closed, f"opened {sorted(opened)} closed {sorted(closed)}"


def test_a_text_only_turn_still_closes_its_block() -> None:
    """The common case: no tool call, so the endpoint wrapper's own closing
    of index 0 is the only one, and it must still be emitted."""
    from llm_budget_gateway.anthropic_adapter import _format_anthropic_sse

    done = openai_sse_to_anthropic_sse(
        _final("stop", usage={"prompt_tokens": 3, "completion_tokens": 2}),
        message_id=MESSAGE_ID, model="m", emit_done=True,
    )
    names = [ln[7:] for ln in done if ln.startswith("event: ")]
    assert "message_delta" in names, names
    frames = [
        f for f in _format_anthropic_sse(done).strip().split("\n\n") if f.strip()
    ]
    assert all(f.count("data:") == 1 for f in frames), frames


def test_a_text_only_turn_emits_no_duplicate_block_stop() -> None:
    """The wrapper closes index 0; the adapter must not close it again.

    The adapter used to fall back to `[0]` when it had opened nothing, which
    produced two `content_block_stop` frames for index 0 in a tool-less
    follow-up turn (assistant tool_use -> tool_result -> text). The second
    stop has no open block and the client rejects the stream with "The
    response stream was malformed" — reproduced live.
    """
    lines = openai_sse_to_anthropic_sse(
        _final("stop", usage={"prompt_tokens": 6, "completion_tokens": 4}),
        message_id=MESSAGE_ID, model="m", emit_done=True,
    )
    stops = [
        json.loads(ln.strip())
        for ln in lines
        if not ln.startswith("event:") and "content_block_stop" in ln
    ]
    assert stops == [], (
        f"the adapter opened no block, so it must emit no stop, but emitted "
        f"{stops} — the wrapper already closed index 0"
    )
