import pytest

from llm_budget_gateway.optimization_suite import (
    BudgetForecast,
    CachePolicyAdvisor,
    OptimizationExperimentStore,
    PromptCompressor,
    SavingsAttributor,
)


def test_prompt_compression():
    p = PromptCompressor()
    r = p.compress(" hello   world\nhello world\n\nnext ")
    assert r["text"] == "hello world\nnext" and r["saved_chars"] > 0
    assert p.compress("")["savings_ratio"] == 0
    with pytest.raises(TypeError):
        p.compress(None)


def test_savings_attribution():
    s = SavingsAttributor()
    r = s.calculate(10, 6, {"cache": 3, "routing": 3})
    assert sum(r["drivers"].values()) == 4 and r["unattributed"] == 0
    assert s.calculate(5, 6, {})["realized_savings"] == 0
    with pytest.raises(ValueError):
        s.calculate(float("inf"), 0, {})


def test_cache_advisor():
    c = CachePolicyAdvisor()
    assert not c.recommend(0.9, 0.1, True)["cache"]
    assert c.recommend(0.8, 0.2, False)["ttl"] > 0
    assert not c.recommend(0.01, 0, False)["cache"]
    with pytest.raises(ValueError):
        c.recommend(2, 0, False)


def test_budget_forecast():
    b = BudgetForecast()
    assert b.forecast([10, 10], 2, 30, 400)["risk"] == "healthy"
    assert b.forecast([20], 1, 30, 500)["risk"] == "critical"
    with pytest.raises(ValueError):
        b.forecast([], 1, 30, 1)
    with pytest.raises(ValueError):
        b.forecast([1], 31, 30, 1)


def test_experiment_store(tmp_path):
    e = OptimizationExperimentStore(str(tmp_path / "o.db"), lambda: 10)
    e.record("t", "r", "a", 1, 100, 0.9)
    e.record("t", "r", "b", 2, 50, 1)
    assert e.winner("t", "r", 0.8)["winner"]["variant"] == "a"
    with pytest.raises(ValueError):
        e.record("", "", "", 0, 0, 0)
    with pytest.raises(ValueError):
        e.winner("x", "r", 0.8)
