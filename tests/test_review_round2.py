"""Regression tests for tech-lead review round 2 (t_71aaa8f1).

Covers four findings at HEAD a43ffac:
  A. BLOCKER — stream=true crashes at the HTTP layer (raw litellm chunk
     objects handed to StreamingResponse, which requires bytes/str).
  B. MEDIUM  — no upstream timeout on provider calls (hung upstream hangs
     the request/worker forever).
  C. MEDIUM  — /v1/embeddings routed to litellm.acompletion (which has no
     ``input`` param); must route to litellm.aembedding.
  D. MINOR   — full submitted virtual key logged on auth failure; redact.

Run: .venv/bin/python -m pytest tests/test_review_round2.py -v
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from llm_budget_gateway.config import Settings
from llm_budget_gateway.gateway_proxy import (
    GatewayProxy,
    ProviderResponse,
    ProviderTimeoutError,
)
from llm_budget_gateway.main import create_app


def _app_settings(tmp_path) -> Settings:
    return Settings(
        virtual_keys={"sk-test": "key1"},
        database_url=f"sqlite:///{tmp_path}/gateway.db",
    )


@pytest.fixture
def settings() -> Settings:
    return Settings(
        virtual_keys={"sk_test_abc": "key1"},
        user_header_mappings={"X-User-Id": "user", "X-Team-Id": "team"},
    )


@pytest.fixture
def proxy(settings: Settings) -> GatewayProxy:
    """GatewayProxy with Mock dependencies (shared shape with
    test_gateway_proxy.py; fixtures are per-module)."""
    return GatewayProxy(
        settings=settings,
        cost_tracker=Mock(),
        budget_enforcer=Mock(),
        fallback_manager=Mock(),
    )


# ---------------------------------------------------------------------------
# A. stream=true must produce SSE at the HTTP layer (P0-1 AC3)
# ---------------------------------------------------------------------------


class TestStreamSse:
    @pytest.mark.asyncio
    async def test_stream_true_http_layer_returns_sse_shape(
        self, tmp_path, mocker
    ) -> None:
        """BLOCKER A: POST stream:true through create_app must return 200 with
        an SSE-shaped body — never an AttributeError from StreamingResponse."""
        app = create_app(settings=_app_settings(tmp_path))

        async def _chunks():
            yield SimpleNamespace(
                model="gpt-4o",
                choices=[{"delta": {"role": "assistant", "content": "hi"}}],
            )
            yield SimpleNamespace(
                model="gpt-4o",
                choices=[{"delta": {"content": "!"}}],
            )

        mocker.patch("litellm.acompletion", new=AsyncMock(return_value=_chunks()))

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                },
                headers={"Authorization": "Bearer sk-test"},
            )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        lines = [line for line in resp.text.split("\n") if line.startswith("data: ")]
        assert len(lines) >= 2  # at least one chunk + terminal [DONE]
        assert "hi" in resp.text
        assert resp.text.rstrip().endswith("data: [DONE]")

    @pytest.mark.asyncio
    async def test_forward_stream_body_is_serialized_sse_lines(
        self, proxy: GatewayProxy, mocker
    ) -> None:
        """BLOCKER A: forward must serialize drained chunks into SSE lines
        (str), not raw litellm chunk objects (StreamingResponse.encode crash)."""
        async def _chunks():
            yield SimpleNamespace(
                model="gpt-4o",
                choices=[{"delta": {"role": "assistant", "content": "hi"}}],
            )

        mocker.patch("litellm.acompletion", new=AsyncMock(return_value=_chunks()))
        result = await proxy.forward(
            "gpt-4o", {"model": "gpt-4o", "stream": True}, stream=True
        )
        assert isinstance(result.body, list)
        assert result.body  # non-empty
        assert all(isinstance(line, str) for line in result.body)
        assert result.body[0].startswith("data: ")
        assert result.body[-1] == "data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# B. upstream timeout: Settings.provider_timeout -> clean 502, no hang
# ---------------------------------------------------------------------------


class TestProviderTimeout:
    @pytest.mark.asyncio
    async def test_upstream_timeout_maps_to_502(self, tmp_path, mocker) -> None:
        """MEDIUM B: a hung upstream must map to a clean 502 and return
        promptly (no infinite hang)."""
        settings = _app_settings(tmp_path)
        settings.provider_timeout = 0.05
        app = create_app(settings=settings)

        async def _hang(*args, **kwargs):
            await asyncio.sleep(3600)

        mocker.patch("litellm.acompletion", new=AsyncMock(side_effect=_hang))

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", timeout=10.0
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "hi"}],
                },
                headers={"Authorization": "Bearer sk-test"},
            )
        assert resp.status_code == 502
        assert "timed out" in resp.text

    @pytest.mark.asyncio
    async def test_forward_hung_upstream_raises_provider_timeout(
        self, mocker
    ) -> None:
        """MEDIUM B unit: forward surfaces a ProviderTimeoutError when the
        upstream does not respond within Settings.provider_timeout."""
        settings = Settings(virtual_keys={"sk-test": "key1"}, provider_timeout=0.05)
        proxy = GatewayProxy(
            settings=settings,
            cost_tracker=Mock(),
            budget_enforcer=Mock(),
            fallback_manager=Mock(),
        )

        async def _hang(*args, **kwargs):
            await asyncio.sleep(3600)

        mocker.patch("litellm.acompletion", new=AsyncMock(side_effect=_hang))
        with pytest.raises(ProviderTimeoutError):
            await proxy.forward(
                "gpt-4o",
                {
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )

    @pytest.mark.asyncio
    async def test_stream_hang_mid_drain_maps_to_502(self, tmp_path, mocker) -> None:
        """MEDIUM B: a stream that stalls mid-flight (no chunk within the
        timeout) must also surface as a 502, not hang the worker."""
        settings = _app_settings(tmp_path)
        settings.provider_timeout = 0.05
        app = create_app(settings=settings)

        async def _chunks():
            yield SimpleNamespace(
                model="gpt-4o",
                choices=[{"delta": {"role": "assistant", "content": "hi"}}],
            )
            await asyncio.sleep(3600)

        mocker.patch("litellm.acompletion", new=AsyncMock(return_value=_chunks()))

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", timeout=10.0
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                },
                headers={"Authorization": "Bearer sk-test"},
            )
        assert resp.status_code == 502
        assert "timed out" in resp.text

    def test_provider_timeout_setting_default_and_env(self, monkeypatch) -> None:
        """MEDIUM B: Settings.provider_timeout exists, defaults to 60s and is
        overridable via GATEWAY_PROVIDER_TIMEOUT."""
        assert Settings().provider_timeout == 60.0
        monkeypatch.setenv("GATEWAY_PROVIDER_TIMEOUT", "7.5")
        assert Settings().provider_timeout == 7.5


# ---------------------------------------------------------------------------
# C. /v1/embeddings must route to litellm.aembedding
# ---------------------------------------------------------------------------


class TestEmbeddingsRouting:
    @pytest.mark.asyncio
    async def test_forward_embeddings_body_calls_aembedding(
        self, proxy: GatewayProxy, mocker
    ) -> None:
        """MEDIUM C unit: forward with an embeddings body (``input``, no
        messages/prompt) calls litellm.aembedding and populates usage."""
        aembedding = mocker.patch("litellm.aembedding", new=AsyncMock())
        aembedding.return_value = SimpleNamespace(
            object="list",
            data=[{"embedding": [0.1, 0.2], "index": 0}],
            usage=SimpleNamespace(prompt_tokens=7, total_tokens=7),
            model="text-embedding-3-small",
        )
        acompletion = mocker.patch("litellm.acompletion", new=AsyncMock())

        body = {"model": "text-embedding-3-small", "input": ["hello world"]}
        result = await proxy.forward("text-embedding-3-small", body)

        aembedding.assert_awaited_once()
        acompletion.assert_not_awaited()
        kwargs = aembedding.await_args.kwargs
        assert kwargs["model"] == "text-embedding-3-small"
        assert kwargs["input"] == ["hello world"]
        assert isinstance(result, ProviderResponse)
        assert result.usage is not None
        assert result.usage.prompt_tokens == 7

    @pytest.mark.asyncio
    async def test_embeddings_http_layer_returns_200(
        self, tmp_path, mocker
    ) -> None:
        """MEDIUM C: POST /v1/embeddings through create_app is served by
        aembedding (200 JSON), not misrouted to acompletion."""
        app = create_app(settings=_app_settings(tmp_path))
        aembedding = mocker.patch("litellm.aembedding", new=AsyncMock())
        # dict-shaped return: real aembedding yields an EmbeddingResponse
        # (model_dump-able); a dict exercises the JSON-serializable path
        # end-to-end.
        aembedding.return_value = {
            "object": "list",
            "data": [{"embedding": [0.1, 0.2], "index": 0}],
            "usage": {"prompt_tokens": 7, "total_tokens": 7},
            "model": "text-embedding-3-small",
        }
        acompletion = mocker.patch("litellm.acompletion", new=AsyncMock())

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/embeddings",
                json={"model": "text-embedding-3-small", "input": ["hello"]},
                headers={"Authorization": "Bearer sk-test"},
            )
        assert resp.status_code == 200
        assert resp.json()["object"] == "list"
        aembedding.assert_awaited_once()
        acompletion.assert_not_awaited()


# ---------------------------------------------------------------------------
# D. submitted api key redacted in auth-failure logs
# ---------------------------------------------------------------------------


class TestKeyRedaction:
    @pytest.mark.asyncio
    async def test_auth_failure_logs_redacted_key_only(
        self, proxy: GatewayProxy, caplog
    ) -> None:
        """MINOR D: the full submitted virtual key must never appear in
        server logs; only a redacted form is logged."""
        secret = "sk-live-0123456789abcdef"
        log_mod = "llm_budget_gateway.gateway_proxy"
        with caplog.at_level(logging.WARNING, logger=log_mod):
            result = await proxy.handle_chat_completion(
                {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
                secret,
                {},
            )
        assert result.status_code == 401
        assert secret not in caplog.text
        # redacted prefix + length marker is present (not the full key)
        assert f"{secret[:4]}…" in caplog.text
