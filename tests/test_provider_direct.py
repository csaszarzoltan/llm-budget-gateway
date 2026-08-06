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


def _client(handler, registry=None):
    """Build a DirectProviderClient with a mocked httpx transport."""
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return DirectProviderClient(
        registry or REGISTRY, timeout=5.0, client=client
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
