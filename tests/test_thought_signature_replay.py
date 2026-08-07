"""Thought-signature replay across tool-name rewriting (Gemini 400 fix)."""

from __future__ import annotations

import asyncio
import sqlite3

import httpx
import pytest

from llm_budget_gateway.provider_direct import DirectProviderClient


def _make_client(tmp_path) -> DirectProviderClient:
    registry = {
        "google": {
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
            "api_key_env": "UNUSED",
            "api_key": "test-key",
            "models": ["gemini-3.6-flash"],
        }
    }
    sig_db = tmp_path / "sig.db"
    return DirectProviderClient(registry, signature_db_path=str(sig_db))


def test_signature_stored_under_rewritten_name_and_lookup_tries_colon_form(tmp_path):
    """A signature captured under the REWRITTEN name (ns_fn) must be replayable
    with the original colon-qualified name (ns:fn) — the Gemini 400 fix."""
    client = _make_client(tmp_path)

    # Gemini returns the REWRITTEN name (default_api_skill_view) because the
    # gateway rewrote default_api:skill_view before the outbound call.
    data = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "id": "tc1",
                            "type": "function",
                            "function": {
                                "name": "default_api_skill_view",
                                "arguments": '{"path":"/x"}',
                            },
                            "extra_content": {
                                "google": {"thought_signature": "SIG123"}
                            },
                        }
                    ]
                }
            }
        ]
    }
    client._capture_thought_signatures(data)

    # Replay arrives with the ORIGINAL colon-qualified name — lookup must
    # try the rewritten variant too.
    sig = client._lookup_signature("", "default_api:skill_view", '{"path":"/x"}')
    assert sig == "SIG123"

    # The rewritten form resolves directly (in-memory fallback for direct replay).
    sig2 = client._lookup_signature("", "default_api_skill_view", '{"path":"/x"}')
    assert sig2 == "SIG123"

    # And the id-based lookup still works.
    sig3 = client._lookup_signature("tc1", "", "")
    assert sig3 == "SIG123"
    assert sig3 == "SIG123"

    # Persisted store carries the rewritten form (what the provider echoed).
    db = sqlite3.connect(tmp_path / "sig.db")
    rows = db.execute(
        "SELECT fn_name FROM thought_signatures WHERE arguments=?",
        ('{"path":"/x"}',),
    ).fetchall()
    names = {r[0] for r in rows}
    assert names == {"default_api_skill_view"}
    db.close()


def test_restore_attaches_signature_to_colon_named_tool_call(tmp_path):
    """_restore_thought_signatures must attach extra_content when the replay
    uses the original colon-qualified name."""
    client = _make_client(tmp_path)
    client._thought_signatures_by_fn[
        ("default_api:skill_view", '{"path":"/x"}')
    ] = "SIG456"

    messages = [
        {
            "role": "user",
            "content": "go",
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "tc-replay",
                    "type": "function",
                    "function": {
                        "name": "default_api:skill_view",
                        "arguments": '{"path":"/x"}',
                    },
                }
            ],
        },
    ]
    client._restore_thought_signatures(messages)
    tc = messages[1]["tool_calls"][0]
    assert tc["extra_content"]["google"]["thought_signature"] == "SIG456"


def test_streaming_delta_reassembly_keys_signature_under_full_arguments(tmp_path):
    """Gemini streams a tool_call as several deltas (first carries id +
    signature with empty arguments, later chunks append the arguments in
    fragments). Per-chunk capture would key the signature under a partial
    arguments string; _reassemble_and_capture must concatenate the fragments
    so the (fn, arguments) lookup succeeds on replay."""
    client = _make_client(tmp_path)

    chunks = [
        {
            "choices": [
                {
                    "delta": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "tc9",
                                "type": "function",
                                "function": {"name": "kanban_show", "arguments": ""},
                                "extra_content": {
                                    "google": {"thought_signature": "SIG789"}
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": '{"action":'}}
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": '"list"}'}}
                        ]
                    }
                }
            ]
        },
    ]
    client._reassemble_and_capture(chunks)

    sig = client._lookup_signature("", "kanban_show", '{"action":"list"}')
    assert sig == "SIG789"

    # The id-based lookup also works.
    assert client._lookup_signature("tc9", "", "") == "SIG789"

    # Persisted with the FULL arguments.
    db = sqlite3.connect(tmp_path / "sig.db")
    rows = db.execute(
        "SELECT fn_name, arguments FROM thought_signatures WHERE id='tc9'"
    ).fetchall()
    assert rows and rows[0][1] == '{"action":"list"}'


def test_lookup_json_normalized_arguments(tmp_path):
    """Replay arguments may be reserialized with different spacing/key order
    ({"board": "default"} vs {"board":"default"}) — the lookup must match on
    parsed JSON, not raw string, or the Gemini replay 400 comes back."""
    client = _make_client(tmp_path)
    data = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "id": "tcA",
                            "type": "function",
                            "function": {
                                "name": "default_api_kanban_show",
                                "arguments": '{"board":"default"}',
                            },
                            "extra_content": {
                                "google": {"thought_signature": "SIGJSON"}
                            },
                        }
                    ]
                }
            }
        ]
    }
    client._capture_thought_signatures(data)

    # Exact string match still works.
    assert client._lookup_signature("", "default_api:kanban_show", '{"board":"default"}') == "SIGJSON"
    # Space-padded reserialization must match via JSON normalization.
    assert client._lookup_signature("", "default_api:kanban_show", '{"board": "default"}') == "SIGJSON"
    # Reordered keys must match too.
    assert client._lookup_signature("", "default_api:kanban_show", '{"board":"default","extra":"x"}') is None  # different payload
    data2 = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "id": "tcB",
                            "type": "function",
                            "function": {
                                "name": "default_api_kanban_show",
                                "arguments": '{"a":1,"b":2}',
                            },
                            "extra_content": {
                                "google": {"thought_signature": "SIGREORDER"}
                            },
                        }
                    ]
                }
            }
        ]
    }
    client._capture_thought_signatures(data2)
    assert client._lookup_signature("", "default_api_kanban_show", '{"b":2,"a":1}') == "SIGREORDER"
