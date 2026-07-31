"""Pre-development tests for the core gateway proxy module (P0-1).

Interface tests (imports, signatures, type hints, dataclass shape) PASS
immediately on the stub. Behavioral tests (request interception order, HTTP
mapping, streaming passthrough, scope resolution) FAIL during the RED phase
with NotImplementedError and become active once gateway_proxy.py / main.py /
config.py are implemented.

Normative interface: analysis/analysis-brief.md §4 P0-1.
"""

from __future__ import annotations

import inspect
from dataclasses import fields, is_dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from llm_budget_gateway.budget_enforcement import BudgetExceededError, BudgetScope, RateLimitExceededError
from llm_budget_gateway.config import Settings
from llm_budget_gateway.gateway_proxy import ApiKeyError, GatewayProxy, ProviderResponse
from llm_budget_gateway.main import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings() -> Settings:
    return Settings(
        virtual_keys={"sk_test_abc": "key1"},
        user_header_mappings={"X-User-Id": "user", "X-Team-Id": "team"},
    )


@pytest.fixture
def proxy(settings: Settings) -> GatewayProxy:
    """GatewayProxy with Mock dependencies (enforcer/fallback stubs raise in __init__)."""
    return GatewayProxy(
        settings=settings,
        cost_tracker=Mock(),
        budget_enforcer=Mock(),
        fallback_manager=Mock(),
    )


# ---------------------------------------------------------------------------
# Interface tests — pass immediately on the stub
# ---------------------------------------------------------------------------


class TestProviderResponseInterface:
    def test_is_dataclass(self) -> None:
        assert is_dataclass(ProviderResponse)

    def test_fields(self) -> None:
        names = {f.name for f in fields(ProviderResponse)}
        assert names == {"status_code", "body", "headers", "model", "usage", "latency_ms"}

    def test_field_types(self) -> None:
        annotations = ProviderResponse.__annotations__
        assert "int" in str(annotations["status_code"])
        assert "usage" in annotations
        assert "latency_ms" in annotations

    def test_constructible(self) -> None:
        resp = ProviderResponse(
            status_code=200,
            body={"choices": []},
            headers={"content-type": "application/json"},
            model="gpt-4o",
            usage=None,
            latency_ms=42,
        )
        assert resp.status_code == 200
        assert resp.model == "gpt-4o"
        assert resp.latency_ms == 42
        assert resp.usage is None


class TestGatewayProxyInterface:
    def test_class_exists(self) -> None:
        assert isinstance(GatewayProxy, type)

    def test_init_signature(self) -> None:
        sig = inspect.signature(GatewayProxy.__init__)
        params = list(sig.parameters)
        assert params == ["self", "settings", "cost_tracker", "budget_enforcer", "fallback_manager"]

    def test_constructible_with_dependencies(self, proxy: GatewayProxy) -> None:
        assert proxy._settings is not None
        assert proxy._cost_tracker is not None
        assert proxy._budget_enforcer is not None
        assert proxy._fallback_manager is not None

    def test_handle_chat_completion_signature(self) -> None:
        sig = inspect.signature(GatewayProxy.handle_chat_completion)
        params = list(sig.parameters)
        assert params == ["self", "body", "api_key", "headers"]
        assert str(sig.parameters["body"].annotation) == "dict"
        assert str(sig.parameters["api_key"].annotation) == "str"
        assert str(sig.parameters["headers"].annotation) == "dict"

    def test_handle_completion_signature(self) -> None:
        sig = inspect.signature(GatewayProxy.handle_completion)
        assert list(sig.parameters) == ["self", "body", "api_key", "headers"]

    def test_handle_embeddings_signature(self) -> None:
        sig = inspect.signature(GatewayProxy.handle_embeddings)
        assert list(sig.parameters) == ["self", "body", "api_key", "headers"]

    def test_forward_signature(self) -> None:
        sig = inspect.signature(GatewayProxy.forward)
        params = list(sig.parameters)
        assert params == ["self", "model", "body", "stream"]
        assert str(sig.parameters["model"].annotation) == "str"
        assert str(sig.parameters["body"].annotation) == "dict"
        assert sig.parameters["stream"].default is False

    def test_resolve_scopes_signature(self) -> None:
        sig = inspect.signature(GatewayProxy.resolve_scopes)
        assert list(sig.parameters) == ["self", "api_key", "headers"]
        assert "list" in str(sig.return_annotation)

    def test_handle_methods_are_async(self) -> None:
        assert inspect.iscoroutinefunction(GatewayProxy.handle_chat_completion)
        assert inspect.iscoroutinefunction(GatewayProxy.handle_completion)
        assert inspect.iscoroutinefunction(GatewayProxy.handle_embeddings)
        assert inspect.iscoroutinefunction(GatewayProxy.forward)

    def test_resolve_scopes_is_sync(self) -> None:
        assert not inspect.iscoroutinefunction(GatewayProxy.resolve_scopes)


