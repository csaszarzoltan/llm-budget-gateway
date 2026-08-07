"""Tests for the direct provider transport (no litellm)."""

from __future__ import annotations

import json

import httpx
import pytest

from llm_budget_gateway.provider_direct import (
    DirectProviderClient,
    ProviderConfigError,
    UnknownModelError,
    UpstreamProviderError,
)

REGISTRY = {
    "opencode-zen": {
        "base_url": "https://opencode.ai/zen/v1",
        "api_key_env": "OPENCODE_ZEN_API_KEY",
        "auth": "bearer",
        "models": ["mimo-v2.5-free", "mimo-v2.5"],
    },
    "opencode-go": {
        "base_url": "https://opencode.ai/zen/go/v1",
        "api_key_env": "OPENCODE_GO_API_KEY",
        "auth": "bearer",
        "models": ["deepseek-v4-flash"],
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "api_key_env": "GOOGLE_API_KEY",
        "auth": "query",
        "models": ["gemini-2.0-flash"],
    },
}


def _client(handler, registry=None, signature_db_path=None):
    """Build a DirectProviderClient with a mocked httpx transport."""
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return DirectProviderClient(
        registry or REGISTRY, timeout=5.0, client=client,
        signature_db_path=signature_db_path,
    )


def _ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "model": "mimo-v2.5-free",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        },
    )


