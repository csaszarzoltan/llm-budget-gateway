"""TDD: Codex Responses API transport for provider connections.

muse-spark on opencode.ai/zen/go serves /responses (Codex protocol) but its
/chat/completions shim returns HTTP 500 (2026-08-23 outage). Hermes talks to
it via api_mode=codex_responses; the gateway direct transport only spoke
chat_completions. Add per-connection ``api_mode`` so a provider connection
can route chat traffic through POST /responses with request/response
translation.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from llm_budget_gateway.provider_direct import (
    DirectProviderClient,
    ProviderEndpoint,
    UpstreamProviderError,
)
from collections.abc import AsyncIterator
from unittest.mock import Mock

from llm_budget_gateway.config import Settings
from llm_budget_gateway.gateway_proxy import (
    GatewayProxy,
    MidStreamFailure,
    ProviderResponse,
)


def _endpoint(api_mode: str = "chat_completions") -> ProviderEndpoint:
    return ProviderEndpoint(
        name="opencode-go",
        base_url="https://zen.example/v1",
        api_key_env="",
        models=("muse-spark-1.2-contributor",),
        api_key_value="test-key",
        user_agent="opencode/1.14.41",
        extra_body=None,
    )


def _client_with(monkeypatch, respond) -> tuple[DirectProviderClient, list[httpx.Request]]:
    """Build a client whose HTTP posts are captured; ``respond`` builds the JSON."""
    calls: list[dict] = []

    async def _post(url, json=None, headers=None, timeout=None):  # noqa: A002
        # `timeout` is a structured httpx.Timeout, forwarded so a per-target
        # timeout bounds the handshake without capping the whole request.
        calls.append({"url": url, "json": json, "headers": headers})
        return httpx.Response(200, json=respond(json), request=httpx.Request("POST", url))


    client = DirectProviderClient(registry={})
    ep = _endpoint()
    client._registry["opencode-go"] = ep
    client._model_index["muse-spark-1.2-contributor"] = ep
    client._model_index["@opencode-go/muse-spark-1.2-contributor"] = ep
    # Inject the stub transport (bypasses real network).
    object.__setattr__(client, "_client", type("C", (), {"post": staticmethod(_post), "aclose": staticmethod(lambda: None)})())
    return client, calls


def _responses_payload(request_json: dict[str, Any]) -> dict[str, Any]:
    """Mirror the real opencode zen /responses shape (verified live 2026-08-24)."""
    return {
        "id": "resp_123",
        "object": "response",
        "status": "completed",
        "model": request_json.get("model"),
        "output": [
            {
                "type": "reasoning",
                "id": "rs_1",
                "summary": [],
            },
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": "pong", "annotations": []}
                ],
            },
        ],
        "usage": {
            "input_tokens": 12,
            "output_tokens": 3,
            "total_tokens": 15,
        },
    }


@pytest.mark.asyncio
async def test_api_mode_responses_posts_to_responses_endpoint(monkeypatch):
    """api_mode=codex_responses → POST {base}/responses with translated body."""
    client, calls = _client_with(
        monkeypatch, lambda payload: _responses_payload(payload)
    )
    ep = client.resolve("muse-spark-1.2-contributor")
    object.__setattr__(ep, "api_mode", "codex_responses")

    status, data, served = await client.forward(
        "muse-spark-1.2-contributor",
        {
            "messages": [{"role": "user", "content": "Say exactly: pong"}],
            "max_tokens": 30,
        },
    )

    assert status == 200
    assert len(calls) == 1
    assert calls[0]["url"].endswith("/responses")
    sent = calls[0]["json"]
    assert sent["model"] == "muse-spark-1.2-contributor"
    assert "input" in sent and "messages" not in sent
    assert sent["max_output_tokens"] == 16384  # muse min enforced (reasoning needs budget; 30 would truncate to 0)
    # Response translated back to chat-completions shape for callers.
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert data["choices"][0]["message"]["content"] == "pong"
    assert data["usage"]["prompt_tokens"] == 12
    assert data["usage"]["completion_tokens"] == 3
    assert data["usage"]["total_tokens"] == 15
    assert served == "muse-spark-1.2-contributor"


@pytest.mark.asyncio
async def test_api_mode_default_stays_chat_completions(monkeypatch):
    """Without api_mode the URL must remain /chat/completions (no regression)."""
    client, calls = _client_with(
        monkeypatch,
        lambda p: {
            "id": "c1",
            "model": p["model"],
            "choices": [{"message": {"role": "assistant", "content": "pong"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    )
    status, data, _served = await client.forward(
        "muse-spark-1.2-contributor",
        {"messages": [{"role": "user", "content": "hi"}]},
    )
    assert status == 200
    assert calls[0]["url"].endswith("/chat/completions")
    assert "messages" in calls[0]["json"]


@pytest.mark.asyncio
async def test_stream_chunks_uses_responses_and_translates(monkeypatch):
    """api_mode=codex_responses + stream → /responses SSE translated to chat chunks."""
    lines = [
        'data: {"type":"response.output_text.delta","item_id":"it1","delta":"po"}',
        'data: {"type":"response.output_text.delta","item_id":"it1","delta":"ng"}',
        'data: {"type":"response.completed","response":{"id":"resp_9","model":"muse-spark-1.2-contributor","status":"completed","usage":{"input_tokens":10,"output_tokens":5,"total_tokens":15}}}',
        "data: [DONE]",
    ]

    class _StreamCtx:
        async def __aenter__(self):
            class R:
                status_code = 200

                async def aread(self):
                    return b""

                async def aiter_lines(self):
                    for ln in lines:
                        yield ln

            return R()

        async def __aexit__(self, *exc):
            return False

    captured = {}

    def _stream(method, url, json=None, headers=None):  # noqa: A002
        captured["url"] = url
        captured["json"] = json
        return _StreamCtx()

    client = DirectProviderClient(registry={})
    ep = _endpoint()
    object.__setattr__(ep, "api_mode", "codex_responses")
    client._registry["opencode-go"] = ep
    client._model_index["muse-spark-1.2-contributor"] = ep
    object.__setattr__(
        client, "_client", type("C", (), {"stream": staticmethod(_stream)})()
    )

    chunks = []
    async for chunk in client.stream_chunks(
        "muse-spark-1.2-contributor",
        {
            "messages": [{"role": "user", "content": "Say exactly: pong"}],
            "max_tokens": 100,
            "stream": True,
        },
    ):
        chunks.append(chunk)

    assert captured["url"].endswith("/responses")
    assert captured["json"]["stream"] is True
    assert captured["json"]["input"][0]["content"][0]["text"] == "Say exactly: pong"
    texts = [
        c["choices"][0]["delta"].get("content", "")
        for c in chunks
        if c["choices"][0]["delta"].get("content")
    ]
    assert "".join(texts) == "pong"
    last = chunks[-1]
    assert last["choices"][0]["finish_reason"] == "stop"
    assert last["usage"]["total_tokens"] == 15


@pytest.mark.asyncio
async def test_stream_chunks_handles_incomplete():
    """response.incomplete must yield length finish, not hang truncated."""
    lines = [
        'data: {"type":"response.output_text.delta","item_id":"it1","delta":"hello "}',
        'data: {"type":"response.output_text.delta","item_id":"it1","delta":"world"}',
        'data: {"type":"response.incomplete","response":{"id":"resp_inc","model":"muse-spark-1.2-contributor","status":"incomplete","incomplete_details":{"reason":"max_output_tokens"},"usage":{"input_tokens":10,"output_tokens":5,"total_tokens":15}}}',
        "data: [DONE]",
    ]
    class _Ctx:
        async def __aenter__(self):
            class R:
                status_code=200
                async def aread(self): return b""
                async def aiter_lines(self):
                    for l in lines: yield l
            return R()
        async def __aexit__(self,*a): return False
    def _stream(m,u,json=None,headers=None):
        return _Ctx()
    from llm_budget_gateway.provider_direct import DirectProviderClient
    client=DirectProviderClient(registry={})
    from tests.test_responses_transport import _endpoint
    ep=_endpoint()
    object.__setattr__(ep,"api_mode","codex_responses")
    client._registry["opencode-go"]=ep
    client._model_index["muse-spark-1.2-contributor"]=ep
    object.__setattr__(client,"_client", type("C",(),{"stream":staticmethod(_stream)})())
    chunks=[]
    async for c in client.stream_chunks("muse-spark-1.2-contributor", {"messages":[{"role":"user","content":"hi"}],"max_tokens":10}):
        chunks.append(c)
    assert len(chunks)==3  # 2 deltas + 1 final
    assert chunks[-1]["choices"][0]["finish_reason"]=="length"
    assert chunks[-1]["usage"]["total_tokens"]==15


@pytest.mark.asyncio
async def test_stream_chunks_default_stays_chat_completions():
    """Without api_mode the streaming URL must remain /chat/completions."""
    captured = {}

    class _StreamCtx:
        async def __aenter__(self):
            class R:
                status_code = 200

                async def aread(self):
                    return b""

                async def aiter_lines(self):
                    yield 'data: {"id":"c1","choices":[{"index":0,"delta":{"content":"hi"}}]}'
                    yield "data: [DONE]"

            return R()

        async def __aexit__(self, *exc):
            return False

    def _stream(method, url, json=None, headers=None):  # noqa: A002
        captured["url"] = url
        return _StreamCtx()

    client = DirectProviderClient(registry={})
    ep = _endpoint()  # default chat_completions
    client._registry["opencode-go"] = ep
    client._model_index["muse-spark-1.2-contributor"] = ep
    object.__setattr__(
        client, "_client", type("C", (), {"stream": staticmethod(_stream)})()
    )
    chunks = []
    async for chunk in client.stream_chunks(
        "muse-spark-1.2-contributor", {"messages": [{"role": "user", "content": "x"}]}
    ):
        chunks.append(chunk)
    assert captured["url"].endswith("/chat/completions")



@pytest.mark.asyncio
async def test_min_output_tokens_pattern_clamps_reasoning(monkeypatch):
    """Pattern fallback: muse with small max → clamped to 4096."""
    client, calls = _client_with(
        monkeypatch, lambda payload: _responses_payload(payload)
    )
    ep = client.resolve("muse-spark-1.2-contributor")
    object.__setattr__(ep, "api_mode", "codex_responses")
    # No explicit min set → pattern should clamp
    object.__setattr__(ep, "min_output_tokens", None)
    await client.forward("muse-spark-1.2-contributor", {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 200})
    assert calls[0]["json"]["max_output_tokens"] == 16384


@pytest.mark.asyncio
async def test_min_output_tokens_provider_overrides_pattern(monkeypatch):
    """Provider explicit min wins over pattern."""
    client, calls = _client_with(
        monkeypatch, lambda payload: _responses_payload(payload)
    )
    ep = client.resolve("muse-spark-1.2-contributor")
    object.__setattr__(ep, "api_mode", "codex_responses")
    object.__setattr__(ep, "min_output_tokens", 8000)
    await client.forward("muse-spark-1.2-contributor", {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 100})
    assert calls[0]["json"]["max_output_tokens"] == 8000


@pytest.mark.asyncio
async def test_min_output_tokens_non_reasoning_not_clamped(monkeypatch):
    """Non-reasoning model with small max gets universal 4096 floor (prevents stealth truncation)."""
    client, calls = _client_with(
        monkeypatch, lambda payload: _responses_payload(payload)
    )
    ep = _endpoint()
    ep2 = type(ep)(name="openai", base_url="https://api.example/v1", api_key_env="", models=("gpt-4o-mini",), api_key_value="k")
    object.__setattr__(ep2, "api_mode", "codex_responses")
    client._registry["openai"] = ep2
    client._model_index["gpt-4o-mini"] = ep2
    client._model_index["@openai/gpt-4o-mini"] = ep2
    await client.forward("gpt-4o-mini", {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 200})
    assert calls[-1]["json"]["max_output_tokens"] == 4096


@pytest.mark.asyncio
async def test_min_output_tokens_zero_disables_clamp(monkeypatch):
    """Explicit 0 disables clamping even for reasoning model."""
    client, calls = _client_with(
        monkeypatch, lambda payload: _responses_payload(payload)
    )
    ep = client.resolve("muse-spark-1.2-contributor")
    object.__setattr__(ep, "api_mode", "codex_responses")
    object.__setattr__(ep, "min_output_tokens", 0)
    await client.forward("muse-spark-1.2-contributor", {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 200})
    assert calls[0]["json"]["max_output_tokens"] == 200


@pytest.mark.asyncio
async def test_responses_input_assistant_uses_output_text():
    """Multi-turn: assistant history items need output_text, users input_text."""
    client = DirectProviderClient(registry={})
    instructions, items = client._responses_input_from_messages({
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello! How can I help?"},
            {"role": "user", "content": "Say: pong"},
        ]
    })
    assert instructions == "sys"
    roles = [(i["role"], i["content"][0]["type"]) for i in items]
    # assistant MUST be output_text; user MUST stay input_text
    assert ("user", "input_text") in roles
    assert ("assistant", "output_text") in roles
    assert all(
        ctype == ("output_text" if role == "assistant" else "input_text")
        for role, ctype in roles
    )

@pytest.mark.asyncio
async def test_responses_input_assistant_uses_output_text():
    """Multi-turn: assistant history items need output_text, users input_text."""
    client = DirectProviderClient(registry={})
    instructions, items = client._responses_input_from_messages({
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello! How can I help?"},
            {"role": "user", "content": "Say: pong"},
        ]
    })
    assert instructions == "sys"
    roles = [(i["role"], i["content"][0]["type"]) for i in items]
    # assistant MUST be output_text; user MUST stay input_text
    assert ("user", "input_text") in roles
    assert ("assistant", "output_text") in roles
    assert all(
        ctype == ("output_text" if role == "assistant" else "input_text")
        for role, ctype in roles
    )


def _session_endpoint(**kw) -> ProviderEndpoint:
    base = dict(name="opencode-go", base_url="https://zen.example/v1", api_key_env="", models=("muse-spark-1.2-contributor",), api_key_value="[REDACTED]")
    base.update(kw)
    return ProviderEndpoint(**base)


def test_session_header_fallback_without_client():
    """09/06+: opencode upstream always gets x-opencode-session (gateway fallback)."""
    from llm_budget_gateway.provider_direct import _UPSTREAM_CLIENT_HEADERS
    _UPSTREAM_CLIENT_HEADERS.set(None)
    ep = _session_endpoint()
    assert ep.headers()["x-opencode-session"] == "gw-opencode-go"


def test_session_header_client_forwarded():
    """Hermes PR #101864 client value wins over the gateway fallback."""
    from llm_budget_gateway.provider_direct import _UPSTREAM_CLIENT_HEADERS
    tok = _UPSTREAM_CLIENT_HEADERS.set({"x-opencode-session": "sess-abc"})
    try:
        ep = _session_endpoint()
        assert ep.headers()["x-opencode-session"] == "sess-abc"
    finally:
        _UPSTREAM_CLIENT_HEADERS.reset(tok)


