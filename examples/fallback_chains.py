"""Automatic model fallback: chains, error classification, cooldowns.

Demonstrates the public fallback API without a network: typed fallback
chains, error classification, cooldown filtering, context pre-checks,
and the dispatch loop — driven by a fake proxy that fails on the
primary model and succeeds on the fallback.

Usage:
    .venv/bin/python examples/fallback_chains.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from llm_budget_gateway.budget_enforcement import BudgetScope, RateLimitExceededError
from llm_budget_gateway.gateway_proxy import ProviderResponse
from llm_budget_gateway.model_fallback import FallbackConfig, FallbackManager

CHAIN = ["gpt-3.5-turbo", "claude-3-5-haiku"]


class _FakeProxy:
    """Records every model it was asked to serve; fails a configurable set."""

    def __init__(self, fail: set[str] | None = None) -> None:
        self.fail = fail or set()
        self.calls: list[str] = []

    async def forward(self, model: str, body: dict) -> ProviderResponse:
        self.calls.append(model)
        if model in self.fail:
            raise RateLimitExceededError(
                BudgetScope(kind="key", key="keyA"), "rpm", 60
            )
        return ProviderResponse(
            status_code=200,
            body={"id": f"cmpl-{model}", "model": model},
            headers={},
            model=model,
            usage=None,
            latency_ms=5,
        )


def classification_demo() -> None:
    print("== classify_error ==")
    manager = FallbackManager(configs=[])
    samples = [
        (
            "RateLimitExceededError",
            RateLimitExceededError(BudgetScope("key", "k"), "rpm", 60),
            None,
        ),
        ("TimeoutError", TimeoutError("upstream timed out"), None),
        ("ValueError + status 503", ValueError("provider boom"), 503),
        (
            "'content management policy' message",
            ValueError("content management policy triggered"),
            None,
        ),
        (
            "'maximum context length' message",
            ValueError("maximum context length exceeded"),
            None,
        ),
        ("unrecognized error", ValueError("weird"), None),
    ]
    for label, exc, status in samples:
        print(f"  {label:34s} -> {manager.classify_error(exc, status)!r}")


def cooldown_demo() -> None:
    print("== cooldowns filter the chain ==")
    config = FallbackConfig(model="gpt-4o", chain=CHAIN, cooldown_seconds=60)
    manager = FallbackManager(configs=[config])
    print(f"  chain_for('gpt-4o') before failure: {manager.chain_for('gpt-4o')}")
    manager.mark_failed("gpt-3.5-turbo")
    print(f"  after mark_failed('gpt-3.5-turbo'):  {manager.chain_for('gpt-4o')}")
    print(f"  in_cooldown('gpt-3.5-turbo') = {manager.in_cooldown('gpt-3.5-turbo')}")

    instant = FallbackConfig(model="gpt-4o", chain=CHAIN, cooldown_seconds=0)
    manager_instant = FallbackManager(configs=[instant])
    manager_instant.mark_failed("gpt-4o")
    print(
        f"  cooldown_seconds=0: in_cooldown('gpt-4o') = "
        f"{manager_instant.in_cooldown('gpt-4o')} (never cools down)"
    )


def dispatch_demo() -> None:
    print("== dispatch: primary fails (429), fallback serves ==")
    manager = FallbackManager(configs=[FallbackConfig(model="gpt-4o", chain=CHAIN)])
    proxy = _FakeProxy(fail={"gpt-4o"})
    body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
    response = asyncio.run(manager.dispatch(proxy, "gpt-4o", body, "sk", {}))
    print(f"  models called: {proxy.calls}")
    print(f"  served by:     {response.model} (response.model = serving model)")

    print("== dispatch: disable_fallbacks=True re-raises the original error ==")
    manager2 = FallbackManager(configs=[FallbackConfig(model="gpt-4o", chain=CHAIN)])
    proxy2 = _FakeProxy(fail={"gpt-4o"})
    try:
        asyncio.run(
            manager2.dispatch(
                proxy2, "gpt-4o", body, "sk", {}, disable_fallbacks=True
            )
        )
    except RateLimitExceededError as exc:
        print(f"  models called: {proxy2.calls}")
        print(f"  raised: RateLimitExceededError({exc.limit_type})")

    print("== dispatch: chain exhausted -> re-raises the last error (HTTP 502) ==")
    manager3 = FallbackManager(configs=[FallbackConfig(model="gpt-4o", chain=CHAIN)])
    proxy3 = _FakeProxy(fail={"gpt-4o", "gpt-3.5-turbo", "claude-3-5-haiku"})
    try:
        asyncio.run(manager3.dispatch(proxy3, "gpt-4o", body, "sk", {}))
    except RateLimitExceededError as exc:
        print(f"  models called: {proxy3.calls}")
        print(
            f"  raised: RateLimitExceededError({exc.limit_type}) "
            f"-> 502 at HTTP layer"
        )


def context_precheck_demo() -> None:
    print("== context pre-check: too-big request skips small-context models ==")
    manager = FallbackManager(configs=[FallbackConfig(model="gpt-4o", chain=CHAIN)])
    proxy = _FakeProxy()  # gpt-4o succeeds; nothing in the chain should be tried
    big_body = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "a" * 70_000}],  # ~17.5k est tokens
    }
    print(f"  estimate_tokens(big_body) = {manager.estimate_tokens(big_body)}")
    print(
        f"  context_safe('gpt-4o', big_body)       = "
        f"{manager.context_safe('gpt-4o', big_body)}"
    )
    print(
        f"  context_safe('gpt-3.5-turbo', big_body) = "
        f"{manager.context_safe('gpt-3.5-turbo', big_body)}"
    )
    response = asyncio.run(manager.dispatch(proxy, "gpt-4o", big_body, "sk", {}))
    print(
        f"  dispatch called only: {proxy.calls} "
        f"(small-context model skipped pre-call)"
    )
    print(f"  served by: {response.model}")


if __name__ == "__main__":
    classification_demo()
    cooldown_demo()
    dispatch_demo()
    context_precheck_demo()