class TestApiKeyErrorInterface:
    def test_subclasses_exception(self) -> None:
        assert issubclass(ApiKeyError, Exception)

    def test_instantiable(self) -> None:
        err = ApiKeyError("unknown api key")
        assert isinstance(err, ApiKeyError)


class TestSettingsInterface:
    def test_fields_exist(self) -> None:
        for field in (
            "database_url",
            "budget_config_path",
            "virtual_keys",
            "user_header_mappings",
            "pricing_overrides",
            "fallback_configs",
        ):
            assert field in Settings.model_fields

    def test_defaults(self) -> None:
        s = Settings()
        assert s.database_url == "sqlite:///./gateway.db"
        assert s.budget_config_path == "budgets.yaml"
        assert s.virtual_keys == {}
        assert s.user_header_mappings == {}
        assert s.pricing_overrides == {}
        assert s.fallback_configs == []

    def test_env_prefix(self) -> None:
        assert Settings.model_config.get("env_prefix") == "GATEWAY_"

    def test_override_via_kwargs(self) -> None:
        s = Settings(virtual_keys={"k": "v"})
        assert s.virtual_keys == {"k": "v"}


class TestCreateAppInterface:
    def test_is_function(self) -> None:
        assert callable(create_app)

    def test_signature(self) -> None:
        sig = inspect.signature(create_app)
        params = list(sig.parameters)
        assert params == ["settings"]
        assert sig.parameters["settings"].default is None
        assert "FastAPI" in str(sig.return_annotation)


# ---------------------------------------------------------------------------
# Behavioral tests — FAIL with NotImplementedError during RED phase
# ---------------------------------------------------------------------------