def test_session_header_static_not_clobbered():
    """Static Console extra_headers_json wins over fallback; client wins over static."""
    from llm_budget_gateway.provider_direct import _UPSTREAM_CLIENT_HEADERS
    _UPSTREAM_CLIENT_HEADERS.set(None)
    ep = _session_endpoint(extra_headers={"x-opencode-session": "static-1"})
    assert ep.headers()["x-opencode-session"] == "static-1"
    tok = _UPSTREAM_CLIENT_HEADERS.set({"x-opencode-session": "client-wins"})
    try:
        assert ep.headers()["x-opencode-session"] == "client-wins"
    finally:
        _UPSTREAM_CLIENT_HEADERS.reset(tok)


def test_session_header_non_opencode_untouched():
    """Non-opencode providers never get the session header injected."""
    from llm_budget_gateway.provider_direct import _UPSTREAM_CLIENT_HEADERS
    _UPSTREAM_CLIENT_HEADERS.set({"x-opencode-session": "sess-abc"})
    ep = _session_endpoint(name="openrouter")
    assert "x-opencode-session" not in ep.headers()


@pytest.mark.asyncio
async def test_session_header_sent_on_responses_post(monkeypatch):
    """POST /responses carries x-opencode-session upstream (captured headers)."""
    from llm_budget_gateway.provider_direct import _UPSTREAM_CLIENT_HEADERS
    _UPSTREAM_CLIENT_HEADERS.set(None)
    client, calls = _client_with(monkeypatch, lambda payload: _responses_payload(payload))
    ep = client.resolve("muse-spark-1.2-contributor")
    object.__setattr__(ep, "api_mode", "codex_responses")
    await client.forward("muse-spark-1.2-contributor", {"messages": [{"role": "user", "content": "hi"}]})
    assert calls[0]["headers"]["x-opencode-session"] == "gw-opencode-go"
    _UPSTREAM_CLIENT_HEADERS.set({"x-opencode-session": "sess-live"})
    await client.forward("muse-spark-1.2-contributor", {"messages": [{"role": "user", "content": "hi"}]})
    assert calls[1]["headers"]["x-opencode-session"] == "sess-live"
    _UPSTREAM_CLIENT_HEADERS.set(None)


