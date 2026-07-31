"""Budget enforcement: sync TPM/RPM ceilings + async dollar budgets.

Demonstrates the public enforcement API without a network: window
mapping, YAML config loading, rate-limit (429) ceilings via
``InMemoryCounterStore``, hard dollar budgets (412) via a stubbed cost
tracker, soft-limit reporting, and composite (hierarchical) scopes.

Usage:
    .venv/bin/python examples/budget_enforcement.py
"""

from __future__ import annotations

import calendar
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from llm_budget_gateway.budget_enforcement import (
    BudgetConfig,
    BudgetEnforcer,
    BudgetExceededError,
    BudgetScope,
    InMemoryCounterStore,
    RateLimitExceededError,
    load_budget_configs,
)

EXAMPLE_YAML = str(Path(__file__).resolve().parent / "budgets.example.yaml")


class _FakeTracker:
    """Minimal async stand-in for CostTracker.

    ``soft_exceeded`` reads the synchronous ``spend`` dict fast-path;
    ``check_hard`` awaits ``spend_since``.
    """

    def __init__(self, spend: dict[str, float]) -> None:
        self.spend = spend

    async def spend_since(self, scope_key: str, since_epoch: int) -> float:
        return self.spend.get(scope_key, 0.0)


def window_demo() -> None:
    print("== windows ==")
    enforcer = BudgetEnforcer(configs=[], cost_tracker=_FakeTracker({}))
    year, month = time.gmtime()[:2]
    expected_monthly = calendar.monthrange(year, month)[1] * 86_400
    for window in ("30s", "30m", "30h", "30d", "daily", "monthly"):
        seconds = enforcer.window_seconds(window)
        print(f"  {window:8s} -> {seconds:>9,}s")
    print(f"  (monthly = current calendar month: {expected_monthly:,}s)")


def yaml_demo() -> None:
    print("== load_budget_configs(examples/budgets.example.yaml) ==")
    for cfg in load_budget_configs(EXAMPLE_YAML):
        print(
            f"  {cfg.scope.scope_key():16s} soft={cfg.soft_limit} "
            f"hard={cfg.hard_limit} window={cfg.window!r} "
            f"tpm={cfg.tpm_limit} rpm={cfg.rpm_limit}"
        )


def sync_ceiling_demo() -> None:
    print("== sync TPM/RPM ceiling (HTTP 429 path) ==")
    config = BudgetConfig(
        scope=BudgetScope(kind="key", key="keyA"),
        tpm_limit=1000,
        rpm_limit=2,
        window="30s",
    )
    counters = InMemoryCounterStore()
    enforcer = BudgetEnforcer(
        configs=[config],
        cost_tracker=_FakeTracker({}),
        counter_store=counters,
    )
    scope = config.scope
    for i in range(1, 4):
        try:
            enforcer.check_sync([scope], "gpt-4o", est_input_tokens=100)
            now = int(time.time())
            bucket = (now // 30) * 30
            rpm_key = f"key:keyA:30s:{bucket}:rpm"
            print(f"  call {i}: allowed (rpm counter={counters.get(rpm_key)})")
        except RateLimitExceededError as exc:
            print(
                f"  call {i}: RateLimitExceededError("
                f"{exc.limit_type}, limit={exc.limit})"
            )


def hard_budget_demo() -> None:
    print("== hard dollar budgets (HTTP 412 path) + composite scopes ==")
    configs = [
        BudgetConfig(
            scope=BudgetScope(kind="key", key="keyA"),
            hard_limit=50.0,
            soft_limit=45.0,
            window="30s",
        ),
        BudgetConfig(
            scope=BudgetScope(kind="team", key="eng"),
            hard_limit=300.0,
            soft_limit=280.0,
            window="30s",
        ),
    ]
    tracker = _FakeTracker(
        {"key:keyA": 40.0, "team:eng": 400.0, "global:default": 0.0}
    )
    enforcer = BudgetEnforcer(configs=configs, cost_tracker=tracker)

    scopes = [
        BudgetScope(kind="key", key="keyA"),
        BudgetScope(kind="team", key="eng"),
        BudgetScope(kind="global", key="default"),
    ]

    import asyncio

    print("  scopes checked per request: key:keyA, team:eng, global:default")
    asyncio.run(enforcer.check_hard([BudgetScope(kind="key", key="keyA")]))
    print("  check_hard([key:keyA])   : spend $40 < hard $50 -> passes (no raise)")

    try:
        asyncio.run(enforcer.check_hard(scopes))
    except BudgetExceededError as exc:
        print(
            f"  check_hard(scopes) raises BudgetExceededError: "
            f"scope={exc.scope.scope_key()} spend=${exc.spend} "
            f">= limit=${exc.limit}"
        )
        print("  (team over its budget blocks the request even though the key is fine)")

    exceeded = enforcer.soft_exceeded(scopes)
    print(
        f"  soft_exceeded(scopes) -> {[s.scope_key() for s in exceeded]} "
        f"(never raises)"
    )


if __name__ == "__main__":
    window_demo()
    yaml_demo()
    sync_ceiling_demo()
    hard_budget_demo()
