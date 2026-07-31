"""Pre-development tests for model_fallback.py (P0-4, spec analysis-brief.md §4).

Two categories:
- TestModelFallbackInterface: imports, class/function existence, signatures,
  type hints, dataclass fields/defaults. Must PASS immediately on the stub.
- TestModelFallbackBehavior: expected behavior (chain traversal, cooldowns,
  error classification, context pre-checks, dispatch). Must FAIL with
  NotImplementedError until the developer implements the module.

Contracts encoded here (normative for the developer):
- ``chain_for`` returns [model] + configured chain, filtered by cooldown and
  ``disable`` (per-call or per-config).
- ``classify_error`` maps 429/RateLimitExceededError -> "rate_limit",
  TimeoutError/timeout markers -> "timeout", 5xx -> "server_error",
  content-filter markers -> "content_policy", context-length markers ->
  "context_window", everything else -> "unknown".
- ``should_fallback`` is true only when error_class is in config.on;
  content_policy falls back only when explicitly configured (default off).
- ``dispatch`` tries the chain in order, marks failed models for cooldown,
  honors disable_fallbacks, skips context-unsafe models pre-call (no provider
  call for skipped models), re-raises the last error on exhaustion.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, fields, is_dataclass

import pytest

from llm_budget_gateway.budget_enforcement import (
    BudgetScope,
    InMemoryCounterStore,
    RateLimitExceededError,
)
from llm_budget_gateway.model_fallback import FallbackConfig, FallbackManager

# Largest body used by context-skip tests: 70k chars ≈ 17.5k tokens by the
# chars/4 heuristic — over gpt-3.5-turbo's ~16k context budget, under
# gpt-4o's 128k. Kept as a module constant so the relationship is obvious.
BIG_BODY: dict = {"messages": [{"content": "word " * 14_000}]}


@dataclass
class ProviderResponse:
    """Test stand-in mirroring gateway_proxy.ProviderResponse (sibling stub)."""

    status_code: int
    body: dict
    headers: dict
    model: str
    usage: object | None = None
    latency_ms: int = 0


class FakeProxy:
    """GatewayProxy stand-in that records forward() calls and can fail per model."""

    def __init__(self, failures: dict[str, Exception] | None = None) -> None:
        self.failures = failures or {}
        self.calls: list[str] = []

    async def forward(
        self, model: str, body: dict, stream: bool = False
    ) -> ProviderResponse:
        self.calls.append(model)
        if model in self.failures:
            raise self.failures[model]
        return ProviderResponse(
            status_code=200, body={}, headers={}, model=model, latency_ms=10
        )


def make_chain_config(
    model: str = "gpt-4o", chain: list[str] | None = None, **kw
) -> FallbackConfig:
    return FallbackConfig(
        model=model, chain=chain or ["gpt-3.5-turbo", "claude-3-5-haiku"], **kw
    )


@pytest.fixture
def manager() -> FallbackManager:
    return FallbackManager(
        [
            make_chain_config("gpt-4o", ["gpt-3.5-turbo", "claude-3-5-haiku"]),
            make_chain_config("gpt-3.5-turbo", ["claude-3-5-haiku"]),
        ]
    )


@pytest.fixture
def body() -> dict:
    return {"model": "gpt-4o", "messages": [{"role": "user", "content": "hello world"}]}


class TestModelFallbackInterface:
    def test_module_importable(self) -> None:
        import llm_budget_gateway.model_fallback as mf

        assert mf is not None

    def test_public_api_exists(self) -> None:
        assert FallbackConfig is not None
        assert FallbackManager is not None

    def test_fallback_config_is_dataclass(self) -> None:
        assert is_dataclass(FallbackConfig)

    def test_fallback_config_field_order(self) -> None:
        assert [f.name for f in fields(FallbackConfig)] == [
            "model",
            "chain",
            "on",
            "cooldown_seconds",
            "disable",
        ]

    def test_fallback_config_defaults(self) -> None:
        cfg = FallbackConfig(model="gpt-4o", chain=["gpt-3.5-turbo"])
        assert cfg.on == ["rate_limit", "server_error", "timeout"]
        assert cfg.cooldown_seconds == 60
        assert cfg.disable is False

    def test_fallback_config_on_default_is_not_shared(self) -> None:
        a = FallbackConfig(model="a", chain=[])
        b = FallbackConfig(model="b", chain=[])
        a.on.append("content_policy")
        assert b.on == ["rate_limit", "server_error", "timeout"]

    def test_fallback_manager_constructible(self) -> None:
        assert FallbackManager(configs=[]) is not None

    def test_fallback_manager_constructible_with_counter_store(self) -> None:
        assert (
            FallbackManager(configs=[], counter_store=InMemoryCounterStore())
            is not None
        )

    def test_fallback_manager_init_signature(self) -> None:
        sig = inspect.signature(FallbackManager.__init__)
        assert list(sig.parameters) == ["self", "configs", "counter_store"]
        assert sig.parameters["counter_store"].default is None

    def test_fallback_manager_methods_exist(self) -> None:
        for name in (
            "config_for",
            "chain_for",
            "classify_error",
            "should_fallback",
            "mark_failed",
            "in_cooldown",
            "estimate_tokens",
            "context_safe",
            "dispatch",
        ):
            assert callable(getattr(FallbackManager, name))

    def test_config_for_signature(self) -> None:
        sig = inspect.signature(FallbackManager.config_for)
        assert list(sig.parameters) == ["self", "model"]
        assert "FallbackConfig" in str(sig.return_annotation)

    def test_chain_for_signature(self) -> None:
        sig = inspect.signature(FallbackManager.chain_for)
        assert list(sig.parameters) == ["self", "model", "disable"]
        assert sig.parameters["disable"].default is False

    def test_classify_error_signature(self) -> None:
        sig = inspect.signature(FallbackManager.classify_error)
        assert list(sig.parameters) == ["self", "exc", "status_code"]
        assert sig.parameters["status_code"].default is None

    def test_should_fallback_signature(self) -> None:
        sig = inspect.signature(FallbackManager.should_fallback)
        assert list(sig.parameters) == ["self", "config", "error_class"]

    def test_dispatch_signature(self) -> None:
        sig = inspect.signature(FallbackManager.dispatch)
        assert list(sig.parameters) == [
            "self",
            "proxy",
            "model",
            "body",
            "api_key",
            "headers",
            "disable_fallbacks",
        ]
        assert sig.parameters["disable_fallbacks"].default is False

    def test_dispatch_is_async(self) -> None:
        assert inspect.iscoroutinefunction(FallbackManager.dispatch)

    def test_dispatch_return_annotation_references_provider_response(self) -> None:
        sig = inspect.signature(FallbackManager.dispatch)
        assert "ProviderResponse" in str(sig.return_annotation)


class TestConfigForBehavior:
    def test_config_for_returns_matching_config(self, manager: FallbackManager) -> None:
        cfg = manager.config_for("gpt-4o")
        assert cfg is not None
        assert cfg.model == "gpt-4o"
        assert cfg.chain == ["gpt-3.5-turbo", "claude-3-5-haiku"]

    def test_config_for_unknown_model_returns_none(
        self, manager: FallbackManager
    ) -> None:
        assert manager.config_for("nope") is None


class TestChainForBehavior:
    def test_chain_for_returns_model_then_chain(self, manager: FallbackManager) -> None:
        assert manager.chain_for("gpt-4o") == [
            "gpt-4o",
            "gpt-3.5-turbo",
            "claude-3-5-haiku",
        ]

    def test_chain_for_unknown_model_returns_just_model(
        self, manager: FallbackManager
    ) -> None:
        assert manager.chain_for("unknown-model") == ["unknown-model"]

    def test_chain_for_disable_true_returns_just_model(
        self, manager: FallbackManager
    ) -> None:
        assert manager.chain_for("gpt-4o", disable=True) == ["gpt-4o"]

    def test_chain_for_skips_disabled_config(self) -> None:
        mgr = FallbackManager([make_chain_config("gpt-4o", disable=True)])
        assert mgr.chain_for("gpt-4o") == ["gpt-4o"]

    def test_chain_for_removes_model_in_cooldown(
        self, manager: FallbackManager
    ) -> None:
        manager.mark_failed("gpt-3.5-turbo")
        assert manager.chain_for("gpt-4o") == ["gpt-4o", "claude-3-5-haiku"]

    def test_chain_for_returns_model_after_cooldown_expires(self) -> None:
        # cooldown_seconds=0 -> mark_failed has no lasting effect.
        mgr = FallbackManager([make_chain_config("gpt-4o", cooldown_seconds=0)])
        mgr.mark_failed("gpt-4o")
        assert mgr.chain_for("gpt-4o") == [
            "gpt-4o",
            "gpt-3.5-turbo",
            "claude-3-5-haiku",
        ]


class TestClassifyErrorBehavior:
    @pytest.mark.parametrize(
        ("exc", "status_code", "expected"),
        [
            (
                RateLimitExceededError(BudgetScope(kind="key", key="k"), "tpm", 60),
                None,
                "rate_limit",
            ),
            (ValueError("rate limit reached"), 429, "rate_limit"),
            (TimeoutError("upstream timed out"), None, "timeout"),
            (ValueError("upstream boom"), 500, "server_error"),
            (ValueError("bad gateway"), 502, "server_error"),
            (ValueError("service unavailable"), 503, "server_error"),
            (
                ValueError(
                    "response was filtered due to the prompt triggering a "
                    "content management policy"
                ),
                None,
                "content_policy",
            ),
            (
                ValueError("this model's maximum context length is 128000 tokens"),
                None,
                "context_window",
            ),
            (ValueError("mystery error"), 400, "unknown"),
        ],
    )
    def test_classify_error_mapping(
        self, exc: Exception, status_code: int | None, expected: str
    ) -> None:
        assert FallbackManager([]).classify_error(exc, status_code) == expected


class TestShouldFallbackBehavior:
    def test_true_for_configured_classes(self) -> None:
        cfg = FallbackConfig(
            model="gpt-4o", chain=[], on=["rate_limit", "server_error", "timeout"]
        )
        mgr = FallbackManager([cfg])
        assert mgr.should_fallback(cfg, "rate_limit") is True
        assert mgr.should_fallback(cfg, "server_error") is True
        assert mgr.should_fallback(cfg, "timeout") is True

    def test_false_for_content_policy_by_default(self) -> None:
        cfg = FallbackConfig(
            model="gpt-4o", chain=[], on=["rate_limit", "server_error", "timeout"]
        )
        assert FallbackManager([cfg]).should_fallback(cfg, "content_policy") is False

    def test_true_for_content_policy_when_configured(self) -> None:
        cfg = FallbackConfig(
            model="gpt-4o", chain=[], on=["rate_limit", "content_policy"]
        )
        assert FallbackManager([cfg]).should_fallback(cfg, "content_policy") is True

    def test_false_for_unknown_class(self) -> None:
        cfg = FallbackConfig(model="gpt-4o", chain=[])
        assert FallbackManager([cfg]).should_fallback(cfg, "unknown") is False


class TestCooldownBehavior:
    def test_in_cooldown_false_initially(self, manager: FallbackManager) -> None:
        assert manager.in_cooldown("gpt-4o") is False

    def test_mark_failed_starts_cooldown(self, manager: FallbackManager) -> None:
        manager.mark_failed("gpt-4o")
        assert manager.in_cooldown("gpt-4o") is True

    def test_zero_cooldown_never_in_cooldown(self) -> None:
        mgr = FallbackManager([make_chain_config("gpt-4o", cooldown_seconds=0)])
        mgr.mark_failed("gpt-4o")
        assert mgr.in_cooldown("gpt-4o") is False


class TestContextEstimationBehavior:
    def test_estimate_tokens_returns_positive_int(self, body: dict) -> None:
        n = FallbackManager([]).estimate_tokens(body)
        assert isinstance(n, int)
        assert n > 0

    def test_estimate_tokens_grows_with_content_size(self) -> None:
        small = FallbackManager([]).estimate_tokens({"messages": [{"content": "hi"}]})
        big = FallbackManager([]).estimate_tokens(
            {"messages": [{"content": "word " * 1000}]}
        )
        assert big > small

    def test_context_safe_small_body(self, body: dict) -> None:
        assert FallbackManager([]).context_safe("gpt-4o", body) is True

    def test_context_safe_false_for_huge_body(self) -> None:
        huge: dict = {
            "messages": [{"content": "x" * 1_000_000}]
        }  # ~250k tokens by chars/4
        assert FallbackManager([]).context_safe("gpt-4o", huge) is False


class TestDispatchBehavior:
    @pytest.mark.asyncio
    async def test_dispatch_success_first_model(
        self, manager: FallbackManager, body: dict
    ) -> None:
        proxy = FakeProxy()
        resp = await manager.dispatch(proxy, "gpt-4o", body, "sk_test", {})
        assert resp.model == "gpt-4o"
        assert proxy.calls == ["gpt-4o"]

    @pytest.mark.asyncio
    async def test_dispatch_falls_back_to_next_chain_model(
        self, manager: FallbackManager, body: dict
    ) -> None:
        exc = RateLimitExceededError(BudgetScope(kind="key", key="k"), "tpm", 60)
        proxy = FakeProxy(failures={"gpt-4o": exc})
        resp = await manager.dispatch(proxy, "gpt-4o", body, "sk_test", {})
        assert resp.model == "gpt-3.5-turbo"
        assert proxy.calls == ["gpt-4o", "gpt-3.5-turbo"]

    @pytest.mark.asyncio
    async def test_dispatch_disable_fallbacks_reraises_original(
        self, manager: FallbackManager, body: dict
    ) -> None:
        exc = RateLimitExceededError(BudgetScope(kind="key", key="k"), "tpm", 60)
        proxy = FakeProxy(failures={"gpt-4o": exc})
        with pytest.raises(RateLimitExceededError) as caught:
            await manager.dispatch(
                proxy, "gpt-4o", body, "sk_test", {}, disable_fallbacks=True
            )
        assert caught.value is exc
        assert proxy.calls == ["gpt-4o"]

    @pytest.mark.asyncio
    async def test_dispatch_chain_exhaustion_reraises_last(
        self, manager: FallbackManager, body: dict
    ) -> None:
        last = ValueError("provider exploded")
        proxy = FakeProxy(
            failures={
                "gpt-4o": RateLimitExceededError(
                    BudgetScope(kind="key", key="k"), "tpm", 60
                ),
                "gpt-3.5-turbo": TimeoutError("slow"),
                "claude-3-5-haiku": last,
            }
        )
        with pytest.raises(ValueError) as caught:
            await manager.dispatch(proxy, "gpt-4o", body, "sk_test", {})
        assert caught.value is last
        assert proxy.calls == ["gpt-4o", "gpt-3.5-turbo", "claude-3-5-haiku"]

    @pytest.mark.asyncio
    async def test_dispatch_content_policy_no_fallback_by_default(
        self, manager: FallbackManager, body: dict
    ) -> None:
        exc = ValueError(
            "response was filtered due to the prompt triggering a "
            "content management policy"
        )
        proxy = FakeProxy(failures={"gpt-4o": exc})
        with pytest.raises(ValueError) as caught:
            await manager.dispatch(proxy, "gpt-4o", body, "sk_test", {})
        assert caught.value is exc
        assert proxy.calls == ["gpt-4o"]

    @pytest.mark.asyncio
    async def test_dispatch_skips_context_unsafe_model_pre_call(self) -> None:
        # gpt-3.5-turbo (~16k context) cannot fit the 70k-char body (~17.5k
        # tokens by chars/4) but gpt-4o (128k) can -> dispatch must skip
        # gpt-3.5-turbo BEFORE calling the provider (assert via call log).
        mgr = FallbackManager([make_chain_config("gpt-3.5-turbo", ["gpt-4o"])])
        assert mgr.context_safe("gpt-3.5-turbo", BIG_BODY) is False
        proxy = FakeProxy()
        resp = await mgr.dispatch(proxy, "gpt-3.5-turbo", BIG_BODY, "sk_test", {})
        assert resp.model == "gpt-4o"
        assert proxy.calls == ["gpt-4o"]
