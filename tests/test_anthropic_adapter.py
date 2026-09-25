"""Tests for the Anthropic Messages edge adapter (Claude Code compat).

RED phase: the adapter module + /v1/messages route do not exist yet.
Covers: text in/out, tool_use <-> tool_calls both directions, tool_result
splice, system prompt, tool_choice mapping, stop_reason mapping, error
shape, x-api-key auth, streaming SSE event translation.
"""

from __future__ import annotations

import json

import pytest

from llm_budget_gateway import anthropic_adapter as ad


def test_text_roundtrip() -> None:
    body = {
        "model": "hermes-default",
        "max_tokens": 64,
        "system": "you are helpful",
        "messages": [{"role": "user", "content": "ping"}],
    }
    oai = ad.anthropic_to_openai(body)
    assert oai["model"] == "hermes-default"
    assert oai["max_tokens"] == 64
    assert oai["messages"][0] == {"role": "system", "content": "you are helpful"}
    assert oai["messages"][1] == {"role": "user", "content": "ping"}


def test_assistant_tool_use_to_openai() -> None:
    body = {
        "model": "m",
        "max_tokens": 64,
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "looking"},
                    {"type": "tool_use", "id": "tu_1", "name": "read", "input": {"p": "/x"}},
                ],
            }
        ],
    }
    oai = ad.anthropic_to_openai(body)
    amsg = oai["messages"][0]
    assert amsg["role"] == "assistant"
    assert amsg["content"] == "looking"
    assert amsg["tool_calls"][0]["id"] == "tu_1"
    assert amsg["tool_calls"][0]["function"]["name"] == "read"
    assert json.loads(amsg["tool_calls"][0]["function"]["arguments"]) == {"p": "/x"}


def test_tool_result_splice() -> None:
    body = {
        "model": "m",
        "max_tokens": 64,
        "messages": [
            {"role": "user", "content": "read x"},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "tu_1", "name": "read", "input": {}}],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu_1", "content": "file-bytes"}
                ],
            },
        ],
    }
    oai = ad.anthropic_to_openai(body)
    roles = [m["role"] for m in oai["messages"]]
    assert roles == ["user", "assistant", "tool"]
    assert oai["messages"][2]["tool_call_id"] == "tu_1"
    assert oai["messages"][2]["content"] == "file-bytes"


def test_tools_and_choice_mapping() -> None:
    body = {
        "model": "m",
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "name": "read",
                "description": "read a file",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
        "tool_choice": {"type": "any"},
    }
    oai = ad.anthropic_to_openai(body)
    assert oai["tools"][0]["function"]["name"] == "read"
    assert oai["tools"][0]["function"]["parameters"] == {"type": "object", "properties": {}}
    assert oai["tool_choice"] == "required"


def test_openai_to_anthropic_text_and_usage() -> None:
    resp = {
        "id": "chatcmpl-1",
        "choices": [
            {"finish_reason": "stop", "message": {"role": "assistant", "content": "pong"}}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3},
    }
    out = ad.openai_to_anthropic(resp, model="hermes-default")
    assert out["type"] == "message"
    assert out["content"] == [{"type": "text", "text": "pong"}]
    assert out["stop_reason"] == "end_turn"
    assert out["usage"] == {"input_tokens": 10, "output_tokens": 3}


def test_openai_to_anthropic_tool_calls_and_length() -> None:
    resp = {
        "id": "chatcmpl-2",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_9",
                            "type": "function",
                            "function": {"name": "read", "arguments": '{"p":1}'},
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 7},
    }
    out = ad.openai_to_anthropic(resp, model="m")
    assert out["stop_reason"] == "tool_use"
    tu = out["content"][0]
    assert tu["type"] == "tool_use" and tu["id"] == "call_9"
    assert tu["input"] == {"p": 1}


def test_sse_text_delta_translation() -> None:
    chunk = {"choices": [{"delta": {"content": "hel"}}]}
    lines = ad.openai_sse_to_anthropic_sse(chunk, message_id="msg_1", model="m")
    assert lines[0] == "event: content_block_delta"
    payload = json.loads(lines[1])
    assert payload["delta"] == {"type": "text_delta", "text": "hel"}


def test_key_extraction_prefers_x_api_key() -> None:
    assert ad.extract_anthropic_key({"x-api-key": "k1"}) == "k1"
    assert ad.extract_anthropic_key({"Authorization": "Bearer k2"}) == "k2"
    assert ad.extract_anthropic_key({}) == ""


def test_system_role_inside_messages_is_accepted() -> None:
    """Claude Code sends the system prompt as a `system`-role ROW inside
    `messages`, not (only) the top-level `system` field. Anthropic tolerates
    it; an OpenAI-shaped upstream does not, so the adapter must fold it into
    a leading system message instead of raising 400 unsupported role."""
    body = {
        "model": "m",
        "max_tokens": 64,
        "messages": [
            {"role": "system", "content": "you are Claude Code"},
            {"role": "user", "content": "hi"},
        ],
    }
    oai = ad.anthropic_to_openai(body)
    assert oai["messages"][0] == {"role": "system", "content": "you are Claude Code"}
    assert oai["messages"][1] == {"role": "user", "content": "hi"}


def test_system_role_string_content_is_accepted() -> None:
    body = {
        "model": "m",
        "max_tokens": 64,
        "messages": [
            {"role": "system", "content": "sys-as-string"},
            {"role": "user", "content": "hi"},
        ],
    }
    oai = ad.anthropic_to_openai(body)
    assert oai["messages"][0]["content"] == "sys-as-string"


def test_format_anthropic_sse_block_shape() -> None:
    """SSE framing: the event name and its data must be CONSECUTIVE lines in
    ONE block. Claude Code rejects the whole stream with "Could not parse
    message into JSON" otherwise — it surfaced against this endpoint."""
    lines = ad.anthropic_message_start(message_id="msg_1", model="m")
    block = ad._format_anthropic_sse(lines)
    assert block.startswith("event: message_start\ndata: {")
    assert block.endswith("\n\n")
    assert "data: event:" not in block
    # no choices -> no lines -> emit NOTHING (a bare `data: null` breaks it)
    assert ad._format_anthropic_sse([]) == ""
    assert ad._format_anthropic_sse(ad.openai_sse_to_anthropic_sse(
        {"choices": []}, message_id="m", model="m"
    )) == ""
    # done event carries a JSON object, not the literal null
    done = ad._format_anthropic_sse(
        ad.openai_sse_to_anthropic_sse({}, message_id="m", model="m", emit_done=True)
    )
    assert "event: message_stop" in done
    assert json.loads(done.split("data: ", 1)[1]) == {"type": "message_stop"}


def test_create_app_serves_v1_messages() -> None:
    from llm_budget_gateway.main import create_app

    app = create_app()
    routes = {getattr(r, "path", None) for r in app.routes}
    assert "/v1/messages" in routes
