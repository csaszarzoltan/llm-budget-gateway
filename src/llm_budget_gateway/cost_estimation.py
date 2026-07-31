"""Preflight LLM request cost estimation without sending provider traffic."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .cost_tracking import CostCalculator
from .model_fallback import FallbackManager


@dataclass(frozen=True)
class CostEstimate:
    model: str
    estimated_input_tokens: int
    max_output_tokens: int
    estimated_input_cost: float
    maximum_output_cost: float
    maximum_total_cost: float
    currency: str = "USD"
    pricing_known: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "estimated_input_tokens": self.estimated_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "estimated_input_cost": round(self.estimated_input_cost, 10),
            "maximum_output_cost": round(self.maximum_output_cost, 10),
            "maximum_total_cost": round(self.maximum_total_cost, 10),
            "currency": self.currency,
            "pricing_known": self.pricing_known,
            "disclaimer": (
                "Estimate only; actual provider tokenization and output length "
                "may differ."
            ),
        }


class CostEstimator:
    """Estimate a request's upper-bound cost using the configured price map."""

    def __init__(
        self, calculator: CostCalculator, fallback_manager: FallbackManager
    ) -> None:
        self._calculator = calculator
        self._fallback_manager = fallback_manager

    def estimate(self, body: Mapping[str, object]) -> CostEstimate:
        model = body.get("model")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model is required")
        max_output = body.get("max_completion_tokens", body.get("max_tokens", 0))
        if (
            isinstance(max_output, bool)
            or not isinstance(max_output, int)
            or max_output < 0
        ):
            raise ValueError(
                "max_tokens/max_completion_tokens must be a non-negative integer"
            )
        input_tokens = self._fallback_manager.estimate_tokens(dict(body))
        input_cost, output_cost, total = self._calculator.calculate(
            model, input_tokens, max_output
        )
        price = self._calculator._price_map.get_price(model)
        known = bool(price.input_cost_per_million or price.output_cost_per_million)
        return CostEstimate(
            model,
            input_tokens,
            max_output,
            input_cost,
            output_cost,
            total,
            pricing_known=known,
        )
