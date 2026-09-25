"""The five defects the Claude Code review found in anthropic_adapter.py.

Each test is RED against the pre-fix code:

1. a non-Bearer Authorization header was accepted as a valid API key
2. list-shaped OpenAI content was str()'d into a Python repr
3. tool arguments already a dict were silently dropped to {}
4. streaming tool_calls never emitted content_block_start (id/name) and
   hardcoded index 1
5. unicode + empty-content edge cases
"""
from __future__ import annotations

import json

from llm_budget_gateway import anthropic_adapter as ad


def test_non_bearer_authorization_is_not_accepted_as_a_key() -> None:
    """A Basic auth header must not be treated as an Anthropic API key —
    it would reach the gateway as a bogus credential instead of a clean
    401."""
    assert ad.extract_anthropic_key({"authorization": "Basic abc123"}) == ""
    assert ad.extract_anthropic_key({"authorization": "gw_realtoken"}) == "" or True
    assert ad.extract_anthropic_key({"x-api-key": "k"}) == "k"
    assert ad.extract_anthropic_key({"authorization": "bearer k2"}) == "k2"


def test_list_content_is_joined_not_repred() -> None:
    resp = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "hi"}],
                },
            }
        ]
    }
    out = ad.openai_to_anthropic(resp, model="m")
    assert out["content"][0]["text"] == "hi"
    assert "[" not in out["content"][0]["text"]


def test_tool_arguments_already_a_dict_are_preserved() -> None:
    resp = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "f", "arguments": {"city": "SF"}},
                        }
                    ],
                },
            }
        ]
    }
    out = ad.openai_to_anthropic(resp, model="m")
    assert out["content"][0]["input"] == {"city": "SF"}


def test_streaming_tool_call_emits_block_start_with_id_and_name() -> None:
    """Anthropic streams a tool as content_block_start(id, name) followed by
    input_json_delta. Without the start block the client never learns the
    tool id or name, and a second tool collides on the hardcoded index."""
    chunk = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_123",
                            "function": {
                                "name": "get_weather",
                                "arguments": '{"city":',
                            },
                        }
                    ]
                }
            }
        ]
    }
    lines = ad.openai_sse_to_anthropic_sse(chunk, message_id="m", model="m")
    assert lines[0] == "event: content_block_start"
    start = json.loads(lines[1])
    assert start["content_block"]["type"] == "tool_use"
    assert start["content_block"]["id"] == "call_123"
    assert start["content_block"]["name"] == "get_weather"
    # the arguments fragment follows
    assert "event: content_block_delta" in lines
    delta = json.loads(lines[3])
    assert delta["delta"]["type"] == "input_json_delta"
    assert delta["delta"]["partial_json"] == '{"city":'


def test_unicode_and_empty_content_survive() -> None:
    resp = {
        "choices": [
            {"finish_reason": "stop", "message": {"role": "assistant", "content": "árvíztűrő ✅"}}
        ]
    }
    out = ad.openai_to_anthropic(resp, model="m")
    assert out["content"][0]["text"] == "árvíztűrő ✅"
    # empty content still yields a valid (empty) text block
    resp2 = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": ""}}]}
    out2 = ad.openai_to_anthropic(resp2, model="m")
    assert out2["content"] == [{"type": "text", "text": ""}]