@pytest.mark.asyncio
async def test_mid_stream_failure_reraises_typed():
    """_rest_stream converts a mid-response upstream death to MidStreamFailure."""
    from unittest.mock import Mock
    from llm_budget_gateway.config import Settings

    async def dying_stream(model, body, kind="chat"):
        yield {"id": "c1", "object": "chat.completion.chunk", "model": model,
               "choices": [{"index": 0, "delta": {"content": "hello "}, "finish_reason": None}]}
        raise UpstreamProviderError(502, "upstream provider error: opencode-go (response.failed: boom)", body="boom")

    stub = Mock()
    stub.stream_chunks = dying_stream
    proxy = GatewayProxy(settings=Settings(virtual_keys={"k": "v"}), cost_tracker=Mock(), budget_enforcer=Mock(), fallback_manager=Mock())
    object.__setattr__(proxy, "_direct_client", stub)
    resp = await proxy._forward_direct("@opencode-go/muse", {"model": "@opencode-go/muse", "messages": [{"role": "user", "content": "hi"}], "stream": True}, stream=True)
    assert isinstance(resp.body, AsyncIterator)
    # draining must raise MidStreamFailure (not the raw UpstreamProviderError)
    with pytest.raises(MidStreamFailure) as ei:
        async for _ in resp.body:
            pass
    assert ei.value.candidate == "@opencode-go/muse"
    assert ei.value.chunks_yielded >= 1