class TestRegistryLoading:
    def test_from_env_empty(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_PROVIDER_REGISTRY", "")
        client = DirectProviderClient.from_env(timeout=5.0)
        assert client.models == []
        import asyncio
        asyncio.run(client.aclose())

    def test_from_env_json(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_PROVIDER_REGISTRY", json.dumps(REGISTRY))
        client = DirectProviderClient.from_env(timeout=5.0)
        assert "mimo-v2.5-free" in client.models
        assert "deepseek-v4-flash" in client.models
        import asyncio
        asyncio.run(client.aclose())

    def test_from_env_invalid_json(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_PROVIDER_REGISTRY", "{not json")
        with pytest.raises(ProviderConfigError):
            DirectProviderClient.from_env(timeout=5.0)

    def test_duplicate_flat_model_first_wins_alias_pins(self):
        """Flat duplicates resolve to the first provider; @slug/ pin disambiguates."""
        registry = {
            "a": {
                "base_url": "https://a.example.com/v1",
                "api_key_env": "A_KEY",
                "models": ["shared-model"],
            },
            "b": {
                "base_url": "https://b.example.com/v1",
                "api_key_env": "B_KEY",
                "models": ["shared-model"],
            },
        }
        client = DirectProviderClient(registry, timeout=5.0)
        # flat name -> first provider in registry order
        assert client.resolve("shared-model").name == "a"
        # qualified alias -> exact provider
        assert client.resolve("@a/shared-model").name == "a"
        assert client.resolve("@b/shared-model").name == "b"

    def test_http_base_url_rejected(self):
        with pytest.raises(ProviderConfigError):
            DirectProviderClient(
                {
                    "bad": {
                        "base_url": "ftp://nope",
                        "api_key_env": "K",
                        "models": ["m"],
                    }
                },
                timeout=5.0,
            )


class TestResolve:
    def test_resolve_known_model(self):
        client = DirectProviderClient(REGISTRY, timeout=5.0)
        endpoint = client.resolve("mimo-v2.5-free")
        assert endpoint.name == "opencode-zen"
        assert endpoint.base_url == "https://opencode.ai/zen/v1"

    def test_resolve_unknown_model(self):
        client = DirectProviderClient(REGISTRY, timeout=5.0)
        with pytest.raises(UnknownModelError):
            client.resolve("no-such-model")

    def test_missing_api_key_env(self, monkeypatch):
        monkeypatch.delenv("OPENCODE_ZEN_API_KEY", raising=False)
        client = DirectProviderClient(REGISTRY, timeout=5.0)
        endpoint = client.resolve("mimo-v2.5-free")
        with pytest.raises(ProviderConfigError):
            endpoint.api_key()


class TestForward:
    @pytest.mark.asyncio
    async def test_forward_chat(self, monkeypatch):
        monkeypatch.setenv("OPENCODE_ZEN_API_KEY", "sk-zen-123")
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["auth"] = request.headers.get("authorization")
            captured["body"] = json.loads(request.content)
            return _ok_handler(request)

        client = _client(handler)
        status, data, served = await client.forward(
            "mimo-v2.5-free",
            {
                "model": "mimo-v2.5-free",
                "messages": [{"role": "user", "content": "hi"}],
                "api_key": "sk-evil",
                "base_url": "https://evil.example.com",
            },
        )
        assert status == 200
        assert data["choices"][0]["message"]["content"] == "Hello"
        assert served == "mimo-v2.5-free"
        assert captured["url"].startswith("https://opencode.ai/zen/v1/chat/completions")
        assert captured["auth"] == "Bearer sk-zen-123"
        # injection fields stripped
        assert "api_key" not in captured["body"]
        assert "base_url" not in captured["body"]
        assert captured["body"]["model"] == "mimo-v2.5-free"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_forward_unknown_model(self, monkeypatch):
        monkeypatch.setenv("OPENCODE_ZEN_API_KEY", "sk-zen-123")
        client = _client(_ok_handler)
        with pytest.raises(UnknownModelError):
            await client.forward("nope", {"model": "nope"})
        await client.aclose()

    @pytest.mark.asyncio
    async def test_forward_upstream_error(self, monkeypatch):
        monkeypatch.setenv("OPENCODE_ZEN_API_KEY", "sk-zen-123")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "boom"})

        client = _client(handler)
        with pytest.raises(UpstreamProviderError) as exc_info:
            await client.forward("mimo-v2.5-free", {"model": "mimo-v2.5-free"})
        assert exc_info.value.status_code == 500
        await client.aclose()

    @pytest.mark.asyncio
    async def test_forward_query_auth(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "AIza-123")
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return _ok_handler(request)

        client = _client(handler)
        await client.forward("gemini-2.0-flash", {"model": "gemini-2.0-flash"})
        assert "key=AIza-123" in captured["url"]
        assert captured["url"].startswith(
            "https://generativelanguage.googleapis.com/v1beta/chat/completions"
        )
        await client.aclose()


class TestForwardStream:
    @pytest.mark.asyncio
    async def test_forward_stream_drains_chunks(self, monkeypatch):
        monkeypatch.setenv("OPENCODE_GO_API_KEY", "sk-go-123")

        def handler(request: httpx.Request) -> httpx.Response:
            body = (
                'data: {"model": "deepseek-v4-flash", "choices": [{"delta": {"content": "Hel"}, "finish_reason": null}]}\n\n'
                'data: {"model": "deepseek-v4-flash", "choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}\n\n'
                "data: [DONE]\n\n"
            )
            return httpx.Response(200, text=body)

        client = _client(handler)
        status, chunks, served = await client.forward_stream(
            "deepseek-v4-flash", {"model": "deepseek-v4-flash", "stream": True}
        )
        assert status == 200
        assert len(chunks) == 2
        assert chunks[0]["choices"][0]["delta"]["content"] == "Hel"
        assert chunks[1]["usage"]["total_tokens"] == 15
        assert served == "deepseek-v4-flash"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_forward_stream_error(self, monkeypatch):
        monkeypatch.setenv("OPENCODE_GO_API_KEY", "sk-go-123")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"error": "rate limited"})

        client = _client(handler)
        with pytest.raises(UpstreamProviderError) as exc_info:
            await client.forward_stream(
                "deepseek-v4-flash", {"model": "deepseek-v4-flash", "stream": True}
            )
        assert exc_info.value.status_code == 429
        # the real provider error body survives (was swallowed pre-fix)
        assert "rate limited" in exc_info.value.body
        await client.aclose()

    @pytest.mark.asyncio
    async def test_gemini_thought_signature_roundtrip(self, monkeypatch):
        """Gemini: tool-call thought_signature from the response is captured
        and re-attached to the matching assistant tool_call on the next
        outbound request (clients routing by route name drop the field)."""
        monkeypatch.setenv("GOOGLE_API_KEY", "sk-google-123")
        captured = {}
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                # First call: the model answers with a tool call carrying
                # extra_content.google.thought_signature.
                return httpx.Response(200, json={
                    "model": "gemini-2.0-flash",
                    "choices": [{
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "tool_calls": [{
                                "id": "tc-gemini-1",
                                "type": "function",
                                "function": {"name": "kanban_show", "arguments": "{}"},
                                "extra_content": {"google": {"thought_signature": "SIG123"}},
                            }],
                        },
                    }],
                })
            # Second call: replay with the assistant tool_call — the gateway
            # must have re-attached the signature.
            captured["body"] = json.loads(request.content)
            return _ok_handler(request)

        client = _client(handler)
        # turn 1: model returns a tool call
        status, data, served = await client.forward(
            "gemini-2.0-flash",
            {"model": "gemini-2.0-flash", "messages": [{"role": "user", "content": "go"}]},
        )
        assert status == 200
        # turn 2: the client replays the assistant tool call without the field
        await client.forward(
            "gemini-2.0-flash",
            {
                "model": "gemini-2.0-flash",
                "messages": [
                    {"role": "user", "content": "go"},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "tc-gemini-1",
                            "type": "function",
                            "function": {"name": "kanban_show", "arguments": "{}"},
                        }],
                    },
                ],
            },
        )
        msgs = captured["body"]["messages"]
        tc = msgs[1]["tool_calls"][0]
        assert tc["extra_content"]["google"]["thought_signature"] == "SIG123"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_gemini_thought_signature_unknown_id_not_touched(self, monkeypatch):
        """Unknown tool_call ids must not get a fabricated signature."""
        monkeypatch.setenv("GOOGLE_API_KEY", "sk-google-123")
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return _ok_handler(request)

        client = _client(handler)
        await client.forward(
            "gemini-2.0-flash",
            {
                "model": "gemini-2.0-flash",
                "messages": [
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "tc-unknown",
                            "type": "function",
                            "function": {"name": "x", "arguments": "{}"},
                        }],
                    },
                ],
            },
        )
        tc = captured["body"]["messages"][0]["tool_calls"][0]
        assert "extra_content" not in tc
        await client.aclose()

    @pytest.mark.asyncio
    async def test_slug_qualified_alias_routes_to_provider(self, monkeypatch):
        """@slug/model pins a model to one provider and strips the prefix."""
        monkeypatch.setenv("OPENCODE_GO_API_KEY", "sk-go-123")
        monkeypatch.setenv("OPENCODE_ZEN_API_KEY", "sk-zen-123")
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["auth"] = request.headers.get("authorization")
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"model": "mimo-v2.5", "choices": [], "usage": {}})

        # mimo-v2.5 exists on BOTH opencode-zen and opencode-go in this
        # registry, so the bare name resolves to the first (zen);
        # @opencode-go/mimo-v2.5 must pin it to the go endpoint.
        registry = {
            "opencode-zen": {
                "base_url": "https://opencode.ai/zen/v1",
                "api_key_env": "OPENCODE_ZEN_API_KEY",
                "auth": "bearer",
                "models": ["mimo-v2.5-free", "mimo-v2.5"],
            },
            "opencode-go": {
                "base_url": "https://opencode.ai/zen/go/v1",
                "api_key_env": "OPENCODE_GO_API_KEY",
                "auth": "bearer",
                "models": ["deepseek-v4-flash", "mimo-v2.5"],
            },
        }
        client = _client(handler, registry)
        status, data, served = await client.forward("@opencode-go/mimo-v2.5", {"messages": []})
        assert status == 200
        assert "zen/go" in captured["url"]
        assert captured["auth"] == "Bearer sk-go-123"
        assert captured["body"]["model"] == "mimo-v2.5"  # bare name upstream
        await client.aclose()

    @pytest.mark.asyncio
    async def test_slug_qualified_alias_stream(self, monkeypatch):
        monkeypatch.setenv("OPENCODE_GO_API_KEY", "sk-go-123")
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, text="data: {\"choices\":[]}\n\ndata: [DONE]\n")

        client = _client(handler)
        status, chunks, served = await client.forward_stream(
            "@opencode-go/deepseek-v4-flash", {"messages": []}
        )
        assert status == 200
        assert captured["body"]["model"] == "deepseek-v4-flash"
        assert captured["body"]["stream"] is True
        await client.aclose()


    @pytest.mark.asyncio
    async def test_user_agent_emulation_header_sent(self, monkeypatch):
        """Client-emulation User-Agent must reach the upstream request."""
        monkeypatch.setenv("OPENCODE_ZEN_API_KEY", "sk-zen-123")
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["ua"] = request.headers.get("user-agent")
            return _ok_handler(request)

        registry = {
            "opencode-go": {
                "base_url": "https://opencode.ai/zen/go/v1",
                "api_key_env": "OPENCODE_ZEN_API_KEY",
                "user_agent": "opencode/1.14.41",
                "models": ["deepseek-v4-flash"],
            }
        }
        client = _client(handler, registry)
        status, data, served = await client.forward(
            "deepseek-v4-flash",
            {"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert status == 200
        assert captured["ua"] == "opencode/1.14.41"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_default_user_agent_is_httpx_when_not_configured(self, monkeypatch):
        """Without a configured user_agent httpx's own UA is sent (the
        upstream-visible identity that motivated client emulation)."""
        monkeypatch.setenv("OPENCODE_ZEN_API_KEY", "sk-zen-123")
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["ua"] = request.headers.get("user-agent")
            return _ok_handler(request)

        client = _client(handler)  # REGISTRY has no user_agent
        status, data, served = await client.forward(
            "mimo-v2.5-free",
            {"model": "mimo-v2.5-free", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert status == 200
        assert captured["ua"] is not None
        assert captured["ua"].startswith("python-httpx/")
        await client.aclose()

    @pytest.mark.asyncio
    async def test_reasoning_model_pads_assistant_reasoning_content(self, monkeypatch):
        """DeepSeek/Kimi/MiMo thinking mode: assistant turns without
        reasoning_content get a single-space pad so the upstream does not
        reject the replay with HTTP 400 (clients routing by route name
        strip the field because they cannot see the serving model)."""
        monkeypatch.setenv("OPENCODE_GO_API_KEY", "sk-go-123")
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return _ok_handler(request)

        client = _client(handler)
        await client.forward(
            "deepseek-v4-flash",
            {
                "model": "deepseek-v4-flash",
                "messages": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello"},
                    {"role": "user", "content": "again"},
                ],
            },
        )
        msgs = captured["body"]["messages"]
        assert msgs[1]["reasoning_content"] == " "
        # user turns untouched
        assert "reasoning_content" not in msgs[0]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_non_reasoning_model_leaves_messages_alone(self, monkeypatch):
        """Strict providers must not receive a reasoning_content pad."""
        monkeypatch.setenv("GOOGLE_API_KEY", "sk-google-123")
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return _ok_handler(request)

        client = _client(handler)
        await client.forward(
            "gemini-2.0-flash",
            {
                "model": "gemini-2.0-flash",
                "messages": [{"role": "assistant", "content": "hello"}],
            },
        )
        msgs = captured["body"]["messages"]
        assert "reasoning_content" not in msgs[0]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_tool_name_colon_rewritten_and_restored(self, monkeypatch):
        """Colon-qualified tool names (Hermes default_api:x) are rewritten for
        strict upstreams (Console Go regex) and restored on the response."""
        monkeypatch.setenv("OPENCODE_GO_API_KEY", "sk-go-123")
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={
                "model": "deepseek-v4-flash",
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "tool_calls": [{
                            "id": "tc1",
                            "type": "function",
                            "function": {"name": "default_api_kanban_show", "arguments": "{}"},
                        }],
                    },
                }],
            })

        client = _client(handler)
        status, data, served = await client.forward(
            "deepseek-v4-flash",
            {
                "model": "deepseek-v4-flash",
                "messages": [{"role": "user", "content": "go"}],
                "tools": [{"type": "function", "function": {
                    "name": "default_api:kanban_show",
                    "description": "show",
                    "parameters": {"type": "object", "properties": {}}}}],
            },
        )
        assert status == 200
        # upstream saw the rewritten name
        assert captured["body"]["tools"][0]["function"]["name"] == "default_api_kanban_show"
        # client sees the original name back
        tc = data["choices"][0]["message"]["tool_calls"][0]
        assert tc["function"]["name"] == "default_api:kanban_show"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_tool_name_without_colon_untouched(self, monkeypatch):
        """Plain tool names must pass through unchanged."""
        monkeypatch.setenv("OPENCODE_GO_API_KEY", "sk-go-123")
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return _ok_handler(request)

        client = _client(handler)
        await client.forward(
            "deepseek-v4-flash",
            {
                "model": "deepseek-v4-flash",
                "messages": [{"role": "user", "content": "go"}],
                "tools": [{"type": "function", "function": {
                    "name": "kanban_show",
                    "description": "show",
                    "parameters": {"type": "object", "properties": {}}}}],
            },
        )
        assert captured["body"]["tools"][0]["function"]["name"] == "kanban_show"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_thought_signature_survives_restart(self, monkeypatch, tmp_path):
        """Signatures persist to the gateway DB so a restart does not lose
        them (Hermes replays tool_calls without extra_content)."""
        monkeypatch.setenv("GOOGLE_API_KEY", "sk-google-123")
        db_path = str(tmp_path / "gw.db")
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(200, json={
                    "model": "gemini-2.0-flash",
                    "choices": [{
                        "finish_reason": "tool_calls",
                        "message": {"role": "assistant", "tool_calls": [{
                            "id": "gid-1",
                            "type": "function",
                            "function": {"name": "skill_view", "arguments": "{}"},
                            "extra_content": {"google": {"thought_signature": "SIGPERSIST"}},
                        }]},
                    }],
                })
            captured["body"] = json.loads(request.content)
            return _ok_handler(request)

        captured = {}
        client = _client(handler, signature_db_path=db_path)
        await client.forward("gemini-2.0-flash", {"model": "gemini-2.0-flash", "messages": [{"role": "user", "content": "go"}]})
        await client.aclose()

        # simulate a gateway restart: brand new client instance, same DB
        client2 = _client(handler, signature_db_path=db_path)
        await client2.forward("gemini-2.0-flash", {
            "model": "gemini-2.0-flash",
            "messages": [
                {"role": "user", "content": "go"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "call_rewritten_id", "type": "function",
                    "function": {"name": "skill_view", "arguments": "{}"},
                }]},
            ],
        })
        tc = captured["body"]["messages"][1]["tool_calls"][0]
        assert tc["extra_content"]["google"]["thought_signature"] == "SIGPERSIST"
        await client2.aclose()

    @pytest.mark.asyncio
    async def test_gemini_thought_signature_fallback_by_fn_args(self, monkeypatch):
        """Clients that rewrite provider call ids (Hermes) still get the
        signature matched via (fn_name, arguments)."""
        monkeypatch.setenv("GOOGLE_API_KEY", "sk-google-123")
        captured = {}
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(200, json={
                    "model": "gemini-2.0-flash",
                    "choices": [{
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "tool_calls": [{
                                "id": "gemini-id-abc",
                                "type": "function",
                                "function": {"name": "skill_view", "arguments": "{}"},
                                "extra_content": {"google": {"thought_signature": "SIGFN"}},
                            }],
                        },
                    }],
                })
            captured["body"] = json.loads(request.content)
            return _ok_handler(request)

        client = _client(handler)
        await client.forward(
            "gemini-2.0-flash",
            {"model": "gemini-2.0-flash", "messages": [{"role": "user", "content": "go"}]},
        )
        # Hermes rewrites the id but keeps fn name + arguments
        await client.forward(
            "gemini-2.0-flash",
            {
                "model": "gemini-2.0-flash",
                "messages": [
                    {"role": "user", "content": "go"},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "call_51366e98f2354ce2bc66f224",
                            "type": "function",
                            "function": {"name": "skill_view", "arguments": "{}"},
                        }],
                    },
                ],
            },
        )
        tc = captured["body"]["messages"][1]["tool_calls"][0]
        assert tc["extra_content"]["google"]["thought_signature"] == "SIGFN"
        await client.aclose()


