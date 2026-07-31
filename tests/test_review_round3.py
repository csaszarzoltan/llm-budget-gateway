"""Regression tests for the post-approval follow-up (t_3bdb075b).

Covers two findings at HEAD f57855f:
  N1. MEDIUM — boundary input validation: malformed JSON bodies must map
     to a 400 invalid_request_error (not an unhandled JSONDecodeError →
     500), and non-object JSON bodies (``[1,2]``, ``"str"``) must map to
     400 (not fall through to the model check → 404 ``unknown model: ``).
  N2. MINOR  — embeddings stream field: a client-sent ``stream`` /
     ``stream_options`` on /v1/embeddings must never reach
     litellm.aembedding (no stream support → upstream 502).

Run: .venv/bin/python -m pytest tests/test_review_round3.py -v
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from llm_budget_gateway.config import Settings
from llm_budget_gateway.gateway_proxy import GatewayProxy
from llm_budget_gateway.main import create_app


def _app_settings(tmp_path) -> Settings:
    return Settings(
        virtual_keys={"sk-test": "key1"},
        database_url=f"sqlite:///{tmp_path}/gateway.db",
    )


@pytest.fixture
def proxy() -> GatewayProxy:
    """GatewayProxy with Mock dependencies (shared shape with
    test_review_round2.py)."""
    return GatewayProxy(
        settings=Settings(virtual_keys={"sk-test": "key1"}),
        cost_tracker=Mock(),
        budget_enforcer=Mock(),
        fallback_manager=Mock(),
    )


def _assert_error_shape(resp: httpx.Response, status: int) -> None:
    """Assert the standard OpenAI-style error body is returned."""
    assert resp.status_code == status
    body = resp.json()
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["code"] == status
    assert body["error"]["message"]


# ---------------------------------------------------------------------------
# N1. malformed / non-object JSON bodies -> 400 (never 500 / 404)
# ---------------------------------------------------------------------------


class TestBodyValidation:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("endpoint", "raw"),
        [
            ("/v1/chat/completions", "{not json"),
            ("/v1/completions", '{"model": "gpt-4o", "prompt": '),
            ("/v1/embeddings", ""),  # empty body
        ],
    )
    async def test_malformed_json_body_returns_400_not_500(
        self, tmp_path, endpoint: str, raw: str
    ) -> None:
        """N1: malformed JSON must map to 400 invalid_request_error, never
        an unhandled JSONDecodeError (500)."""
        app = create_app(settings=_app_settings(tmp_path))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                endpoint,
                content=raw,
                headers={
                    "Authorization": "Bearer sk-test",
                    "content-type": "application/json",
                },
            )
        _assert_error_shape(resp, 400)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("endpoint", "raw"),
        [
            ("/v1/chat/completions", "[1, 2]"),
            ("/v1/completions", '"str"'),
            ("/v1/embeddings", "42"),
        ],
    )
    async def test_non_dict_json_body_returns_400_not_404(
        self, tmp_path, endpoint: str, raw: str
    ) -> None:
        """N1: a valid-JSON-but-non-object body must map to 400, not fall
        through to ``body.get("model", "")`` (404 ``unknown model: ``)."""
        app = create_app(settings=_app_settings(tmp_path))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                endpoint,
                content=raw,
                headers={
                    "Authorization": "Bearer sk-test",
                    "content-type": "application/json",
                },
            )
        _assert_error_shape(resp, 400)


# ---------------------------------------------------------------------------
# N2. embeddings: client-sent stream/stream_options never reach aembedding
# ---------------------------------------------------------------------------


class TestEmbeddingsStreamStrip:
    @pytest.mark.asyncio
    async def test_stream_never_reaches_aembedding(
        self, proxy: GatewayProxy, mocker
    ) -> None:
        """N2: stream/stream_options on an embeddings body must be dropped
        before the aembedding call (aembedding has no stream support →
        upstream 502 if forwarded)."""
        aembedding = mocker.patch("litellm.aembedding", new=AsyncMock())
        aembedding.return_value = SimpleNamespace(
            object="list",
            data=[{"embedding": [0.1, 0.2], "index": 0}],
            usage=SimpleNamespace(prompt_tokens=7, total_tokens=7),
            model="text-embedding-3-small",
        )

        body = {
            "model": "text-embedding-3-small",
            "input": ["hello world"],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        result = await proxy.forward("text-embedding-3-small", body)

        aembedding.assert_awaited_once()
        kwargs = aembedding.await_args.kwargs
        assert "stream" not in kwargs
        assert "stream_options" not in kwargs
        # the rest of the embeddings payload still forwards
        assert kwargs["model"] == "text-embedding-3-small"
        assert kwargs["input"] == ["hello world"]
        assert result.status_code == 200
