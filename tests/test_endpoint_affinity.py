"""Learned endpoint affinity: stop paying for the dead step on every request.

The provider-wide `api_mode=codex_responses` sends every opencode-go request
to `/responses`, but only the deepseek family serves it there. The glm family
and space-bunny-free answer 400 `ModelProtocolUnsupported`, and
`_responses_endpoint_unavailable` then retries `/chat/completions` inside
the same attempt — correct, and it works, but nothing is remembered.

Measured cost of the un-remembered retry, 8 calls to the dead endpoint
(`scripts/probe_responses_support.py` sweeps 14 models):

    avg 0.477s   median 0.461s   max 0.617s

so every chat-only model pays ~0.5s per request for a call that can never
succeed. Against a 1.3s answer that is ~27% of the request.

The fix is the same shape as sticky sessions (`_sticky_sessions` +
`_STICKY_TTL_SECONDS`): remember the endpoint that worked, in memory, with a
TTL so a provider that changes what it serves gets re-learned rather than
pinned forever. Not persisted — the decision is cheap to re-derive and a
stale persisted row would be worse than a 0.5s retry.

In memory only, so it must be bounded: a gateway that learns thousands of
model/provider pairs must not grow without limit.

Each test below fails against the current code.
"""
from __future__ import annotations

import sys
import time
import types

import pytest

SRC = "/home/zoltan/llm-budget-gateway/src"
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from llm_budget_gateway.provider_direct import (  # noqa: E402
    DirectProviderClient,
    ProviderEndpoint,
    UpstreamProviderError,
)

PROTOCOL_400 = (
    '{"type":"error","error":{"type":"ModelProtocolUnsupported",'
    '"message":"Model does not support this protocol."}}'
)


def _endpoint() -> ProviderEndpoint:
    return ProviderEndpoint(
        name="opencode-go",
        base_url="https://example.invalid/v1",
        api_key_env="__vault_opencode-go__",
        api_key_value="k",
        models=("m",),
        api_mode="codex_responses",
        extra_headers={"x-opencode-session": "hermes-gateway"},
    )


def _client() -> DirectProviderClient:
    """A real __init__ (so all internal state exists) + a scripted transport.

    Mocking the state field by field is how this test went wrong twice: the
    chat path touches `_thought_signatures`, `_thought_signatures_by_fn` and
    `_sig_db`, none of which a hand-built object has. Only the two network
    seams are replaced.
    """
    cl = DirectProviderClient(
        registry={"opencode-go": {
            "base_url": "https://example.invalid/v1",
            "api_key_env": "__vault_opencode-go__",
            "api_key": "k",
            "models": ["m"],
            "api_mode": "codex_responses",
            "extra_headers": {"x-opencode-session": "hermes-gateway"},
        }},
        timeout=30.0,
        idle_timeout=30.0,
    )
    cl.resolve = lambda model: _endpoint()
    cl._request_url = lambda ep, kind: "https://example.invalid/v1/chat/completions"
    # NOTE: _with_auth_query is deliberately NOT stubbed. Overriding it as an
    # instance attribute hid a real regression: a lost @staticmethod decorator
    # made it a bound method, and a mock that replaced it locally kept the
    # tests green while every /responses call raised TypeError in production.
    cl._client = types.SimpleNamespace()
    return cl


def _ok_payload() -> dict:
    return {"model": "m", "choices": [{"message": {"content": "ok"}}]}


def _responses_refuses() -> UpstreamProviderError:
    return UpstreamProviderError(
        400, "upstream provider error: opencode-go (HTTP 400)", body=PROTOCOL_400
    )


def _body() -> dict:
    return {"max_tokens": 32, "messages": [{"role": "user", "content": "hi"}]}


# --- the learned decision -----------------------------------------------


@pytest.mark.asyncio
async def test_the_second_request_skips_the_dead_endpoint() -> None:
    """After one learned refusal, /chat/completions is used first.

    This is the whole point: the dead call costs ~0.48s and can never
    succeed, so paying it again on every request is waste.
    """
    cl = _client()
    attempts: list[str] = []

    async def _fr(endpoint, model, body, target_seconds=None):
        attempts.append("responses")
        raise _responses_refuses()

    async def _post(url, **kwargs):
        attempts.append("chat")
        return types.SimpleNamespace(
            status_code=200, json=lambda: _ok_payload(), text="", headers={}
        )

    cl._forward_responses = _fr
    cl._client.post = _post

    for _ in range(3):
        status, _, _ = await cl.forward("@opencode-go/m", _body(), kind="chat")
        assert status == 200

    assert attempts == ["responses", "chat", "chat", "chat"], (
        f"request 1 learns the endpoint, later ones must not retry the dead "
        f"path first; got {attempts}"
    )