class TestRequestHandlingBehavior:
    @pytest.mark.asyncio
    async def test_handle_chat_completion_returns_provider_response(self, proxy: GatewayProxy) -> None:
        result = await proxy.handle_chat_completion(
            {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            "sk_test_abc",
            {},
        )
        assert isinstance(result, ProviderResponse)

    @pytest.mark.asyncio
    async def test_handle_completion_returns_provider_response(self, proxy: GatewayProxy) -> None:
        result = await proxy.handle_completion({"model": "gpt-4o", "prompt": "hi"}, "sk_test_abc", {})
        assert isinstance(result, ProviderResponse)

    @pytest.mark.asyncio
    async def test_handle_embeddings_returns_provider_response(self, proxy: GatewayProxy) -> None:
        result = await proxy.handle_embeddings({"model": "text-embedding-3-small", "input": "hi"}, "sk_test_abc", {})
        assert isinstance(result, ProviderResponse)

    @pytest.mark.asyncio
    async def test_request_interception_order(self, proxy: GatewayProxy, mocker) -> None:
        """auth -> scopes -> sync enforce -> forward -> cost record."""
        order: list[str] = []

        proxy.resolve_scopes = AsyncMock(  # type: ignore[method-assign]
            side_effect=lambda *a: order.append("scopes") or [BudgetScope("key", "key1")]
        )

        enforcer = proxy._budget_enforcer
        enforcer.check_sync = Mock(side_effect=lambda *a: order.append("sync"))
        enforcer.check_hard = AsyncMock(side_effect=lambda *a: order.append("hard"))

        tracker = proxy._cost_tracker
        tracker.record = AsyncMock(side_effect=lambda *a: order.append("record"))

        proxy.forward = AsyncMock(  # type: ignore[method-assign]
            side_effect=lambda *a: order.append("forward")
            or ProviderResponse(200, {}, {}, "gpt-4o", None, 5)
        )

        await proxy.handle_chat_completion({"model": "gpt-4o"}, "sk_test_abc", {})

        core_order = [step for step in order if step != "hard"]
        assert core_order == ["scopes", "sync", "forward", "record"]

    @pytest.mark.asyncio
    async def test_unknown_key_maps_to_401(self, proxy: GatewayProxy) -> None:
        result = await proxy.handle_chat_completion({"model": "gpt-4o"}, "sk_unknown", {})
        assert isinstance(result, ProviderResponse)
        assert result.status_code == 401

    @pytest.mark.asyncio
    async def test_budget_exhausted_maps_to_412(self, proxy: GatewayProxy, mocker) -> None:
        enforcer = proxy._budget_enforcer
        enforcer.check_hard = AsyncMock(
            side_effect=BudgetExceededError(BudgetScope("key", "key1"), spend=10.0, limit=5.0)
        )
        proxy.resolve_scopes = AsyncMock(return_value=[BudgetScope("key", "key1")])  # type: ignore[method-assign]
        proxy.forward = AsyncMock(  # type: ignore[method-assign]
            return_value=ProviderResponse(200, {}, {}, "gpt-4o", None, 5)
        )
        result = await proxy.handle_chat_completion({"model": "gpt-4o"}, "sk_test_abc", {})
        assert result.status_code == 412

    @pytest.mark.asyncio
    async def test_rate_limited_maps_to_429(self, proxy: GatewayProxy) -> None:
        enforcer = proxy._budget_enforcer
        enforcer.check_sync = Mock(
            side_effect=RateLimitExceededError(BudgetScope("key", "key1"), limit_type="tpm", limit=1000)
        )
        result = await proxy.handle_chat_completion({"model": "gpt-4o"}, "sk_test_abc", {})
        assert result.status_code == 429

    @pytest.mark.asyncio
    async def test_provider_failure_after_fallback_exhaustion_maps_to_502(
        self, proxy: GatewayProxy, mocker
    ) -> None:
        proxy.forward = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("all fallbacks exhausted")
        )
        result = await proxy.handle_chat_completion({"model": "gpt-4o"}, "sk_test_abc", {})
        assert result.status_code == 502


class TestForwardBehavior:
    @pytest.mark.asyncio
    async def test_forward_delegates_to_litellm(self, proxy: GatewayProxy, mocker) -> None:
        litellm = mocker.patch("litellm.acompletion", new=AsyncMock())
        litellm.return_value = SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            model="gpt-4o",
        )
        result = await proxy.forward("gpt-4o", {"model": "gpt-4o"})
        assert isinstance(result, ProviderResponse)
        assert result.usage is not None
        assert result.usage.prompt_tokens == 10

    @pytest.mark.asyncio
    async def test_forward_streaming_passthrough(self, proxy: GatewayProxy, mocker) -> None:
        litellm = mocker.patch("litellm.acompletion", new=AsyncMock())
        litellm.return_value = SimpleNamespace(model="gpt-4o")
        result = await proxy.forward("gpt-4o", {"model": "gpt-4o", "stream": True}, stream=True)
        assert isinstance(result, ProviderResponse)
        assert result.body is not None


class TestResolveScopesBehavior:
    def test_combines_key_user_team_global(self, settings: Settings, proxy: GatewayProxy) -> None:
        scopes = proxy.resolve_scopes(
            "sk_test_abc",
            {"X-User-Id": "42", "X-Team-Id": "eng"},
        )
        keys = {s.scope_key() for s in scopes}
        assert "key:key1" in keys
        assert "user:42" in keys
        assert "team:eng" in keys
        assert "global:default" in keys

    def test_unknown_key_raises_api_key_error(self, proxy: GatewayProxy) -> None:
        with pytest.raises(ApiKeyError):
            proxy.resolve_scopes("sk_unknown", {})


class TestCreateAppBehavior:
    def test_create_app_returns_fastapi_app(self) -> None:
        app = create_app()
        routes = {getattr(r, "path", None) for r in app.routes}
        assert "/v1/chat/completions" in routes
        assert "/v1/completions" in routes
        assert "/v1/embeddings" in routes
        assert "/v1/models" in routes
        assert "/health" in routes

    def test_create_app_accepts_settings(self, settings: Settings) -> None:
        app = create_app(settings=settings)
        assert app is not None