@pytest.mark.asyncio
async def test_extra_body_merged_into_payload():
    """Provider-level extra_body (e.g. DeepInfra service_tier: flex) must be
    merged into the outbound payload, not hardcoded in the client."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _ok_handler(request)

    registry = {
        "deepinfra": {
            "base_url": "https://api.deepinfra.com/v1/openai",
            "api_key_env": "DEEPINFRA_API_KEY",
            "api_key": "test-key",
            "models": ["deepseek-ai/DeepSeek-V3"],
            "extra_body": {"service_tier": "flex"},
        }
    }
    client = _client(handler, registry)
    await client.forward(
        "@deepinfra/deepseek-ai/DeepSeek-V3",
        {"model": "@deepinfra/deepseek-ai/DeepSeek-V3", "messages": [{"role": "user", "content": "go"}]},
    )
    body = captured["body"]
    assert isinstance(body, dict)
    assert body.get("service_tier") == "flex"
    # the bare model name goes upstream, not the @-qualified alias
    assert body["model"] == "deepseek-ai/DeepSeek-V3"
    await client.aclose()


@pytest.mark.asyncio
async def test_extra_body_from_registry_raw_load():
    """The registry loader must pass extra_body through from raw config so a
    provider connection's Extra body JSON field reaches the payload."""
    registry = {
        "deepinfra": {
            "base_url": "https://api.deepinfra.com/v1/openai",
            "api_key_env": "DEEPINFRA_API_KEY",
            "api_key": "test-key",
            "models": ["deepseek-ai/DeepSeek-V3"],
            "extra_body": {"service_tier": "flex"},
        }
    }
    client = DirectProviderClient(registry)
    endpoint = client.resolve("@deepinfra/deepseek-ai/DeepSeek-V3")
    assert endpoint.extra_body == {"service_tier": "flex"}
    await client.aclose()
