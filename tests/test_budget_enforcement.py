"""Pre-development tests for budget_enforcement.py (P0-3, spec analysis-brief.md §4).

Two categories:
- TestBudgetEnforcementInterface: imports, class/function existence, signatures,
  type hints, dataclass fields/defaults. Must PASS immediately on the stub.
- TestBudgetEnforcementBehavior: expected behavior (windowed counters, sync
  TPM/RPM ceilings, soft/hard limits, composite scopes, YAML loading). Must
  FAIL with NotImplementedError until the developer implements the module.

Contracts encoded here (normative for the developer):
- ``check_sync`` increments the TPM counter by ``est_input_tokens`` and the RPM
  counter by 1 per call; raises RateLimitExceededError when the ceiling is hit.
- TPM/RPM windows reset automatically when the window bucket elapses; time is
  injected via ``now_fn`` (tests use a mutable clock).
- ``check_hard`` raises BudgetExceededError if spend_since >= hard_limit for
  ANY provided scope (composite enforcement: key + team + user + global).
- Soft limits are non-blocking: ``soft_exceeded`` reports, never raises.
- ``load_budget_configs`` parses the shape of examples/budgets.example.yaml and
  raises ValueError on malformed YAML or unknown scope kind.
"""

from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError, fields, is_dataclass
from types import SimpleNamespace

import pytest

from llm_budget_gateway.budget_enforcement import (
    BudgetConfig,
    BudgetEnforcer,
    BudgetExceededError,
    BudgetScope,
    CounterStore,
    InMemoryCounterStore,
    RateLimitExceededError,
    load_budget_configs,
)

# 2025-07-15T00:00:00Z — a fixed "now" so monthly-window math is deterministic.
NOW = 1_752_537_600


class MutableClock:
    """Injected ``now_fn`` whose value the test can advance."""

    def __init__(self, value: int = NOW) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


class FakeCostTracker:
    """Minimal CostTracker stand-in (cost_tracking.py is stubbed separately)."""

    def __init__(self, spend: dict[str, float] | None = None) -> None:
        self.spend = spend or {}

    async def spend_since(self, scope_key: str, since_epoch: int) -> float:
        return self.spend.get(scope_key, 0.0)


