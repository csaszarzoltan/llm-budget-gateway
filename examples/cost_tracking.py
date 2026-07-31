"""Cost tracking: pricing, per-request math, and the SQLite ledger.

Demonstrates the public cost-tracking API without a network:
``PriceMap`` (litellm baseline + overrides), ``CostCalculator`` math,
``CostStore``/SQLite WAL persistence, scope spend queries, and
streaming usage aggregation.

Usage:
    .venv/bin/python examples/cost_tracking.py
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from llm_budget_gateway.budget_enforcement import BudgetScope
from llm_budget_gateway.cost_tracking import (
    CostCalculator,
    CostStore,
    CostTracker,
    ModelPrice,
    PriceMap,
    TokenUsage,
    accumulate_usage,
)

CHAT_MODEL = "gpt-4o"
CUSTOM_MODEL = "my-custom-model"  # e.g. a self-hosted model not in litellm


def pricing_demo() -> None:
    print("== pricing: litellm baseline + overrides ==")
    price_map = PriceMap(
        overrides={
            CUSTOM_MODEL: ModelPrice(
                input_cost_per_million=1.5,
                output_cost_per_million=2.5,
            )
        }
    )
    gpt4o = price_map.get_price(CHAT_MODEL)
    print(
        f"  {CHAT_MODEL} (litellm baseline): "
        f"${gpt4o.input_cost_per_million}/1M in, "
        f"${gpt4o.output_cost_per_million}/1M out"
    )
    custom = price_map.get_price(CUSTOM_MODEL)
    print(
        f"  {CUSTOM_MODEL} (override): "
        f"${custom.input_cost_per_million}/1M in, "
        f"${custom.output_cost_per_million}/1M out"
    )
    zero = price_map.get_price("no-such-model")
    print(
        f"  no-such-model: ${zero.input_cost_per_million}/1M in "
        f"(unknown -> $0, no raise)"
    )

    calculator = CostCalculator(price_map)
    in_cost, out_cost, total = calculator.calculate(CHAT_MODEL, 1000, 500)
    print(f"  calculate({CHAT_MODEL}, 1000 prompt, 500 completion):")
    print(f"    input=${in_cost:.6f}  output=${out_cost:.6f}  total=${total:.6f}")


def ledger_demo() -> None:
    print("== ledger: SQLite (WAL) persistence + spend queries ==")
    tmp = Path(tempfile.mkdtemp(prefix="cost-tracking-"))
    try:
        db_path = tmp / "costs.db"
        store = CostStore(str(db_path))
        import sqlite3

        mode = (
            sqlite3.connect(str(db_path)).execute("PRAGMA journal_mode").fetchone()[0]
        )
        print(f"  journal_mode={mode}")
        tracker = CostTracker(
            store=store,
            calculator=CostCalculator(
                PriceMap(overrides={CHAT_MODEL: ModelPrice(5.0, 15.0)})
            ),
        )

        # Two successful chat requests under key scope 'key1'.
        for i in range(2):
            record = tracker.build_record(
                request_id=f"req-{i}",
                scope=BudgetScope(kind="key", key="key1"),
                model=CHAT_MODEL,
                provider="litellm",
                usage=TokenUsage(
                    prompt_tokens=1000,
                    completion_tokens=500,
                    total_tokens=1500,
                ),
                latency_ms=120,
                status="success",
            )
            asyncio.run(tracker.record(record))

        # One failed request (timeout): zero cost, status recorded.
        failed = tracker.build_record(
            request_id="req-fail",
            scope=BudgetScope(kind="key", key="key1"),
            model=CHAT_MODEL,
            provider="litellm",
            usage=None,
            latency_ms=0,
            status="timeout",
        )
        asyncio.run(tracker.record(failed))

        # One request from another key: must not count toward key1.
        other = tracker.build_record(
            request_id="req-other",
            scope=BudgetScope(kind="key", key="key2"),
            model=CHAT_MODEL,
            provider="litellm",
            usage=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
            latency_ms=90,
            status="success",
        )
        asyncio.run(tracker.record(other))

        spend_key1 = asyncio.run(tracker.spend_since("key:key1", 0))
        spend_key2 = asyncio.run(tracker.spend_since("key:key2", 0))
        spend_global = asyncio.run(tracker.spend_since("global:default", 0))
        print(
            f"  spend_since('key:key1')  = ${spend_key1:.6f}   (2 x $0.0125 = $0.025)"
        )
        print(f"  spend_since('key:key2')  = ${spend_key2:.6f}   (1 x $0.00125)")
        print(f"  spend_since('global:default') = ${spend_global:.6f}   (all keys)")
        store.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def streaming_usage_demo() -> None:
    print("== streaming: aggregate partial usage chunks ==")
    chunks = [
        {"prompt_tokens": 1000, "completion_tokens": 0, "total_tokens": 1000},
        {"prompt_tokens": 0, "completion_tokens": 300, "total_tokens": 300},
        {"prompt_tokens": 0, "completion_tokens": 200, "total_tokens": 200},
    ]
    usage = accumulate_usage(chunks)
    print(f"  chunks={chunks}")
    print(f"  accumulated -> {usage}")


if __name__ == "__main__":
    pricing_demo()
    ledger_demo()
    streaming_usage_demo()
