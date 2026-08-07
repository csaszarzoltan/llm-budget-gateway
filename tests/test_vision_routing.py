"""Vision-aware route resolution: image-bearing requests must only be
served by vision-capable models, inferred from the model slug (zero-config),
so a 400 ``unknown variant image_url`` can never burn the fallback chain."""

import asyncio

import pytest

from llm_budget_gateway.config import Settings
from llm_budget_gateway.cost_tracking import CostStore
from llm_budget_gateway.gateway_proxy import (
    GatewayProxy,
    _body_has_images,
    _model_supports_vision,
)

IMG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def _route(*models: str) -> dict:
    return {
        "targets": [
            {"model": m, "priority": i * 10, "timezone": "UTC"}
            for i, m in enumerate(models)
        ]
    }


def test_model_supports_vision_markers():
    assert _model_supports_vision("@google/gemini-3.6-flash")
    assert _model_supports_vision("gpt-4o")
    assert _model_supports_vision("@openrouter/openai/gpt-4o-mini")
    assert _model_supports_vision("claude-3-5-sonnet")
    assert _model_supports_vision("qwen2.5-vl-72b")


def test_model_supports_vision_curated():
    # xiaomi mimo-v2.5 reports image input despite the plain slug.
    assert _model_supports_vision("@xiaomi/mimo-v2.5")
    # opencode-zen mimo-v2.5-free is a DIFFERENT model — text-only.
    assert not _model_supports_vision("@opencode-zen/mimo-v2.5-free")


def test_model_supports_vision_text_only():
    for m in (
        "@opencode-go/deepseek-v4-flash",
        "@openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
        "@deepinfra/deepseek-ai/DeepSeek-V4-Flash-0731",
        None,
        "",
    ):
        assert not _model_supports_vision(m)


def test_body_has_images():
    assert _body_has_images(
        {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": IMG}}]}]}
    )
    assert not _body_has_images(
        {"messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]}
    )
    assert not _body_has_images({"messages": [{"role": "user", "content": "plain"}]})
    assert not _body_has_images(None)
    assert not _body_has_images({})


@pytest.mark.asyncio
async def test_vision_gate_restricts_image_requests():
    store = CostStore(db_path=":memory:")
    proxy = GatewayProxy(
        settings=Settings(),
        cost_tracker=store,
        budget_enforcer=None,
        fallback_manager=None,
    )
    route = _route(
        "@google/gemini-3.6-flash",
        "@xiaomi/mimo-v2.5",
        "@opencode-go/deepseek-v4-flash",
        "@openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
    )
    image_body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "what is this?"},
                    {"type": "image_url", "image_url": {"url": IMG}},
                ],
            }
        ]
    }
    decision = proxy._resolve_targets(route, [], body=image_body)
    assert decision is not None
    assert decision["candidates"] == [
        "@google/gemini-3.6-flash",
        "@xiaomi/mimo-v2.5",
    ]


@pytest.mark.asyncio
async def test_vision_gate_allows_all_for_text():
    store = CostStore(db_path=":memory:")
    proxy = GatewayProxy(
        settings=Settings(),
        cost_tracker=store,
        budget_enforcer=None,
        fallback_manager=None,
    )
    route = _route(
        "@google/gemini-3.6-flash",
        "@xiaomi/mimo-v2.5",
        "@opencode-go/deepseek-v4-flash",
    )
    decision = proxy._resolve_targets(
        route, [], body={"messages": [{"role": "user", "content": "hello"}]}
    )
    assert decision is not None
    # All three are eligible for text; order follows priority (route order).
    assert decision["candidates"] == [
        "@google/gemini-3.6-flash",
        "@xiaomi/mimo-v2.5",
        "@opencode-go/deepseek-v4-flash",
    ]