class TestBudgetEnforcementInterface:
    def test_module_importable(self) -> None:
        import llm_budget_gateway.budget_enforcement as be

        assert be is not None

    def test_public_api_exists(self) -> None:
        for item in (
            BudgetScope,
            BudgetConfig,
            BudgetExceededError,
            RateLimitExceededError,
            CounterStore,
            InMemoryCounterStore,
            BudgetEnforcer,
            load_budget_configs,
        ):
            assert item is not None

    def test_budget_scope_is_frozen_dataclass(self) -> None:
        assert is_dataclass(BudgetScope)
        scope = BudgetScope(kind="key", key="a")
        with pytest.raises(FrozenInstanceError):
            scope.kind = "team"  # type: ignore[misc]

    def test_budget_scope_fields(self) -> None:
        assert {f.name for f in fields(BudgetScope)} == {"kind", "key"}

    def test_budget_scope_constructible(self) -> None:
        scope = BudgetScope(kind="key", key="sk_live_abc")
        assert scope.kind == "key"
        assert scope.key == "sk_live_abc"

    def test_budget_scope_scope_key_signature(self) -> None:
        sig = inspect.signature(BudgetScope.scope_key)
        assert list(sig.parameters) == ["self"]
        assert "str" in str(sig.return_annotation)

    def test_budget_config_is_dataclass(self) -> None:
        assert is_dataclass(BudgetConfig)

    def test_budget_config_field_order(self) -> None:
        assert [f.name for f in fields(BudgetConfig)] == [
            "scope",
            "soft_limit",
            "hard_limit",
            "window",
            "tpm_limit",
            "rpm_limit",
        ]

    def test_budget_config_defaults(self) -> None:
        cfg = BudgetConfig(scope=BudgetScope(kind="key", key="k"))
        assert cfg.soft_limit is None
        assert cfg.hard_limit is None
        assert cfg.window == "30d"
        assert cfg.tpm_limit is None
        assert cfg.rpm_limit is None

    def test_budget_exceeded_error_is_exception(self) -> None:
        assert issubclass(BudgetExceededError, Exception)

    def test_budget_exceeded_error_constructible(self) -> None:
        err = BudgetExceededError(BudgetScope(kind="key", key="k"), 10.0, 5.0)
        assert err.scope.kind == "key"
        assert err.spend == 10.0
        assert err.limit == 5.0

    def test_rate_limit_exceeded_error_is_exception(self) -> None:
        assert issubclass(RateLimitExceededError, Exception)

    def test_rate_limit_exceeded_error_constructible(self) -> None:
        err = RateLimitExceededError(BudgetScope(kind="key", key="k"), "tpm", 60)
        assert err.limit_type == "tpm"
        assert err.limit == 60

    def test_counter_store_is_protocol(self) -> None:
        from typing import Protocol

        assert issubclass(CounterStore, Protocol)

    def test_counter_store_method_signatures(self) -> None:
        inc = inspect.signature(CounterStore.increment)
        assert list(inc.parameters) == ["self", "key", "amount"]
        assert inc.parameters["amount"].default == 1
        assert list(inspect.signature(CounterStore.get).parameters) == ["self", "key"]
        assert list(inspect.signature(CounterStore.reset).parameters) == ["self", "key"]

    def test_in_memory_counter_store_constructible(self) -> None:
        assert InMemoryCounterStore() is not None

    def test_in_memory_counter_store_methods_exist(self) -> None:
        for name in ("increment", "get", "reset"):
            assert callable(getattr(InMemoryCounterStore, name))

    def test_in_memory_counter_store_signatures(self) -> None:
        inc = inspect.signature(InMemoryCounterStore.increment)
        assert list(inc.parameters) == ["self", "key", "amount"]
        assert inc.parameters["amount"].default == 1
        assert list(inspect.signature(InMemoryCounterStore.get).parameters) == [
            "self",
            "key",
        ]
        assert list(inspect.signature(InMemoryCounterStore.reset).parameters) == [
            "self",
            "key",
        ]

    def test_budget_enforcer_constructible(self) -> None:
        assert BudgetEnforcer(configs=[], cost_tracker=None) is not None

    def test_budget_enforcer_constructible_with_deps(self) -> None:
        enforcer = BudgetEnforcer(
            configs=[],
            cost_tracker=None,
            counter_store=InMemoryCounterStore(),
            now_fn=MutableClock(),
        )
        assert enforcer is not None

    def test_budget_enforcer_init_signature(self) -> None:
        sig = inspect.signature(BudgetEnforcer.__init__)
        assert list(sig.parameters) == [
            "self",
            "configs",
            "cost_tracker",
            "counter_store",
            "now_fn",
        ]
        assert sig.parameters["counter_store"].default is None
        assert sig.parameters["now_fn"].default is None

    def test_budget_enforcer_methods_exist(self) -> None:
        for name in (
            "config_for",
            "window_seconds",
            "check_sync",
            "check_hard",
            "soft_exceeded",
            "reconcile",
        ):
            assert callable(getattr(BudgetEnforcer, name))

    def test_config_for_signature(self) -> None:
        sig = inspect.signature(BudgetEnforcer.config_for)
        assert list(sig.parameters) == ["self", "scope"]
        assert "BudgetConfig" in str(sig.return_annotation)

    def test_window_seconds_signature(self) -> None:
        sig = inspect.signature(BudgetEnforcer.window_seconds)
        assert list(sig.parameters) == ["self", "window"]
        assert "int" in str(sig.return_annotation)

    def test_check_sync_signature(self) -> None:
        sig = inspect.signature(BudgetEnforcer.check_sync)
        assert list(sig.parameters) == ["self", "scopes", "model", "est_input_tokens"]

    def test_check_hard_is_async(self) -> None:
        assert inspect.iscoroutinefunction(BudgetEnforcer.check_hard)

    def test_check_hard_signature(self) -> None:
        sig = inspect.signature(BudgetEnforcer.check_hard)
        assert list(sig.parameters) == ["self", "scopes"]

    def test_soft_exceeded_is_sync(self) -> None:
        assert not inspect.iscoroutinefunction(BudgetEnforcer.soft_exceeded)

    def test_reconcile_is_async(self) -> None:
        assert inspect.iscoroutinefunction(BudgetEnforcer.reconcile)

    def test_reconcile_signature(self) -> None:
        sig = inspect.signature(BudgetEnforcer.reconcile)
        assert list(sig.parameters) == ["self", "usage"]

    def test_load_budget_configs_signature(self) -> None:
        sig = inspect.signature(load_budget_configs)
        assert list(sig.parameters) == ["path"]