@pytest.mark.asyncio
async def test_a_healthy_responses_model_is_never_pinned_to_chat() -> None:
    """A model that serves /responses must keep using it.

    The affinity records a refusal, not a blanket "prefer chat" — the deepseek
    family works on /responses and must not be slowed down.
    """
    cl = _client()
    attempts: list[str] = []

    async def _fr(endpoint, model, body, target_seconds=None):
        attempts.append("responses")
        return 200, _ok_payload(), "m"

    cl._forward_responses = _fr
    cl._client.post = None  # any chat call is a failure of this test

    for _ in range(3):
        status, _, _ = await cl.forward("@opencode-go/m", _body(), kind="chat")
        assert status == 200

    assert attempts == ["responses"] * 3, (
        f"a healthy /responses model must stay on /responses, got {attempts}"
    )


@pytest.mark.asyncio
async def test_a_genuine_client_error_does_not_teach_the_wrong_lesson() -> None:
    """Only an endpoint refusal is a signal about the endpoint.

    A 400 that is a real client error (unknown parameter) must not pin the
    model to /chat/completions for an hour.
    """
    cl = _client()

    async def _fr(endpoint, model, body, target_seconds=None):
        raise UpstreamProviderError(
            400, "upstream provider error: opencode-go (HTTP 400)",
            body='{"error":{"message":"Unknown parameter: max_tokens"}}',
        )

    cl._forward_responses = _fr

    with pytest.raises(UpstreamProviderError):
        await cl.forward("@opencode-go/m", _body(), kind="chat")

    assert cl._known_endpoint("@opencode-go/m") is None, (
        "a real client error says nothing about which endpoint to use"
    )


# --- the TTL, so a changed provider is relearned -------------------------


def test_the_affinity_expires_so_a_changed_provider_is_relearned() -> None:
    """A stale decision must not pin a model to the slower path forever.

    Providers change what they serve: deepseek moved onto /responses while
    glm stayed on /chat. A learned row outliving that would keep a model on
    the slower endpoint indefinitely.
    """
    from llm_budget_gateway.provider_direct import _ENDPOINT_AFFINITY_TTL_SECONDS

    assert 0 < _ENDPOINT_AFFINITY_TTL_SECONDS <= 86400, (
        "the learned endpoint must expire, and not on a multi-day horizon"
    )


def test_a_stale_entry_is_dropped_on_read() -> None:
    """Reading an expired entry returns nothing and cleans up."""
    from llm_budget_gateway.provider_direct import _ENDPOINT_AFFINITY_TTL_SECONDS

    cl = DirectProviderClient.__new__(DirectProviderClient)
    cl._endpoint_affinity = {
        "@opencode-go/old": (
            "chat_completions",
            time.monotonic() - _ENDPOINT_AFFINITY_TTL_SECONDS - 1,
        )
    }
    assert cl._known_endpoint("@opencode-go/old") is None
    assert "@opencode-go/old" not in cl._endpoint_affinity


# --- the staticmethod that a lost decorator turned into a live bug ------


def test_with_auth_query_is_still_a_staticmethod() -> None:
    """A lost @staticmethod here broke every /responses call in production.

    It is called as `self._with_auth_query(url, endpoint)` from three places.
    Without the decorator Python binds it, so each call raised
    `TypeError: takes 2 positional arguments but 3 were given` — and a test
    that stubbed it as an instance attribute kept passing while the gateway
    was dead. Assert the binding, not the behaviour.
    """
    import inspect

    from llm_budget_gateway.provider_direct import DirectProviderClient as C

    assert isinstance(inspect.getattr_static(C, "_with_auth_query"), staticmethod), (
        "_with_auth_query must stay a staticmethod: it is called as "
        "self._with_auth_query(url, endpoint) and binding it adds a third arg"
    )
    # the affinity helpers are the opposite case: they need `self`
    for name in ("_remember_endpoint", "_known_endpoint"):
        assert not isinstance(inspect.getattr_static(C, name), staticmethod), (
            f"{name} uses self and must NOT be a staticmethod"
        )


# --- bounded, because a gateway runs for months -------------------------


def test_the_affinity_map_is_bounded() -> None:
    """A long-lived gateway must not accumulate one entry per model forever."""
    from llm_budget_gateway.provider_direct import _ENDPOINT_AFFINITY_MAX

    assert _ENDPOINT_AFFINITY_MAX > 0
    cl = DirectProviderClient.__new__(DirectProviderClient)
    cl._endpoint_affinity = {}
    for i in range(_ENDPOINT_AFFINITY_MAX + 50):
        cl._remember_endpoint(f"@opencode-go/model-{i}", "chat_completions")
    assert len(cl._endpoint_affinity) <= _ENDPOINT_AFFINITY_MAX, (
        f"affinity map grew to {len(cl._endpoint_affinity)}"
    )
