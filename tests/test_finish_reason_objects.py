"""finish_reason must be read from object-shaped stream chunks, not only dicts.

The direct transport appends the RAW upstream chunks, which are litellm /
pydantic objects. A dict-only reader left finish_reason NULL on every such
stream (22,950 of 70,982 recorded successes), so a provider-side cut
(`length`) could not be told from a natural stop.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from llm_budget_gateway.gateway_proxy import GatewayProxy

extract = GatewayProxy._extract_finish_reason


class _Choice(BaseModel):
    index: int = 0
    finish_reason: str | None = None


class _Chunk(BaseModel):
    choices: list[_Choice]


def test_pydantic_chunks_report_the_terminal_finish_reason():
    chunks = [_Chunk(choices=[_Choice()]), _Chunk(choices=[_Choice(finish_reason="length")])]

    assert extract(chunks) == "length"


def test_namespace_chunks_report_the_terminal_finish_reason():
    chunks = [
        SimpleNamespace(choices=[SimpleNamespace(finish_reason=None)]),
        SimpleNamespace(choices=[SimpleNamespace(finish_reason="tool_calls")]),
    ]

    assert extract(chunks) == "tool_calls"


def test_mixed_dict_and_object_chunks():
    chunks = [
        {"choices": [{"finish_reason": "stop"}]},
        SimpleNamespace(choices=[SimpleNamespace(finish_reason=None)], usage=None),
    ]

    assert extract(chunks) == "stop"


def test_object_chunk_with_responses_status_incomplete_is_length():
    assert extract([SimpleNamespace(status="incomplete")]) == "length"


@pytest.mark.parametrize("chunks", [
    [], [SimpleNamespace(choices=[SimpleNamespace(finish_reason=None)])],
    [_Chunk(choices=[_Choice()])], [SimpleNamespace(choices=None)],
])
def test_no_finish_reason_returns_none(chunks):
    assert extract(chunks) is None


def test_dict_chunks_still_work():
    assert extract([{"choices": [{"finish_reason": "stop"}]}]) == "stop"