class TestBudgetScopeBehavior:
    def test_scope_key_formats_kind_and_key(self) -> None:
        assert (
            BudgetScope(kind="key", key="sk_live_abc").scope_key() == "key:sk_live_abc"
        )

    def test_scope_key_for_user_scope(self) -> None:
        assert BudgetScope(kind="user", key="42").scope_key() == "user:42"


class TestWindowSecondsBehavior:
    @pytest.mark.parametrize(
        ("window", "expected"),
        [
            ("30s", 30),
            ("30m", 1800),
            ("30h", 108_000),
            ("30d", 2_592_000),
            ("daily", 86_400),
        ],
    )
    def test_window_seconds_fixed_windows(self, window: str, expected: int) -> None:
        enforcer = BudgetEnforcer(configs=[], cost_tracker=None, now_fn=lambda: NOW)
        assert enforcer.window_seconds(window) == expected

    def test_window_seconds_monthly(self) -> None:
        # July 2025 has 31 days -> 31 * 86400 seconds in the calendar month.
        enforcer = BudgetEnforcer(configs=[], cost_tracker=None, now_fn=lambda: NOW)
        assert enforcer.window_seconds("monthly") == 31 * 86_400


class TestConfigForBehavior:
    def test_config_for_returns_matching_config(self) -> None:
        scope = BudgetScope(kind="key", key="sk_live_abc")
        cfg = BudgetConfig(scope=scope, hard_limit=50.0, window="30d", tpm_limit=100)
        enforcer = BudgetEnforcer(configs=[cfg], cost_tracker=None)
        assert enforcer.config_for(scope) is cfg

    def test_config_for_returns_none_for_unknown_scope(self) -> None:
        cfg = BudgetConfig(scope=BudgetScope(kind="key", key="a"), hard_limit=50.0)
        enforcer = BudgetEnforcer(configs=[cfg], cost_tracker=None)
        assert enforcer.config_for(BudgetScope(kind="team", key="eng")) is None


class TestCheckSyncBehavior:
    def _enforcer(
        self,
        tpm: int | None = None,
        rpm: int | None = None,
        window: str = "30s",
        clock: MutableClock | None = None,
    ) -> BudgetEnforcer:
        scope = BudgetScope(kind="key", key="sk_live_abc")
        cfg = BudgetConfig(scope=scope, window=window, tpm_limit=tpm, rpm_limit=rpm)
        return BudgetEnforcer(
            configs=[cfg],
            cost_tracker=None,
            counter_store=InMemoryCounterStore(),
            now_fn=clock or MutableClock(),
        )

    def test_check_sync_passes_under_ceiling(self) -> None:
        enforcer = self._enforcer(tpm=5000, rpm=100)
        scope = BudgetScope(kind="key", key="sk_live_abc")
        enforcer.check_sync([scope], "gpt-4o", est_input_tokens=1000)
        enforcer.check_sync([scope], "gpt-4o", est_input_tokens=1000)

    def test_check_sync_raises_when_tpm_exceeded(self) -> None:
        # TPM counter increments by est_input_tokens: 1000 + 1000 > 1500.
        enforcer = self._enforcer(tpm=1500, rpm=100)
        scope = BudgetScope(kind="key", key="sk_live_abc")
        enforcer.check_sync([scope], "gpt-4o", est_input_tokens=1000)
        with pytest.raises(RateLimitExceededError) as exc:
            enforcer.check_sync([scope], "gpt-4o", est_input_tokens=1000)
        assert exc.value.limit_type == "tpm"
        assert exc.value.limit == 1500

    def test_check_sync_raises_when_rpm_exceeded(self) -> None:
        # RPM counter increments by 1 per call: 2 > 1.
        enforcer = self._enforcer(tpm=100_000, rpm=1)
        scope = BudgetScope(kind="key", key="sk_live_abc")
        enforcer.check_sync([scope], "gpt-4o", est_input_tokens=1000)
        with pytest.raises(RateLimitExceededError) as exc:
            enforcer.check_sync([scope], "gpt-4o", est_input_tokens=1000)
        assert exc.value.limit_type == "rpm"
        assert exc.value.limit == 1

    def test_window_reset_after_elapse_reallows(self) -> None:
        # tpm_limit=1: first call hits the ceiling, second is blocked; once the
        # 30s window elapses (injected now_fn advances), a fresh bucket starts
        # at zero and the request is allowed again.
        clock = MutableClock()
        enforcer = self._enforcer(tpm=1, rpm=100, window="30s", clock=clock)
        scope = BudgetScope(kind="key", key="sk_live_abc")
        enforcer.check_sync([scope], "gpt-4o", est_input_tokens=1)
        with pytest.raises(RateLimitExceededError):
            enforcer.check_sync([scope], "gpt-4o", est_input_tokens=1)
        clock.value += 31
        enforcer.check_sync([scope], "gpt-4o", est_input_tokens=1)

    def test_check_sync_ignores_scope_without_tpm_rpm_config(self) -> None:
        # No tpm/rpm limits configured -> nothing to enforce -> never raises.
        enforcer = self._enforcer()
        enforcer.check_sync(
            [BudgetScope(kind="key", key="sk_live_abc")], "gpt-4o", est_input_tokens=1
        )
        enforcer.check_sync(
            [BudgetScope(kind="key", key="sk_live_abc")], "gpt-4o", est_input_tokens=1
        )