@pytest.mark.asyncio
async def test_mid_stream_recovery_serves_next_candidate():
    """_wrapped_stream re-drives remaining candidates as a fresh stream.

    Dead candidate yields 1 chunk then dies; the finalized wrapper must
    yield the next candidate's full reply + [DONE] (not a traceback).
    """
    async def dying_stream(model, body, kind="chat"):
        yield {"id": "c1", "object": "chat.completion.chunk", "model": model,
               "choices": [{"index": 0, "delta": {"content": "partial "}, "finish_reason": None}]}
        raise UpstreamProviderError(502, "upstream provider error: dead (response.failed: boom)", body="boom")

    async def good_stream(model, body, kind="chat"):
        yield {"id": "c2", "object": "chat.completion.chunk", "model": model,
               "choices": [{"index": 0, "delta": {"content": "full reply"}, "finish_reason": None}]}

    class Router:
        def stream_chunks(self, model, body, kind="chat"):
            if "dead" in model:
                return dying_stream(model, body, kind)
            return good_stream(model, body, kind)

    proxy = GatewayProxy(settings=Settings(virtual_keys={"k": "v"}), cost_tracker=Mock(), budget_enforcer=Mock(), fallback_manager=Mock())
    tracker = Mock()
    tracker.model_in_cooldown.return_value = 0
    tracker.record_success.return_value = None
    tracker.set_model_cooldown.return_value = None
    tracker.build_record.side_effect = Exception("no db in test")
    tracker.resolve_customer_id.return_value = None
    tracker.record.return_value = None
    object.__setattr__(proxy, "_cost_tracker", tracker)
    object.__setattr__(proxy, "_direct_client", Router())

    dead_resp = await proxy._forward_direct("@dead/m", {"model": "@dead/m", "messages": [{"role": "user", "content": "hi"}], "stream": True}, stream=True)

    async def fake_chain(**kw):
        assert kw["candidates"] == ["@good/m"], kw["candidates"]
        r2 = await proxy._forward_direct("@good/m", {"model": "@good/m", "messages": [], "stream": True}, stream=True)
        return r2, "@good/m", "mid_stream", 0.0
    object.__setattr__(proxy, "_run_candidate_chain", fake_chain)

    finalized = await proxy._finalize_route_response(
        response=dead_resp, body={"model": "r", "messages": []}, api_key="k", headers={},
        metadata={}, request_id="req1", alias="r", route={}, route_name="r",
        from_plane=True, session_id=None, sticky_model=None,
        outbound={"model": "@dead/m", "messages": [{"role": "user", "content": "hi"}], "stream": True},
        want_cache=False, client_id=None, client_profile=None, conversation_id=None,
        fallback="none", served="@dead/m", chain_started=0.0,
        candidates=["@dead/m", "@good/m"], fallback_statuses=[502, 503, 504], target_cooldowns={},
    )
    events = []
    async for ev in finalized.body:
        events.append(ev)
    text = "\n".join(events)
    # The dead candidate already put ONE chunk on the wire. A partial SSE
    # prefix cannot be retracted, so re-driving the good candidate here would
    # splice two models' output into one stream: the client would store
    # "partial full reply" as a single coherent answer. Past the first chunk
    # the only honest contract is: error event, then [DONE].
    assert "partial " in text, text[:500]
    assert "full reply" not in text, (
        "the fallback candidate must NOT be spliced onto an already-sent "
        f"partial prefix: {text[:500]}"
    )
    assert "provider_error" in text, text[:500]
    assert events[-1].strip() == "data: [DONE]"
    assert "mid_stream_@dead/m" in finalized.headers["X-Gateway-Fallback"]
    assert tracker.set_model_cooldown.called


