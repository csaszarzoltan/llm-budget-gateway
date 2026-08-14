"""Pre-development tests for the core gateway proxy module (P0-1).

Interface tests (imports, signatures, type hints, dataclass shape) PASS
immediately on the stub. Behavioral tests (request interception order, HTTP
mapping, streaming passthrough, scope resolution) FAIL during the RED phase
with NotImplementedError and become active once gateway_proxy.py / main.py /
config.py are implemented.

Normative interface: analysis/analysis-brief.md §4 P0-1.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import fields, is_dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from llm_budget_gateway.budget_enforcement import (
    BudgetExceededError,
    BudgetScope,
    RateLimitExceededError,
)
from llm_budget_gateway.config import Settings
from llm_budget_gateway.cost_tracking import (
    CostCalculator,
    CostStore,
    CostTracker,
    ModelPrice,
    PriceMap,
)
from llm_budget_gateway.gateway_proxy import (
    ApiKeyError,
    GatewayProxy,
    ProviderResponse,
    ProviderTimeoutError,
)
from llm_budget_gateway.main import create_app
from llm_budget_gateway.model_fallback import FallbackConfig, FallbackManager

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
    """GatewayProxy with Mock dependencies (enforcer/fallback stubs raise
    in __init__).
    """
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
        assert names == {
            "status_code",
            "body",
            "headers",
            "model",
            "usage",
            "latency_ms",
        }

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
        assert params == [
            "self",
            "settings",
            "cost_tracker",
            "budget_enforcer",
            "fallback_manager",
        ]

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
        assert params == ["self", "model", "body", "stream", "timeout"]
        assert str(sig.parameters["model"].annotation) == "str"
        assert str(sig.parameters["body"].annotation) == "dict"
        assert sig.parameters["stream"].default is False
        assert sig.parameters["timeout"].default is None

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
    async def test_handle_chat_completion_returns_provider_response(
        self, proxy: GatewayProxy
    ) -> None:
        result = await proxy.handle_chat_completion(
            {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            "sk_test_abc",
            {},
        )
        assert isinstance(result, ProviderResponse)

    @pytest.mark.asyncio
    async def test_handle_completion_returns_provider_response(
        self, proxy: GatewayProxy
    ) -> None:
        result = await proxy.handle_completion(
            {"model": "gpt-4o", "prompt": "hi"}, "sk_test_abc", {}
        )
        assert isinstance(result, ProviderResponse)

    @pytest.mark.asyncio
    async def test_handle_embeddings_returns_provider_response(
        self, proxy: GatewayProxy
    ) -> None:
        result = await proxy.handle_embeddings(
            {"model": "text-embedding-3-small", "input": "hi"}, "sk_test_abc", {}
        )
        assert isinstance(result, ProviderResponse)

    @pytest.mark.asyncio
    async def test_request_interception_order(
        self, proxy: GatewayProxy, mocker
    ) -> None:
        """auth -> scopes -> sync enforce -> forward -> cost record."""
        order: list[str] = []

        proxy.resolve_scopes = Mock(  # type: ignore[method-assign]
            side_effect=lambda *a: (
                order.append("scopes") or [BudgetScope("key", "key1")]
            )
        )

        enforcer = proxy._budget_enforcer
        enforcer.check_sync = Mock(side_effect=lambda *a: order.append("sync"))
        enforcer.check_hard = AsyncMock(side_effect=lambda *a: order.append("hard"))

        tracker = proxy._cost_tracker
        tracker.record = AsyncMock(side_effect=lambda *a: order.append("record"))

        proxy.forward = AsyncMock(  # type: ignore[method-assign]
            side_effect=lambda *a: (
                order.append("forward")
                or ProviderResponse(200, {}, {}, "gpt-4o", None, 5)
            )
        )

        await proxy.handle_chat_completion({"model": "gpt-4o"}, "sk_test_abc", {})

        core_order = [step for step in order if step != "hard"]
        assert core_order == ["scopes", "sync", "forward", "record"]

    @pytest.mark.asyncio
    async def test_unknown_key_maps_to_401(self, proxy: GatewayProxy) -> None:
        result = await proxy.handle_chat_completion(
            {"model": "gpt-4o"}, "sk_unknown", {}
        )
        assert isinstance(result, ProviderResponse)
        assert result.status_code == 401

    @pytest.mark.asyncio
    async def test_client_errors_do_not_leak_key_or_exception_text(
        self, settings: Settings
    ) -> None:
        """M4: client-visible errors are generic. The submitted api key must
        not be echoed back (401) and raw provider exception text must not be
        forwarded (502) — details stay in server-side logs."""
        secret = "sk_test_supersecretvalue123"
        proxy = GatewayProxy(
            settings=settings,
            cost_tracker=Mock(),
            budget_enforcer=Mock(),
            fallback_manager=Mock(),
        )
        result = await proxy.handle_chat_completion(
            {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            secret,
            {},
        )
        assert result.status_code == 401
        body_401 = json.dumps(result.body)
        assert secret not in body_401
        assert "unknown api key" not in body_401

        # provider failure path: a real manager routes through dispatch, and
        # the raw exception text (incl. the submitted key) must not leak.
        proxy2 = GatewayProxy(
            settings=settings,
            cost_tracker=Mock(),
            budget_enforcer=Mock(),
            fallback_manager=FallbackManager([]),
        )

        async def _boom(model: str, body: dict, stream: bool = False):
            raise RuntimeError(
                f"litellm AuthenticationError: {secret} invalid, "
                "base_url https://evil.example.com refused"
            )

        proxy2.forward = AsyncMock(side_effect=_boom)  # type: ignore[method-assign]
        result2 = await proxy2.handle_chat_completion(
            {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            "sk_test_abc",
            {},
        )
        assert result2.status_code == 502
        body_502 = json.dumps(result2.body)
        assert secret not in body_502
        assert "AuthenticationError" not in body_502
        assert "evil.example.com" not in body_502

    @pytest.mark.asyncio
    async def test_budget_exhausted_maps_to_412(
        self, proxy: GatewayProxy, mocker
    ) -> None:
        enforcer = proxy._budget_enforcer
        enforcer.check_hard = AsyncMock(
            side_effect=BudgetExceededError(
                BudgetScope("key", "key1"), spend=10.0, limit=5.0
            )
        )
        proxy.resolve_scopes = Mock(return_value=[BudgetScope("key", "key1")])  # type: ignore[method-assign]
        proxy.forward = AsyncMock(  # type: ignore[method-assign]
            return_value=ProviderResponse(200, {}, {}, "gpt-4o", None, 5)
        )
        result = await proxy.handle_chat_completion(
            {"model": "gpt-4o"}, "sk_test_abc", {}
        )
        assert result.status_code == 412

    @pytest.mark.asyncio
    async def test_rate_limited_maps_to_429(self, proxy: GatewayProxy) -> None:
        enforcer = proxy._budget_enforcer
        enforcer.check_sync = Mock(
            side_effect=RateLimitExceededError(
                BudgetScope("key", "key1"), limit_type="tpm", limit=1000
            )
        )
        result = await proxy.handle_chat_completion(
            {"model": "gpt-4o"}, "sk_test_abc", {}
        )
        assert result.status_code == 429

    @pytest.mark.asyncio
    async def test_provider_failure_after_fallback_exhaustion_maps_to_502(
        self, proxy: GatewayProxy, mocker
    ) -> None:
        proxy.forward = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("all fallbacks exhausted")
        )
        result = await proxy.handle_chat_completion(
            {"model": "gpt-4o"}, "sk_test_abc", {}
        )
        assert result.status_code == 502

    @pytest.mark.asyncio
    async def test_unknown_model_maps_to_404(self, proxy: GatewayProxy, mocker) -> None:
        """M1: unknown model -> 404 (never a 502 provider error)."""
        result = await proxy.handle_chat_completion(
            {"model": "definitely-not-a-model-xyz"}, "sk_test_abc", {}
        )
        assert result.status_code == 404

    @pytest.mark.asyncio
    async def test_429_on_primary_served_by_fallback_chain(
        self, settings: Settings, mocker
    ) -> None:
        """BLOCKER 1: _handle must route through FallbackManager.dispatch —
        a 429 on gpt-4o is served by the next chain model end-to-end."""
        manager = FallbackManager(
            [FallbackConfig(model="gpt-4o", chain=["gpt-3.5-turbo"])]
        )
        proxy = GatewayProxy(
            settings=settings,
            cost_tracker=Mock(),
            budget_enforcer=Mock(),
            fallback_manager=manager,
        )

        async def _forward(model: str, body: dict, stream: bool = False):
            if model == "gpt-4o":
                raise RateLimitExceededError(
                    BudgetScope(kind="key", key="key1"), "tpm", 60
                )
            return ProviderResponse(200, {}, {}, model, None, 5)

        proxy.forward = AsyncMock(side_effect=_forward)  # type: ignore[method-assign]
        result = await proxy.handle_chat_completion(
            {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            "sk_test_abc",
            {},
        )
        assert result.status_code == 200
        assert result.model == "gpt-3.5-turbo"
        # the primary was attempted first, then the fallback
        assert [c.args[0] for c in proxy.forward.call_args_list] == [
            "gpt-4o",
            "gpt-3.5-turbo",
        ]

    @pytest.mark.asyncio
    async def test_429_chain_exhaustion_maps_to_502(
        self, settings: Settings, mocker
    ) -> None:
        """BLOCKER 1: when every chain model 429s, the last error surfaces
        as a 502 provider error."""
        manager = FallbackManager(
            [FallbackConfig(model="gpt-4o", chain=["gpt-3.5-turbo"])]
        )
        proxy = GatewayProxy(
            settings=settings,
            cost_tracker=Mock(),
            budget_enforcer=Mock(),
            fallback_manager=manager,
        )

        async def _forward(model: str, body: dict, stream: bool = False):
            raise RateLimitExceededError(BudgetScope(kind="key", key="key1"), "tpm", 60)

        proxy.forward = AsyncMock(side_effect=_forward)  # type: ignore[method-assign]
        result = await proxy.handle_chat_completion(
            {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            "sk_test_abc",
            {},
        )
        assert result.status_code == 502
        assert [c.args[0] for c in proxy.forward.call_args_list] == [
            "gpt-4o",
            "gpt-3.5-turbo",
        ]


class TestForwardBehavior:
    @pytest.mark.asyncio
    async def test_forward_delegates_to_litellm(
        self, proxy: GatewayProxy, mocker
    ) -> None:
        litellm = mocker.patch("litellm.acompletion", new=AsyncMock())
        litellm.return_value = SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=5, total_tokens=15
            ),
            model="gpt-4o",
        )
        result = await proxy.forward("gpt-4o", {"model": "gpt-4o"})
        assert isinstance(result, ProviderResponse)
        assert result.usage is not None
        assert result.usage.prompt_tokens == 10

    @pytest.mark.asyncio
    async def test_forward_uses_param_model_over_body_model(
        self, proxy: GatewayProxy, mocker
    ) -> None:
        """BLOCKER 1: forward must honor the ``model`` param (the fallback
        candidate), not the body's model — otherwise dispatch would re-call
        the failing primary model."""
        litellm = mocker.patch("litellm.acompletion", new=AsyncMock())
        litellm.return_value = SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=5, total_tokens=15
            ),
            model="gpt-3.5-turbo",
        )
        body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
        result = await proxy.forward("gpt-3.5-turbo", body)
        kwargs = litellm.await_args.kwargs
        assert kwargs["model"] == "gpt-3.5-turbo"
        assert result.model == "gpt-3.5-turbo"

    @pytest.mark.asyncio
    async def test_forward_streaming_passthrough(
        self, proxy: GatewayProxy, mocker
    ) -> None:
        litellm = mocker.patch("litellm.acompletion", new=AsyncMock())
        litellm.return_value = SimpleNamespace(model="gpt-4o")
        result = await proxy.forward(
            "gpt-4o", {"model": "gpt-4o", "stream": True}, stream=True
        )
        assert isinstance(result, ProviderResponse)
        assert result.body is not None

    @pytest.mark.asyncio
    async def test_forward_strips_injection_fields(
        self, proxy: GatewayProxy, mocker
    ) -> None:
        """BLOCKER 2: api_key/base_url/headers in the client body must never
        reach litellm (provider auth/endpoint come from gateway settings)."""
        litellm = mocker.patch("litellm.acompletion", new=AsyncMock())
        litellm.return_value = SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=5, total_tokens=15
            ),
            model="gpt-4o",
        )
        body = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
            "api_key": "sk-client-injected",
            "api_base": "https://evil.example.com",
            "base_url": "https://evil.example.com",
            "headers": {"Authorization": "Bearer sk-client-injected"},
            "temperature": 0.2,
        }
        await proxy.forward("gpt-4o", body)
        kwargs = litellm.await_args.kwargs
        assert "api_key" not in kwargs
        assert "api_base" not in kwargs
        assert "base_url" not in kwargs
        assert "headers" not in kwargs
        assert kwargs["messages"] == [{"role": "user", "content": "hi"}]
        assert kwargs["temperature"] == 0.2

    @pytest.mark.asyncio
    async def test_forward_strips_injection_fields_for_completions(
        self, proxy: GatewayProxy, mocker
    ) -> None:
        """BLOCKER 2: completions-style body with api_key/base_url stripped."""
        litellm = mocker.patch("litellm.acompletion", new=AsyncMock())
        litellm.return_value = SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=5, total_tokens=15
            ),
            model="gpt-4o",
        )
        body = {
            "model": "gpt-4o",
            "prompt": "hello",
            "api_key": "sk-client-injected",
            "base_url": "https://evil.example.com",
        }
        await proxy.forward("gpt-4o", body)
        kwargs = litellm.await_args.kwargs
        assert "api_key" not in kwargs
        assert "base_url" not in kwargs
        assert kwargs["prompt"] == "hello"

    @pytest.mark.asyncio
    async def test_forward_streaming_aggregates_usage(
        self, proxy: GatewayProxy, mocker
    ) -> None:
        """BLOCKER 3: stream=true must aggregate chunk usage so the request
        is recorded at real cost, not $0 (budget bypass)."""

        async def _chunks():
            yield SimpleNamespace(
                model="gpt-4o",
                usage=SimpleNamespace(
                    prompt_tokens=10, completion_tokens=5, total_tokens=15
                ),
            )
            yield SimpleNamespace(
                model="gpt-4o",
                usage=SimpleNamespace(
                    prompt_tokens=0, completion_tokens=3, total_tokens=3
                ),
            )

        litellm = mocker.patch("litellm.acompletion", new=AsyncMock())
        litellm.return_value = _chunks()
        result = await proxy.forward(
            "gpt-4o", {"model": "gpt-4o", "stream": True}, stream=True
        )
        assert isinstance(result, ProviderResponse)
        assert result.usage is not None
        assert result.usage.prompt_tokens == 10
        assert result.usage.completion_tokens == 8
        assert result.usage.total_tokens == 18
        assert isinstance(result.body, list)

    @pytest.mark.asyncio
    async def test_forward_streaming_no_usage_chunks_stays_none(
        self, proxy: GatewayProxy, mocker
    ) -> None:
        """BLOCKER 3: streaming without any usage chunk -> usage None, no
        crash (provider did not emit usage)."""

        async def _chunks():
            yield SimpleNamespace(model="gpt-4o", choices=[])

        litellm = mocker.patch("litellm.acompletion", new=AsyncMock())
        litellm.return_value = _chunks()
        result = await proxy.forward(
            "gpt-4o", {"model": "gpt-4o", "stream": True}, stream=True
        )
        assert result.usage is None
        assert isinstance(result.body, list)
        assert result.model == "gpt-4o"


class TestStreamingCostRecording:
    @pytest.mark.asyncio
    async def test_stream_true_records_aggregated_usage(
        self, settings: Settings, tmp_path, mocker
    ) -> None:
        """BLOCKER 3: stream=true through handle_chat_completion must produce
        a cost record with non-zero (aggregated) usage so spend_since reflects
        streamed spend — no dollar hard-budget bypass."""
        store = CostStore(str(tmp_path / "ledger.db"))
        calculator = CostCalculator(
            PriceMap(
                overrides={
                    "gpt-4o": ModelPrice(
                        input_cost_per_million=1.0, output_cost_per_million=2.0
                    )
                }
            )
        )
        tracker = CostTracker(store=store, calculator=calculator)
        proxy = GatewayProxy(
            settings=settings,
            cost_tracker=tracker,
            budget_enforcer=Mock(),
            fallback_manager=FallbackManager([]),
        )

        async def _chunks():
            yield SimpleNamespace(
                model="gpt-4o",
                usage=SimpleNamespace(
                    prompt_tokens=10, completion_tokens=5, total_tokens=15
                ),
            )
            yield SimpleNamespace(
                model="gpt-4o",
                usage=SimpleNamespace(
                    prompt_tokens=0, completion_tokens=3, total_tokens=3
                ),
            )

        litellm = mocker.patch("litellm.acompletion", new=AsyncMock())
        litellm.return_value = _chunks()

        result = await proxy.handle_chat_completion(
            {
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
            "sk_test_abc",
            {},
        )
        assert result.status_code == 200
        assert result.usage is not None
        assert result.usage.total_tokens == 18
        # the aggregated usage was persisted: spend_since reflects streamed spend
        assert store.spend_since("key:key1", 0) > 0.0
        store.close()


class TestResolveScopesBehavior:
    def test_combines_key_user_team_global(
        self, settings: Settings, proxy: GatewayProxy
    ) -> None:
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

    @pytest.mark.asyncio
    async def test_product_route_timeout_falls_back_to_next_target(
        self, settings: Settings, mocker
    ) -> None:
        """A provider timeout on the primary target must fall back to the
        next target instead of failing the request (route-loop exception
        handling), and the timed-out target gets a cooldown."""
        store = Mock()
        store.published_route_by_name.return_value = {
            "name": "hermes-default",
            "targets": [
                {
                    "model": "@a/primary",
                    "priority": 10,
                    "timeout_seconds": 15,
                    "on_status_codes": [429, 500],
                },
                {
                    "model": "@b/fallback",
                    "priority": 20,
                    "timeout_seconds": 30,
                    "on_status_codes": [429, 500],
                },
            ],
        }
        tracker = Mock()
        tracker.model_in_cooldown.return_value = 0
        tracker.build_record.return_value = SimpleNamespace(total_cost=0.0)
        proxy = GatewayProxy(
            settings=settings,
            cost_tracker=tracker,
            budget_enforcer=Mock(),
            fallback_manager=FallbackManager([]),
        )
        proxy.attach_product_console(store)

        async def _forward(
            model: str,
            body: dict,
            stream: bool = False,
            timeout: float | None = None,
        ):
            if model == "@a/primary":
                raise ProviderTimeoutError(
                    "upstream provider timed out after 15s"
                )
            return ProviderResponse(200, {}, {}, model, None, 5)

        proxy.forward = AsyncMock(side_effect=_forward)  # type: ignore[method-assign]
        result = await proxy.handle_chat_completion(
            {
                "model": "hermes-default",
                "messages": [{"role": "user", "content": "hi"}],
            },
            "sk_test_abc",
            {},
        )
        assert result.status_code == 200
        assert result.model == "@b/fallback"
        # primary attempted, retried once (timeout retry), then fallback served it
        assert [c.args[0] for c in proxy.forward.call_args_list] == [
            "@a/primary",
            "@a/primary",  # timeout retry (target retries default 1)
            "@b/fallback",
        ]
        # the timed-out primary was marked for cooldown
        assert tracker.set_model_cooldown.called

    @pytest.mark.asyncio
    async def test_product_route_timeout_on_last_target_maps_to_502(
        self, settings: Settings, mocker
    ) -> None:
        """When the last target times out there is nowhere to fall back —
        the request surfaces as a 502 provider error."""
        store = Mock()
        store.published_route_by_name.return_value = {
            "name": "hermes-default",
            "targets": [
                {
                    "model": "@a/only",
                    "priority": 10,
                    "timeout_seconds": 15,
                    "on_status_codes": [429, 500],
                },
            ],
        }
        tracker2 = Mock()
        tracker2.model_in_cooldown.return_value = 0
        tracker2.build_record.return_value = SimpleNamespace(total_cost=0.0)
        proxy = GatewayProxy(
            settings=settings,
            cost_tracker=tracker2,
            budget_enforcer=Mock(),
            fallback_manager=FallbackManager([]),
        )
        proxy.attach_product_console(store)

        async def _forward(
            model: str,
            body: dict,
            stream: bool = False,
            timeout: float | None = None,
        ):
            raise ProviderTimeoutError("upstream provider timed out after 15s")

        proxy.forward = AsyncMock(side_effect=_forward)  # type: ignore[method-assign]
        result = await proxy.handle_chat_completion(
            {
                "model": "hermes-default",
                "messages": [{"role": "user", "content": "hi"}],
            },
            "sk_test_abc",
            {},
        )
        assert result.status_code == 502
        # primary retried once (default retries=1), then raised as last target
        assert len(proxy.forward.call_args_list) == 2

    @pytest.mark.asyncio
    async def test_product_route_400_context_error_falls_back(
        self, settings: Settings, mocker
    ) -> None:
        """A 400 whose body says 'maximum context length' must walk to the
        next target (some providers report overflow as 400, not 413/422),
        and the timed-out target must NOT be cooled down."""
        store = Mock()
        store.published_route_by_name.return_value = {
            "name": "hermes-default",
            "targets": [
                {
                    "model": "@a/small",
                    "priority": 10,
                    "timeout_seconds": 15,
                    "on_status_codes": [429, 500],
                },
                {
                    "model": "@b/big",
                    "priority": 20,
                    "timeout_seconds": 30,
                    "on_status_codes": [429, 500],
                },
            ],
        }
        tracker = Mock()
        tracker.model_in_cooldown.return_value = 0
        tracker.build_record.return_value = SimpleNamespace(total_cost=0.0)
        proxy = GatewayProxy(
            settings=settings,
            cost_tracker=tracker,
            budget_enforcer=Mock(),
            fallback_manager=FallbackManager([]),
        )
        proxy.attach_product_console(store)

        async def _forward(
            model: str,
            body: dict,
            stream: bool = False,
            timeout: float | None = None,
        ):
            if model == "@a/small":
                return ProviderResponse(
                    400,
                    {"error": {"message": "maximum context length exceeded"}},
                    {},
                    model,
                    None,
                    5,
                )
            return ProviderResponse(200, {}, {}, model, None, 5)

        proxy.forward = AsyncMock(side_effect=_forward)  # type: ignore[method-assign]
        result = await proxy.handle_chat_completion(
            {
                "model": "hermes-default",
                "messages": [{"role": "user", "content": "hi"}],
            },
            "sk_test_abc",
            {},
        )
        assert result.status_code == 200
        assert result.model == "@b/big"
        # overflow is NOT a provider outage -> no cooldown for the 400 target
        assert not tracker.set_model_cooldown.called

    @pytest.mark.asyncio
    async def test_product_route_400_plain_error_does_not_fallback(
        self, settings: Settings, mocker
    ) -> None:
        """A plain 400 (no context error) is a client error: surface it,
        do not walk the chain."""
        store = Mock()
        store.published_route_by_name.return_value = {
            "name": "hermes-default",
            "targets": [
                {
                    "model": "@a/small",
                    "priority": 10,
                    "timeout_seconds": 15,
                    "on_status_codes": [429, 500],
                },
                {
                    "model": "@b/big",
                    "priority": 20,
                    "timeout_seconds": 30,
                    "on_status_codes": [429, 500],
                },
            ],
        }
        tracker = Mock()
        tracker.model_in_cooldown.return_value = 0
        tracker.build_record.return_value = SimpleNamespace(total_cost=0.0)
        proxy = GatewayProxy(
            settings=settings,
            cost_tracker=tracker,
            budget_enforcer=Mock(),
            fallback_manager=FallbackManager([]),
        )
        proxy.attach_product_console(store)

        async def _forward(
            model: str,
            body: dict,
            stream: bool = False,
            timeout: float | None = None,
        ):
            return ProviderResponse(
                400, {"error": {"message": "invalid prompt format"}}, {}, model, None, 5
            )

        proxy.forward = AsyncMock(side_effect=_forward)  # type: ignore[method-assign]
        result = await proxy.handle_chat_completion(
            {
                "model": "hermes-default",
                "messages": [{"role": "user", "content": "hi"}],
            },
            "sk_test_abc",
            {},
        )
        assert result.status_code == 400
        # the fallback was never attempted for a plain client error
        assert [c.args[0] for c in proxy.forward.call_args_list] == ["@a/small"]

    @pytest.mark.asyncio
    async def test_product_route_transient_503_retries_once_then_falls_back(
        self, settings: Settings, mocker
    ) -> None:
        """A transient 503 on the primary must be retried once on the SAME
        model before falling back, so a rare blip does not degrade the
        response (opencode-go scenario)."""
        store = Mock()
        store.published_route_by_name.return_value = {
            "name": "hermes-default",
            "targets": [
                {
                    "model": "@a/primary",
                    "priority": 10,
                    "timeout_seconds": 15,
                    "on_status_codes": [429, 500, 503],
                },
                {
                    "model": "@b/fallback",
                    "priority": 20,
                    "timeout_seconds": 30,
                    "on_status_codes": [429, 500, 503],
                },
            ],
        }
        tracker = Mock()
        tracker.model_in_cooldown.return_value = 0
        tracker.build_record.return_value = SimpleNamespace(total_cost=0.0)
        proxy = GatewayProxy(
            settings=settings,
            cost_tracker=tracker,
            budget_enforcer=Mock(),
            fallback_manager=FallbackManager([]),
        )
        proxy.attach_product_console(store)

        calls = []

        async def _forward(
            model: str,
            body: dict,
            stream: bool = False,
            timeout: float | None = None,
        ):
            calls.append(model)
            if model == "@a/primary" and len(calls) == 1:
                return ProviderResponse(
                    503, {"error": {"message": "overloaded"}}, {}, model, None, 5
                )
            if model == "@a/primary":
                return ProviderResponse(200, {}, {}, model, None, 5)

            return ProviderResponse(200, {}, {}, model, None, 5)

        proxy.forward = AsyncMock(side_effect=_forward)  # type: ignore[method-assign]
        result = await proxy.handle_chat_completion(
            {
                "model": "hermes-default",
                "messages": [{"role": "user", "content": "hi"}],
            },
            "sk_test_abc",
            {},
        )
        assert result.status_code == 200
        # retried the SAME model once, never touched the fallback
        assert calls == ["@a/primary", "@a/primary"]
        # a success after retry must NOT set a cooldown
        assert not tracker.set_model_cooldown.called

    @pytest.mark.asyncio
    async def test_product_route_transient_503_short_cooldown(
        self, settings: Settings, mocker
    ) -> None:
        """When the transient 503 survives the retry and the chain is
        exhausted, the cooldown must be SHORT (<=60s), not the target's
        full cooldown (600s) — a transient overload is not a dead model."""
        store = Mock()
        store.published_route_by_name.return_value = {
            "name": "hermes-default",
            "targets": [
                {
                    "model": "@a/primary",
                    "priority": 10,
                    "timeout_seconds": 15,
                    "cooldown_seconds": 600,
                    "on_status_codes": [429, 500, 503],
                },
            ],
        }
        tracker = Mock()
        tracker.model_in_cooldown.return_value = 0
        tracker.build_record.return_value = SimpleNamespace(total_cost=0.0)
        proxy = GatewayProxy(
            settings=settings,
            cost_tracker=tracker,
            budget_enforcer=Mock(),
            fallback_manager=FallbackManager([]),
        )
        proxy.attach_product_console(store)

        async def _forward(
            model: str,
            body: dict,
            stream: bool = False,
            timeout: float | None = None,
        ):
            return ProviderResponse(
                503, {"error": {"message": "overloaded"}}, {}, model, None, 5
            )

        proxy.forward = AsyncMock(side_effect=_forward)  # type: ignore[method-assign]
        result = await proxy.handle_chat_completion(
            {
                "model": "hermes-default",
                "messages": [{"role": "user", "content": "hi"}],
            },
            "sk_test_abc",
            {},
        )
        # transient 503 with a single target → surfaced, but with SHORT cooldown
        assert result.status_code == 503
        assert tracker.set_model_cooldown.called
        cooldown_kwargs = tracker.set_model_cooldown.call_args
        assert cooldown_kwargs.args[2] <= 60


    @pytest.mark.asyncio
    async def test_retry_backoff_sleeps_between_attempts(
        self, settings: Settings, mocker
    ) -> None:
        """Retries of the same target must wait between attempts (exponential
        backoff) so a short provider blip gets a chance to recover instead of
        burning all retries into the same outage."""
        store = Mock()
        store.published_route_by_name.return_value = {
            "name": "hermes-default",
            "targets": [
                {
                    "model": "@a/primary",
                    "priority": 10,
                    "timeout_seconds": 15,
                    "retries": 3,
                    "on_status_codes": [429, 500, 503],
                },
            ],
        }
        tracker = Mock()
        tracker.model_in_cooldown.return_value = 0
        tracker.build_record.return_value = SimpleNamespace(total_cost=0.0)
        proxy = GatewayProxy(
            settings=settings,
            cost_tracker=tracker,
            budget_enforcer=Mock(),
            fallback_manager=FallbackManager([]),
        )
        proxy.attach_product_console(store)

        async def _forward(
            model: str,
            body: dict,
            stream: bool = False,
            timeout: float | None = None,
        ):
            return ProviderResponse(
                503, {"error": {"message": "overloaded"}}, {}, model, None, 5
            )

        proxy.forward = AsyncMock(side_effect=_forward)  # type: ignore[method-assign]
        sleeps: list[float] = []
        real_sleep = asyncio.sleep

        async def _sleep(secs: float) -> None:
            sleeps.append(secs)
            await real_sleep(0)  # don't actually wait in tests

        mocker.patch("llm_budget_gateway.gateway_proxy.asyncio.sleep", side_effect=_sleep)
        result = await proxy.handle_chat_completion(
            {
                "model": "hermes-default",
                "messages": [{"role": "user", "content": "hi"}],
            },
            "sk_test_abc",
            {},
        )
        # single target, 3 retries → 503 surfaces after all retries
        assert result.status_code == 503
        # backoff waits: 1s then 2s then 4s (base 1s, exponential, capped 10s)
        assert sleeps == [1.0, 2.0, 4.0]

    @pytest.mark.asyncio
    async def test_429_goes_straight_to_cooldown_no_retry(
        self, settings: Settings, mocker
    ) -> None:
        """A 429 rate limit (opencode-go scenario) must NOT be retried — the
        model goes straight to cooldown and the flow moves to the next
        candidate. Retrying a 429 only burns more requests into the same
        quota window and keeps the flow stuck on a model that cannot serve
        right now."""
        store = Mock()
        store.published_route_by_name.return_value = {
            "name": "hermes-default",
            "targets": [
                {
                    "model": "@a/primary",
                    "priority": 10,
                    "timeout_seconds": 15,
                    "retries": 2,
                    "on_status_codes": [429, 500, 503],
                },
                {
                    "model": "@b/fallback",
                    "priority": 20,
                    "timeout_seconds": 15,
                    "retries": 1,
                    "on_status_codes": [429, 500, 503],
                },
            ],
        }
        tracker = Mock()
        tracker.model_in_cooldown.return_value = 0
        tracker.build_record.return_value = SimpleNamespace(total_cost=0.0)
        proxy = GatewayProxy(
            settings=settings,
            cost_tracker=tracker,
            budget_enforcer=Mock(),
            fallback_manager=FallbackManager([]),
        )
        proxy.attach_product_console(store)

        calls = []

        async def _forward(
            model: str,
            body: dict,
            stream: bool = False,
            timeout: float | None = None,
        ):
            calls.append(model)
            return ProviderResponse(
                429, {"error": {"message": "rate limited"}}, {}, model, None, 5
            )

        proxy.forward = AsyncMock(side_effect=_forward)  # type: ignore[method-assign]
        sleeps: list[float] = []

        async def _sleep(secs: float) -> None:
            sleeps.append(secs)
            await asyncio.sleep(0)

        mocker.patch("llm_budget_gateway.gateway_proxy.asyncio.sleep", side_effect=_sleep)
        await proxy.handle_chat_completion(
            {
                "model": "hermes-default",
                "messages": [{"role": "user", "content": "hi"}],
            },
            "sk_test_abc",
            {},
        )
        # 429 → NO same-model retry: primary gets cooldown, chain moves to fallback
        assert calls == ["@a/primary", "@b/fallback"]
        # no backoff sleeps at all
        assert sleeps == []
        # primary got the transient (<=60s) cooldown
        cooldown_calls = tracker.set_model_cooldown.call_args_list
        assert cooldown_calls
        primary_cooldown = cooldown_calls[0][0]
        assert primary_cooldown[1] == "@a/primary"
        assert primary_cooldown[2] <= 60

    @pytest.mark.asyncio
    async def test_route_chain_budget_skips_mid_targets(
        self, settings: Settings, mocker
    ) -> None:
        """When the per-target timeouts would exceed the client's own timeout
        (Hermes gives up at ~60-90s), the chain budget must skip the middle
        candidates and land on the last one quickly instead of burning 2-3
        minutes on slow timeouts."""
        store = Mock()
        store.published_route_by_name.return_value = {
            "name": "hermes-default",
            "targets": [
                {
                    "model": "@a/primary",
                    "priority": 10,
                    "timeout_seconds": 120,
                    "on_status_codes": [429, 500],
                },
                {
                    "model": "@b/mid",
                    "priority": 20,
                    "timeout_seconds": 120,
                    "on_status_codes": [429, 500],
                },
                {
                    "model": "@c/last",
                    "priority": 30,
                    "timeout_seconds": 120,
                    "on_status_codes": [429, 500],
                },
            ],
        }
        tracker = Mock()
        tracker.model_in_cooldown.return_value = 0
        tracker.build_record.return_value = SimpleNamespace(total_cost=0.0)
        settings.route_timeout_budget = 0.1  # tiny budget for the test
        proxy = GatewayProxy(
            settings=settings,
            cost_tracker=tracker,
            budget_enforcer=Mock(),
            fallback_manager=FallbackManager([]),
        )
        proxy.attach_product_console(store)

        async def _forward(
            model: str,
            body: dict,
            stream: bool = False,
            timeout: float | None = None,
        ):
            # Both primary and mid burn the budget with slow timeouts.
            if model in ("@a/primary", "@b/mid"):
                await asyncio.sleep(0.2)
                raise ProviderTimeoutError("slow timeout")
            return ProviderResponse(200, {}, {}, model, None, 5)

        proxy.forward = AsyncMock(side_effect=_forward)  # type: ignore[method-assign]
        result = await proxy.handle_chat_completion(
            {
                "model": "hermes-default",
                "messages": [{"role": "user", "content": "hi"}],
            },
            "sk_test_abc",
            {},
        )
        # The mid target is skipped by the budget; the last one serves.
        assert result.status_code == 200
        assert result.model == "@c/last"
        # primary attempted, retried once (timeout retry), mid skipped by
        # budget, last served
        assert [c.args[0] for c in proxy.forward.call_args_list] == [
            "@a/primary",
            "@a/primary",
            "@c/last",
        ]

    @pytest.mark.asyncio
    async def test_product_route_timeout_honors_target_retries(
        self, settings: Settings, mocker
    ) -> None:
        """A target with retries=2 is retried twice on timeout before
        falling back to the next candidate."""
        store = Mock()
        store.published_route_by_name.return_value = {
            "name": "hermes-default",
            "targets": [
                {
                    "model": "@a/primary",
                    "priority": 10,
                    "timeout_seconds": 15,
                    "retries": 2,
                    "on_status_codes": [429, 500],
                },
                {
                    "model": "@b/fallback",
                    "priority": 20,
                    "timeout_seconds": 30,
                    "retries": 1,
                    "on_status_codes": [429, 500],
                },
            ],
        }
        tracker = Mock()
        tracker.model_in_cooldown.return_value = 0
        tracker.build_record.return_value = SimpleNamespace(total_cost=0.0)
        proxy = GatewayProxy(
            settings=settings,
            cost_tracker=tracker,
            budget_enforcer=Mock(),
            fallback_manager=FallbackManager([]),
        )
        proxy.attach_product_console(store)

        forward_calls: list[str] = []

        async def _forward(
            model: str,
            body: dict,
            stream: bool = False,
            timeout: float | None = None,
        ):
            forward_calls.append(model)
            if model == "@a/primary":
                raise ProviderTimeoutError(
                    "upstream provider timed out after 15s"
                )
            return ProviderResponse(200, {}, {}, model, None, 5)

        proxy.forward = AsyncMock(side_effect=_forward)  # type: ignore[method-assign]
        result = await proxy.handle_chat_completion(
            {
                "model": "hermes-default",
                "messages": [{"role": "user", "content": "hi"}],
            },
            "sk_test_abc",
            {},
        )
        assert result.status_code == 200
        assert result.model == "@b/fallback"
        # primary attempted + retried twice (retries=2), then fallback served
        assert forward_calls == [
            "@a/primary",
            "@a/primary",
            "@a/primary",
            "@b/fallback",
        ]
        assert tracker.set_model_cooldown.called