class TestCheckHardBehavior:
    @pytest.fixture
    def key_scope(self) -> BudgetScope:
        return BudgetScope(kind="key", key="sk_live_abc")

    @pytest.fixture
    def team_scope(self) -> BudgetScope:
        return BudgetScope(kind="team", key="eng")

    @staticmethod
    def _enforcer(
        configs: list[BudgetConfig], spend: dict[str, float]
    ) -> BudgetEnforcer:
        return BudgetEnforcer(configs=configs, cost_tracker=FakeCostTracker(spend))

    @pytest.mark.asyncio
    async def test_check_hard_raises_when_hard_limit_exceeded(
        self, key_scope: BudgetScope
    ) -> None:
        cfg = BudgetConfig(
            scope=key_scope, soft_limit=25.0, hard_limit=50.0, window="30d"
        )
        enforcer = self._enforcer([cfg], {"key:sk_live_abc": 60.0})
        with pytest.raises(BudgetExceededError) as exc:
            await enforcer.check_hard([key_scope])
        assert exc.value.limit == 50.0

    @pytest.mark.asyncio
    async def test_check_hard_passes_when_under_limit(
        self, key_scope: BudgetScope
    ) -> None:
        cfg = BudgetConfig(scope=key_scope, hard_limit=50.0, window="30d")
        enforcer = self._enforcer([cfg], {"key:sk_live_abc": 10.0})
        await enforcer.check_hard([key_scope])

    @pytest.mark.asyncio
    async def test_composite_scope_team_over_blocks_even_if_key_under(
        self, key_scope: BudgetScope, team_scope: BudgetScope
    ) -> None:
        # The classic bypass scenario (LiteLLM #24770 class): one key is under
        # its own limit but the TEAM budget is blown -> the request is blocked.
        cfg_key = BudgetConfig(scope=key_scope, hard_limit=100.0, window="30d")
        cfg_team = BudgetConfig(scope=team_scope, hard_limit=50.0, window="30d")
        spend = {"key:sk_live_abc": 10.0, "team:eng": 60.0}
        enforcer = self._enforcer([cfg_key, cfg_team], spend)
        with pytest.raises(BudgetExceededError):
            await enforcer.check_hard([key_scope, team_scope])

    @pytest.mark.asyncio
    async def test_composite_scope_key_over_blocks(
        self, key_scope: BudgetScope, team_scope: BudgetScope
    ) -> None:
        cfg_key = BudgetConfig(scope=key_scope, hard_limit=100.0, window="30d")
        cfg_team = BudgetConfig(scope=team_scope, hard_limit=50.0, window="30d")
        spend = {"key:sk_live_abc": 150.0, "team:eng": 10.0}
        enforcer = self._enforcer([cfg_key, cfg_team], spend)
        with pytest.raises(BudgetExceededError):
            await enforcer.check_hard([key_scope, team_scope])

    @pytest.mark.asyncio
    async def test_soft_limit_exceeded_does_not_block(
        self, key_scope: BudgetScope
    ) -> None:
        # Soft limits alert only: spend 60 > soft 25 but < hard 100 -> pass.
        cfg = BudgetConfig(
            scope=key_scope, soft_limit=25.0, hard_limit=100.0, window="30d"
        )
        enforcer = self._enforcer([cfg], {"key:sk_live_abc": 60.0})
        await enforcer.check_hard([key_scope])


