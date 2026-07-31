import pytest

from llm_budget_gateway.cost_estimation import CostEstimator
from llm_budget_gateway.cost_tracking import CostCalculator, ModelPrice, PriceMap
from llm_budget_gateway.model_fallback import FallbackManager


def estimator():
    prices = PriceMap({"test-model": ModelPrice(2.0, 8.0)})
    return CostEstimator(CostCalculator(prices), FallbackManager([]))


def test_estimate_calculates_upper_bound_cost():
    result = estimator().estimate(
        {"model": "test-model", "prompt": "a" * 400, "max_tokens": 100}
    )
    assert result.estimated_input_tokens == 100
    assert result.max_output_tokens == 100
    assert result.estimated_input_cost == pytest.approx(0.0002)
    assert result.maximum_output_cost == pytest.approx(0.0008)
    assert result.maximum_total_cost == pytest.approx(0.001)
    assert result.pricing_known is True


def test_unknown_price_is_explicit_not_silent():
    result = estimator().estimate({"model": "unknown", "prompt": "hi"})
    assert result.maximum_total_cost == 0
    assert result.pricing_known is False


def test_missing_model_rejected():
    with pytest.raises(ValueError, match="model is required"):
        estimator().estimate({"prompt": "hi"})


def test_negative_or_boolean_output_limit_rejected():
    for value in (-1, True):
        with pytest.raises(ValueError):
            estimator().estimate({"model": "test-model", "max_tokens": value})
