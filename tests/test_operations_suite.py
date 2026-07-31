import pytest

from llm_budget_gateway.operations_suite import (
    ModelCatalog,
    PromptRegistry,
    QuotaDiagnostic,
    RetryPolicy,
    SLOMonitor,
)


def test_retry_policy_bounds_transient_and_terminal_errors():
    policy = RetryPolicy(3, 1000, 400)
    d = policy.decide(1, 100, 429, seed=7)
    assert d.retry and 0 <= d.delay_ms <= 250
    assert policy.decide(3, 100, 429).reason == "attempt_limit"
    assert policy.decide(1, 1000, 503).reason == "elapsed_limit"
    assert policy.decide(1, 0, 400).reason == "non_retryable"
    with pytest.raises(ValueError):
        policy.decide(0, 0, 429)
    with pytest.raises(ValueError):
        RetryPolicy(0)


def test_quota_diagnostic_distinguishes_429_failures():
    q = QuotaDiagnostic()
    assert (
        q.classify(429, "insufficient_quota", "billing")["category"]
        == "financial_quota"
    )
    assert q.classify(429, None, "TPM token limit")["category"] == "token_rate_limit"
    assert q.classify(429, None, "slow down")["category"] == "request_rate_limit"
    assert q.classify(503, None, None)["category"] == "provider_availability"
    assert q.classify(400, None, None)["action"] == "do_not_retry"
    with pytest.raises(TypeError):
        q.classify(True, None, None)


def test_model_catalog_validates_normalizes_and_sorts():
    c = ModelCatalog()
    items = c.normalize(
        [
            {
                "id": "b",
                "context_window": 1,
                "input_cost_per_million": 2,
                "capabilities": ["vision", "vision"],
                "regions": ["eu"],
            },
            {"id": "a", "context_window": 8, "output_cost_per_million": "3"},
        ]
    )
    assert [x["id"] for x in items] == ["a", "b"] and items[1]["capabilities"] == [
        "vision"
    ]
    with pytest.raises(ValueError):
        c.normalize(
            [{"id": "a", "context_window": 1}, {"id": "a", "context_window": 2}]
        )
    with pytest.raises(ValueError):
        c.normalize([{"id": "a", "context_window": 0}])
    with pytest.raises(ValueError):
        c.normalize([{"id": "a", "context_window": 1, "input_cost_per_million": -1}])


def test_slo_monitor_states_and_edge_validation():
    s = SLOMonitor()
    assert s.evaluate(1000, 0)["state"] == "healthy"
    assert s.evaluate(1000, 10, 0.99)["state"] in {"warning", "healthy"}
    assert s.evaluate(1000, 30, 0.99)["state"] == "critical"
    for values in ((0, 0, 0.99), (10, 11, 0.99), (10, -1, 0.99), (10, 1, 1.0)):
        with pytest.raises(ValueError):
            s.evaluate(*values)


def test_prompt_registry_versions_isolates_and_assigns(tmp_path):
    p = PromptRegistry(str(tmp_path / "p.db"), clock=lambda: 10)
    one = p.create("t1", "support", "Hello {name}", {"owner": "ai", "secret": "drop"})
    two = p.create("t1", "support", "Hi {name}")
    assert (
        one["version"] == 1 and two["version"] == 2 and "secret" not in one["metadata"]
    )
    assert [x["version"] for x in p.list("t1", "support")] == [2, 1] and p.list(
        "t2", "support"
    ) == []
    a = p.assign("t1", "support", "user-1", [1, 2])
    assert a == p.assign("t1", "support", "user-1", [2, 1])
    with pytest.raises(ValueError):
        p.create("", "", "")
    with pytest.raises(ValueError):
        p.assign("t1", "support", "", [1])
    with pytest.raises(ValueError):
        p.assign("t1", "support", "u", [99])
