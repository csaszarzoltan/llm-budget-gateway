"""Cached input-token telemetry: how much of the prompt the provider served from cache."""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from llm_budget_gateway.budget_enforcement import BudgetScope
from llm_budget_gateway.console_api import create_console_app
from llm_budget_gateway.cost_tracking import (
    CostCalculator,
    CostStore,
    CostTracker,
    PriceMap,
    TokenUsage,
    accumulate_usage,
    extract_cached_tokens,
)
from llm_budget_gateway.gateway_proxy import GatewayProxy
from llm_budget_gateway.provider_direct import _responses_to_chat
from llm_budget_gateway.service_manager import ServiceManager


@pytest.mark.parametrize("usage, expected", [
    ({"prompt_tokens_details": {"cached_tokens": 70}}, 70),                 # OpenAI chat
    ({"input_tokens_details": {"cached_tokens": 55}}, 55),                  # OpenAI responses / codex
    ({"cache_read_input_tokens": 40}, 40),                                   # Anthropic
    ({"prompt_cache_hit_tokens": 30}, 30),                                   # DeepSeek
    ({"cached_tokens": 20}, 20),                                             # flat
    ({"prompt_tokens_details": {"cached_tokens": None}}, 0),
    ({"prompt_tokens": 100}, 0),
    (None, 0),
])
def test_extract_cached_tokens_reads_every_provider_shape(usage, expected):
    assert extract_cached_tokens(usage) == expected


def test_extract_cached_tokens_reads_objects_like_litellm_usage():
    usage = SimpleNamespace(prompt_tokens_details=SimpleNamespace(cached_tokens=64))

    assert extract_cached_tokens(usage) == 64


def test_accumulate_usage_sums_cached_tokens():
    parts = [{"prompt_tokens": 10, "cached_tokens": 4}, {"prompt_tokens": 5, "cached_tokens": 1}]

    assert accumulate_usage(parts).cached_tokens == 5


def test_stream_usage_collection_keeps_cached_tokens():
    chunk = {"usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105,
                       "prompt_tokens_details": {"cached_tokens": 80}}}

    usage = GatewayProxy._collect_stream_usage([chunk])

    assert usage is not None and usage.cached_tokens == 80


def test_codex_responses_mapping_keeps_cached_tokens():
    data = {"id": "r", "status": "completed", "output": [],
            "usage": {"input_tokens": 200, "output_tokens": 10,
                      "input_tokens_details": {"cached_tokens": 150}}}

    chat = _responses_to_chat(data, "m")

    assert extract_cached_tokens(chat["usage"]) == 150


def _store(tmp_path):
    store = CostStore(connection=sqlite3.connect(tmp_path / "cost.db", check_same_thread=False))
    tracker = CostTracker(store=store, calculator=CostCalculator(PriceMap()))
    return store, tracker


def _insert(store, tracker, rid, prompt, cached):
    store.insert(tracker.build_record(
        request_id=rid, scope=BudgetScope(kind="key", key="k"), model="m", provider="p",
        usage=TokenUsage(prompt_tokens=prompt, completion_tokens=1, total_tokens=prompt + 1,
                         cached_tokens=cached),
        latency_ms=1, status="success"))


def test_cache_summary_reports_share_of_prompt_served_from_cache(tmp_path):
    store, tracker = _store(tmp_path)
    _insert(store, tracker, "a", 1000, 900)
    _insert(store, tracker, "b", 1000, 100)

    summary = store.cache_summary(days=1)

    assert summary["prompt_tokens"] == 2000
    assert summary["cached_tokens"] == 1000
    assert summary["cached_share"] == 0.5


def test_cache_summary_is_none_share_without_prompt_tokens(tmp_path):
    store, _ = _store(tmp_path)

    assert store.cache_summary(days=1)["cached_share"] is None


@pytest.mark.asyncio
async def test_usage_endpoint_exposes_cache_summary(tmp_path):
    store, tracker = _store(tmp_path)
    _insert(store, tracker, "a", 400, 300)

    def conn(n):
        return sqlite3.connect(tmp_path / n, check_same_thread=False)

    app = create_console_app(
        manager=ServiceManager(workdir=tmp_path), project_root=tmp_path,
        product_connection=conn("product.db"), provider_connection=conn("providers.db"),
        routing_connection=conn("routing.db"), cost_connection=conn("cost.db"),
        credential_key_path=tmp_path / "k.key", alerts_db_path=tmp_path / "a.db")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://c") as client:
        body = (await client.get("/v1/product/usage")).json()

    assert body["cache"]["cached_tokens"] == 300
    assert body["cache"]["cached_share"] == 0.75