class TestSoftExceededBehavior:
    def test_soft_exceeded_returns_scopes_past_soft_limit(self) -> None:
        scope = BudgetScope(kind="key", key="sk_live_abc")
        cfg = BudgetConfig(scope=scope, soft_limit=25.0, hard_limit=100.0, window="30d")
        enforcer = BudgetEnforcer(
            configs=[cfg], cost_tracker=FakeCostTracker({"key:sk_live_abc": 40.0})
        )
        assert enforcer.soft_exceeded([scope]) == [scope]

    def test_soft_exceeded_empty_when_under_soft_limit(self) -> None:
        scope = BudgetScope(kind="key", key="sk_live_abc")
        cfg = BudgetConfig(scope=scope, soft_limit=25.0, hard_limit=100.0, window="30d")
        enforcer = BudgetEnforcer(
            configs=[cfg], cost_tracker=FakeCostTracker({"key:sk_live_abc": 10.0})
        )
        assert enforcer.soft_exceeded([scope]) == []

    def test_soft_exceeded_empty_when_no_soft_limit(self) -> None:
        scope = BudgetScope(kind="key", key="sk_live_abc")
        cfg = BudgetConfig(scope=scope, hard_limit=100.0, window="30d")
        enforcer = BudgetEnforcer(
            configs=[cfg], cost_tracker=FakeCostTracker({"key:sk_live_abc": 10.0})
        )
        assert enforcer.soft_exceeded([scope]) == []

    @pytest.mark.asyncio
    async def test_soft_exceeded_with_real_async_tracker_inside_running_loop(
        self,
    ) -> None:
        """M2: soft_exceeded must not raise RuntimeError when called from
        within a running event loop with a real async tracker (only the dict
        fast-path used to work; asyncio.run in-loop crashed)."""
        class AsyncTracker:
            def __init__(self, spend: dict[str, float]) -> None:
                self.spend_since_calls = 0
                self._spend = spend

            async def spend_since(self, scope_key: str, since_epoch: int) -> float:
                self.spend_since_calls += 1
                return self._spend.get(scope_key, 0.0)

        scope = BudgetScope(kind="key", key="sk_live_abc")
        cfg = BudgetConfig(scope=scope, soft_limit=25.0, hard_limit=100.0, window="30d")
        tracker = AsyncTracker({"key:sk_live_abc": 40.0})
        enforcer = BudgetEnforcer(configs=[cfg], cost_tracker=tracker)
        # sync call from within an async test (running loop active) must work
        assert enforcer.soft_exceeded([scope]) == [scope]
        assert tracker.spend_since_calls == 1


class TestReconcileBehavior:
    @pytest.mark.asyncio
    async def test_reconcile_accepts_usage_record(self) -> None:
        enforcer = BudgetEnforcer(configs=[], cost_tracker=FakeCostTracker({}))
        record = SimpleNamespace(scope_key="key:sk_live_abc", total_cost=1.25)
        await enforcer.reconcile(record)  # must complete without raising


class TestLoadBudgetConfigsBehavior:
    VALID_YAML = """
scopes:
  - scope:
      kind: key
      key: "sk_live_abc"
    soft_limit: 25.0
    hard_limit: 50.0
    window: "30d"
    tpm_limit: 90000
    rpm_limit: 60
  - scope:
      kind: team
      key: "eng"
    hard_limit: 1000.0
    window: "monthly"
"""

    def test_load_valid_yaml(self, tmp_path) -> None:
        path = tmp_path / "budgets.yaml"
        path.write_text(self.VALID_YAML)
        configs = load_budget_configs(path)
        assert len(configs) == 2
        assert configs[0].scope.kind == "key"
        assert configs[0].scope.key == "sk_live_abc"
        assert configs[0].soft_limit == 25.0
        assert configs[0].hard_limit == 50.0
        assert configs[0].window == "30d"
        assert configs[0].tpm_limit == 90000
        assert configs[0].rpm_limit == 60
        assert configs[1].scope.kind == "team"
        assert configs[1].window == "monthly"

    def test_load_malformed_yaml_raises_value_error(self, tmp_path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("scopes: [unclosed")
        with pytest.raises(ValueError):
            load_budget_configs(path)

    def test_load_unknown_scope_kind_rejected(self, tmp_path) -> None:
        path = tmp_path / "bad-kind.yaml"
        path.write_text(
            "scopes:\n"
            "  - scope:\n"
            "      kind: planet\n"
            "      key: x\n"
            "    hard_limit: 1.0\n"
        )
        with pytest.raises(ValueError):
            load_budget_configs(path)

    def test_load_missing_file_raises_file_not_found(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            load_budget_configs(tmp_path / "nope.yaml")
