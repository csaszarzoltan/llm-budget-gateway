"""The five gateway_proxy.py routing defects the Claude Code review found.

1. the curated vision entry was "@xiaomi/mimo-v2.5" tested against the RAW
   `model`, so the curated branch could never fire (a vision model whose slug
   carries no marker marker went to a text-only provider -> 400 on images),
2. a string metadata.capabilities was iterated char-by-char -> bogus
   capabilities ['t','o','l','s'] -> "no eligible target" (422),
3. a malformed metadata.max_cost_usd was reported as "404 unknown route"
   for a route that does exist (ValueError shared the resolve_alias try),
4. decision["selected_model"] was dereferenced OUTSIDE the try -> an
   unhandled KeyError escaped as a 502 + traceback,
5. a decision without selected_model.
"""
from __future__ import annotations

from llm_budget_gateway import gateway_proxy as gp


def test_curated_vision_entry_is_reachable() -> None:
    """The curated list must actually match its model."""
    assert gp._model_supports_vision("xiaomi/mimo-v2.5") is True
    assert gp._model_supports_vision("XIAOMI/MIMO-V2.5") is True
    # the old "@" prefix made every comparison fail
    assert gp._model_supports_vision("@xiaomi/mimo-v2.5") is True
    # and a genuinely text-only model must still be excluded. Note gpt-4o-mini
    # IS vision capable (it matches the "4o" marker) — that is correct.
    assert gp._model_supports_vision("deepseek/deepseek-chat") is False
    assert gp._model_supports_vision("glm-4.6") is False


def test_capabilities_accepts_a_bare_string() -> None:
    proxy = gp.GatewayProxy.__new__(gp.GatewayProxy)
    caps = proxy._capabilities_from({}, {"capabilities": "tools"})
    assert caps == ["tools"], caps
    caps2 = proxy._capabilities_from({}, {"capabilities": ["tools", "vision"]})
    assert caps2 == ["tools", "vision"]
    # no garbage characters
    assert not any(len(c) == 1 for c in caps)


def test_capabilities_from_body_still_works() -> None:
    proxy = gp.GatewayProxy.__new__(gp.GatewayProxy)
    body = {"tools": [{"type": "function"}], "response_format": {"type": "json"}}
    caps = proxy._capabilities_from(body, {})
    assert "tools" in caps
    assert "structured_output" in caps


def test_max_cost_usd_parse_is_not_a_404() -> None:
    """float('abc') must be a 400-class client error, not 'unknown route'.

    Asserted on the shape of the fix: the parse is separated from the
    resolve_alias try, so a ValueError cannot reach the 404 mapping.
    """
    import inspect

    src = inspect.getsource(gp.GatewayProxy._resolve_route_plan)
    parse_idx = src.index("max_cost_usd = float(")
    alias_idx = src.index("plane.resolve_alias(")
    assert parse_idx < alias_idx, "max_cost_usd must be parsed before resolve_alias"
    # the parse must have its own except returning a 400
    tail = src[parse_idx:alias_idx]
    assert '"status": 400' in tail, tail
    assert "resolve_alias" not in tail


def test_selected_model_dereference_is_guarded() -> None:
    import inspect

    src = inspect.getsource(gp.GatewayProxy._resolve_route_plan)
    assert 'decision["selected_model"]' not in src
    assert 'decision.get("selected_model")' in src
