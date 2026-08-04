"""Cost and performance optimization services for the LLM gateway."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence


class PromptCompressor:
    """Apply deterministic whitespace and duplicate-example compression."""

    def compress(self, text: str) -> dict[str, object]:
        """Compress text without changing non-whitespace token order."""
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        original = len(text)
        lines = [" ".join(line.split()) for line in text.splitlines()]
        output, previous = [], None
        for line in lines:
            if line and line != previous:
                output.append(line)
                previous = line
        compressed = "\n".join(output)
        saved = original - len(compressed)
        return {
            "text": compressed,
            "original_chars": original,
            "compressed_chars": len(compressed),
            "saved_chars": saved,
            "savings_ratio": saved / original if original else 0.0,
        }


class SavingsAttributor:
    """Attribute measured savings to optimization drivers without double counting."""

    def calculate(
        self, baseline_cost: float, actual_cost: float, drivers: Mapping[str, float]
    ) -> dict[str, object]:
        """Normalize driver claims to the realized non-negative savings amount."""
        values = (baseline_cost, actual_cost, *drivers.values())
        if any(
            isinstance(x, bool)
            or not isinstance(x, (int, float))
            or not math.isfinite(x)
            or x < 0
            for x in values
        ):
            raise ValueError(
                "costs and driver values must be finite non-negative numbers"
            )
        realized = max(0.0, baseline_cost - actual_cost)
        claimed = sum(drivers.values())
        scale = min(1.0, realized / claimed) if claimed else 0.0
        attributed = {name: value * scale for name, value in sorted(drivers.items())}
        return {
            "baseline_cost": baseline_cost,
            "actual_cost": actual_cost,
            "realized_savings": realized,
            "drivers": attributed,
            "unattributed": max(0.0, realized - sum(attributed.values())),
        }


class CachePolicyAdvisor:
    """Recommend an exact-cache TTL from reuse, volatility, and sensitivity."""

    def recommend(
        self,
        reuse_probability: float,
        volatility: float,
        sensitive: bool,
        max_ttl: int = 86400,
    ) -> dict[str, object]:
        """Return a bounded TTL and an explainable cache decision."""
        if (
            any(
                isinstance(x, bool)
                or not isinstance(x, (int, float))
                or not 0 <= x <= 1
                for x in (reuse_probability, volatility)
            )
            or isinstance(max_ttl, bool)
            or max_ttl < 1
        ):
            raise ValueError("probabilities and max_ttl are invalid")
        if sensitive or reuse_probability < 0.1:
            return {
                "cache": False,
                "ttl": 0,
                "reason": "sensitive" if sensitive else "low_reuse",
            }
        ttl = max(60, round(max_ttl * reuse_probability * (1 - volatility)))
        return {
            "cache": True,
            "ttl": min(max_ttl, ttl),
            "reason": "reuse_outweighs_volatility",
        }


class BudgetForecast:
    """Forecast period-end spend and risk using observed daily costs."""

    def forecast(
        self,
        daily_costs: Sequence[float],
        elapsed_days: int,
        period_days: int,
        budget: float,
    ) -> dict[str, object]:
        """Return run rate, projected spend, remaining budget, and risk state."""
        if not daily_costs or any(
            isinstance(x, bool)
            or not isinstance(x, (int, float))
            or not math.isfinite(x)
            or x < 0
            for x in daily_costs
        ):
            raise ValueError("daily costs must be finite non-negative numbers")
        if (
            isinstance(elapsed_days, bool)
            or isinstance(period_days, bool)
            or not 1 <= elapsed_days <= period_days
            or budget < 0
        ):
            raise ValueError("period and budget are invalid")
        run_rate = sum(daily_costs) / len(daily_costs)
        projected = run_rate * period_days
        ratio = projected / budget if budget else (math.inf if projected else 0.0)
        state = "critical" if ratio > 1.1 else "warning" if ratio > 0.9 else "healthy"
        return {
            "daily_run_rate": run_rate,
            "projected_spend": projected,
            "budget": budget,
            "remaining_budget": max(0.0, budget - projected),
            "risk": state,
        }


class OptimizationExperimentStore:
    """Persist tenant-isolated optimization experiments and winner decisions."""

    def __init__(self, path: str, clock: Callable[[], int] | None = None) -> None:
        self.clock = clock or (lambda: int(time.time()))
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS optimization_experiment(id TEXT PRIMARY KEY,tenant TEXT,name TEXT,variant TEXT,cost REAL,latency REAL,quality REAL,created INTEGER)"
        )
        self.db.commit()

    def record(
        self,
        tenant: str,
        name: str,
        variant: str,
        cost: float,
        latency: float,
        quality: float,
    ) -> dict[str, object]:
        """Record one experiment observation with strict normalized quality."""
        if not all(isinstance(x, str) and x for x in (tenant, name, variant)):
            raise ValueError("tenant, name, and variant are required")
        if (
            any(
                isinstance(x, bool)
                or not isinstance(x, (int, float))
                or not math.isfinite(x)
                for x in (cost, latency, quality)
            )
            or cost < 0
            or latency < 0
            or not 0 <= quality <= 1
        ):
            raise ValueError("experiment metrics are invalid")
        raw = f"{tenant}:{name}:{variant}:{cost}:{latency}:{quality}:{self.clock()}"
        item_id = hashlib.sha256(raw.encode()).hexdigest()[:20]
        self.db.execute(
            "INSERT INTO optimization_experiment VALUES(?,?,?,?,?,?,?,?)",
            (item_id, tenant, name, variant, cost, latency, quality, self.clock()),
        )
        self.db.commit()
        return {"id": item_id, "variant": variant}

    def winner(
        self, tenant: str, name: str, minimum_quality: float
    ) -> dict[str, object]:
        """Select lowest-cost eligible variant, using latency as tie breaker."""
        if not 0 <= minimum_quality <= 1:
            raise ValueError("minimum_quality must be between zero and one")
        rows = list(
            self.db.execute(
                "SELECT variant,AVG(cost) cost,AVG(latency) latency,AVG(quality) quality,COUNT(*) samples FROM optimization_experiment WHERE tenant=? AND name=? GROUP BY variant",
                (tenant, name),
            )
        )
        eligible = [dict(row) for row in rows if row["quality"] >= minimum_quality]
        if not eligible:
            raise ValueError("no eligible experiment variant")
        selected = min(
            eligible, key=lambda row: (row["cost"], row["latency"], row["variant"])
        )
        return {
            "winner": selected,
            "eligible_count": len(eligible),
            "integrity": hashlib.sha256(
                json.dumps(eligible, sort_keys=True).encode()
            ).hexdigest(),
        }