def test_transport_error_keeps_cause():
    """Bare 'upstream provider error: <name>' must not recur — cause kept."""
    import httpx
    from llm_budget_gateway.provider_direct import _transport_error
    err = _transport_error("opencode-go", httpx.ConnectError("connection refused"), "transport")
    assert err.status_code == 502
    assert "transport error: opencode-go" in str(err)
    assert "ConnectError" in str(err)
    assert "connection refused" in str(err)
    te = _transport_error("opencode-go", httpx.ReadTimeout("read timed out"), "timeout")
    assert "timed out: opencode-go" in str(te)
    assert "ReadTimeout" in str(te)
    # long causes truncated
    big = _transport_error("x", RuntimeError("z" * 900), "transport")
    assert len(str(big)) < 450


# ---------------------------------------------------------------------------
# Responses input ordering: a function_call MUST be immediately followed by
# its function_call_output. Console Go fails the WHOLE request with
#   400 "No tool output found for tool call <id>"
# when any item (e.g. the turn's own narration text) sits between them.
# ---------------------------------------------------------------------------


def _tool_turn_body(model: str = "deepseek-v4.1-flash") -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {"role": "user", "content": "list the files"},
            {
                "role": "assistant",
                "content": "Let me check that directory first.",
                "reasoning_content": "I should run ls.",
                "tool_calls": [
                    {
                        "id": "call_0",
                        "type": "function",
                        "function": {"name": "terminal", "arguments": '{"command":"ls"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_0", "content": "file1 file2"},
            {"role": "user", "content": "thanks"},
        ],
    }


def test_responses_function_call_output_stays_adjacent() -> None:
    """Regression: narration text between call and output broke every request."""
    client = DirectProviderClient(registry={})
    _, items = client._responses_input_from_messages(
        _tool_turn_body(), "deepseek-v4.1-flash"
    )
    call_idx = next(i for i, it in enumerate(items) if it.get("type") == "function_call")
    out_idx = next(
        i for i, it in enumerate(items) if it.get("type") == "function_call_output"
    )
    between = [items[i].get("type") or items[i].get("role") for i in range(call_idx + 1, out_idx)]
    assert out_idx == call_idx + 1, f"output not adjacent; interleaved: {between}"


def test_responses_assistant_text_precedes_tool_calls() -> None:
    """The turn's text must come BEFORE the calls, never after them."""
    client = DirectProviderClient(registry={})
    _, items = client._responses_input_from_messages(
        _tool_turn_body(), "deepseek-v4.1-flash"
    )
    text_idx = next(
        i
        for i, it in enumerate(items)
        if it.get("role") == "assistant" and it.get("content")
    )
    call_idx = next(i for i, it in enumerate(items) if it.get("type") == "function_call")
    assert text_idx < call_idx


def test_responses_reasoning_item_emitted_for_thinking_model() -> None:
    """Tool replays on thinking models need a reasoning item before the call."""
    client = DirectProviderClient(registry={})
    _, items = client._responses_input_from_messages(
        _tool_turn_body(), "deepseek-v4.1-flash"
    )
    reason_idx = next(i for i, it in enumerate(items) if it.get("type") == "reasoning")
    call_idx = next(i for i, it in enumerate(items) if it.get("type") == "function_call")
    assert reason_idx < call_idx
    assert items[reason_idx]["content"][0]["type"] == "reasoning_text"
    # the client's own reasoning is replayed verbatim
    assert items[reason_idx]["content"][0]["text"] == "I should run ls."


def test_responses_reasoning_padded_when_client_stripped_it() -> None:
    """A route hides the serving model, so the client may send no reasoning."""
    client = DirectProviderClient(registry={})
    body = _tool_turn_body()
    del body["messages"][1]["reasoning_content"]
    _, items = client._responses_input_from_messages(body, "deepseek-v4.1-flash")
    reason = next(it for it in items if it.get("type") == "reasoning")
    assert reason["content"][0]["text"] == " "


def test_responses_no_reasoning_item_for_non_thinking_model() -> None:
    """Non-thinking models must not receive a reasoning item."""
    client = DirectProviderClient(registry={})
    _, items = client._responses_input_from_messages(
        _tool_turn_body("muse-spark-1.3-contributor"), "muse-spark-1.3-contributor"
    )
    assert all(it.get("type") != "reasoning" for it in items)


def test_responses_model_omitted_keeps_plain_shape() -> None:
    """Callers without a model name keep the pre-fix (reasoning-free) shape."""
    client = DirectProviderClient(registry={})
    _, items = client._responses_input_from_messages(_tool_turn_body())
    assert all(it.get("type") != "reasoning" for it in items)
    call_idx = next(i for i, it in enumerate(items) if it.get("type") == "function_call")
    out_idx = next(
        i for i, it in enumerate(items) if it.get("type") == "function_call_output"
    )
    assert out_idx == call_idx + 1


def _client_with_url_routing(post_respond) -> tuple[DirectProviderClient, list[dict]]:
    """Client stub whose POST answer depends on the URL (per-endpoint behavior)."""
    calls: list[dict] = []

    async def _post(url, json=None, headers=None, timeout=None):  # noqa: A002
        calls.append({"url": url, "json": json})
        status, payload = post_respond(url, json)
        return httpx.Response(status, json=payload, request=httpx.Request("POST", url))

    client = DirectProviderClient(registry={})
    ep = _endpoint()
    object.__setattr__(ep, "api_mode", "codex_responses")
    client._registry["opencode-go"] = ep
    client._model_index["glm-5.3-flash"] = ep
    client._model_index["@opencode-go/glm-5.3-flash"] = ep
    object.__setattr__(
        client,
        "_client",
        type("C", (), {"post": staticmethod(_post), "aclose": staticmethod(lambda: None)})(),
    )
    return client, calls


def _endpoint_unavailable(url: str) -> tuple[int, dict]:
    return 503, {
        "error": {
            "type": "server_error",
            "message": "Upstream request failed: Endpoint is unavailable.",
        }
    }


def _chat_pong(url: str, request_json: dict) -> tuple[int, dict]:
    return 200, {
        "id": "c1",
        "model": request_json["model"],
        "choices": [{"message": {"role": "assistant", "content": "pong"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


@pytest.mark.asyncio
async def test_responses_endpoint_unavailable_falls_back_to_chat():
    """503 'Endpoint is unavailable' on /responses → same-attempt /chat/completions retry.

    Live 2026-09-25: opencode-go serves glm-5.3-flash + mimo-v2.6-flash (+21 more)
    on /chat/completions only; /responses answers 503 in ~0.4s. The provider-wide
    api_mode=codex_responses sends them down the dead path, so one failed
    endpoint must not cost the whole candidate — retry chat in the same attempt.
    """
    client, calls = _client_with_url_routing(
        lambda url, payload: _endpoint_unavailable(url)
        if url.endswith("/responses")
        else _chat_pong(url, payload)
    )

    status, data, _served = await client.forward(
        "@opencode-go/glm-5.3-flash",
        {"messages": [{"role": "user", "content": "pong"}]},
    )

    assert status == 200
    assert [c["url"].rsplit("/", 1)[-1] for c in calls] == ["responses", "completions"]
    assert calls[1]["json"]["model"] == "glm-5.3-flash"
    assert "messages" in calls[1]["json"]
    assert data["choices"][0]["message"]["content"] == "pong"


@pytest.mark.asyncio
async def test_responses_other_error_does_not_fall_back():
    """A non-endpoint error on /responses (e.g. 500) must surface, not retry chat.

    Falling back on every 5xx would double calls for models broken on BOTH
    endpoints (live: minimax-m2.7) and mask real outages.
    """
    client, calls = _client_with_url_routing(
        lambda url, payload: (
            (500, {"error": {"type": "server_error", "message": "boom"}})
            if url.endswith("/responses")
            else _chat_pong(url, payload)
        )
    )

    with pytest.raises(UpstreamProviderError) as exc_info:
        await client.forward(
            "@opencode-go/glm-5.3-flash",
            {"messages": [{"role": "user", "content": "pong"}]},
        )

    assert exc_info.value.status_code == 500
    assert len(calls) == 1
    assert calls[0]["url"].endswith("/responses")


@pytest.mark.asyncio
async def test_stream_chunks_endpoint_unavailable_falls_back_to_chat():
    """Streaming variant: dead /responses stream → chat SSE in the same attempt."""
    seen: list[str] = []

    class _ErrCtx:
        async def __aenter__(self):
            class R:
                status_code = 503

                async def aread(self):
                    return (
                        b'{"error":{"type":"server_error","message":'
                        b'"Upstream request failed: Endpoint is unavailable."}}'
                    )

                async def aiter_lines(self):
                    yield ""
                    raise AssertionError("must not read lines on 503")

            return R()

        async def __aexit__(self, *exc):
            return False

    class _OkCtx:
        async def __aenter__(self):
            class R:
                status_code = 200

                async def aread(self):
                    return b""

                async def aiter_lines(self):
                    yield 'data: {"id":"c1","choices":[{"index":0,"delta":{"content":"pong"},"finish_reason":null}]}'
                    yield "data: [DONE]"

            return R()

        async def __aexit__(self, *exc):
            return False

    def _stream(method, url, json=None, headers=None):  # noqa: A002
        seen.append(url)
        return _ErrCtx() if url.endswith("/responses") else _OkCtx()

    client = DirectProviderClient(registry={})
    ep = _endpoint()
    object.__setattr__(ep, "api_mode", "codex_responses")
    client._registry["opencode-go"] = ep
    client._model_index["glm-5.3-flash"] = ep
    object.__setattr__(client, "_client", type("C", (), {"stream": staticmethod(_stream)})())

    chunks = [
        c
        async for c in client.stream_chunks(
            "glm-5.3-flash",
            {"messages": [{"role": "user", "content": "pong"}]},
        )
    ]

    assert [u.rsplit("/", 1)[-1] for u in seen] == ["responses", "completions"]
    assert any(
        c.get("choices", [{}])[0].get("delta", {}).get("content") == "pong" for c in chunks
    )
