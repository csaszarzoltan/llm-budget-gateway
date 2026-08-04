import pytest

from llm_budget_gateway.market_features import (
    CostAwareRouter,
    ExactResponseCache,
    PIIRedactor,
    SignedWebhook,
    UsageAnomalyDetector,
)


def test_pii_redactor_all_categories_and_empty():
    r = PIIRedactor()
    out = r.redact("a@b.com +41 44 123 45 67 4111 1111 1111 1111")
    assert (
        out.count == 3
        and set(out.categories) == {"email", "phone", "card"}
        and "a@b.com" not in out.text
    )
    assert r.redact("").count == 0
    with pytest.raises(TypeError):
        r.redact(None)


def test_cache_ttl_tenant_isolation_and_validation(tmp_path):
    now = [100]
    c = ExactResponseCache(str(tmp_path / "c.db"), clock=lambda: now[0])
    req = {"b": 2, "a": 1}
    key = c.put("a", req, {"answer": 42}, 10)
    assert (
        len(key) == 64
        and c.get("a", {"a": 1, "b": 2}) == {"answer": 42}
        and c.get("b", req) is None
    )
    now[0] = 110
    assert c.get("a", req) is None
    with pytest.raises(ValueError):
        c.put("", req, 1, 0)


def test_webhook_sign_verify_tamper_and_invalid():
    e = SignedWebhook.build("secret", "budget.exceeded", {"spend": 12}, 100)
    assert SignedWebhook.verify("secret", e)
    e["payload"] = {"spend": 13}
    assert not SignedWebhook.verify("secret", e)
    assert not SignedWebhook.verify("secret", {})
    with pytest.raises(ValueError):
        SignedWebhook.build("", "", "", -1)


def test_anomaly_detector_spike_normal_and_edges():
    d = UsageAnomalyDetector()
    assert d.detect([10, 11, 9], 30)["anomaly"] is True
    assert d.detect([10, 10], 10)["anomaly"] is False
    with pytest.raises(ValueError):
        d.detect([1], 2)
    with pytest.raises(ValueError):
        d.detect([1, 2], -1)


def test_cost_router_constraints_tie_break_and_failure():
    r = CostAwareRouter()
    candidates = [
        {
            "model": "cheap",
            "cost": 1,
            "quality": 0.8,
            "latency_ms": 100,
            "healthy": True,
        },
        {
            "model": "bad",
            "cost": 0.1,
            "quality": 0.9,
            "latency_ms": 20,
            "healthy": False,
        },
        {
            "model": "quality",
            "cost": 2,
            "quality": 0.95,
            "latency_ms": 80,
            "healthy": True,
        },
    ]
    assert r.choose(candidates, 0.8, 120)["model"] == "cheap"
    assert r.choose(candidates, 0.9, 120)["model"] == "quality"
    with pytest.raises(ValueError):
        r.choose(candidates, 0.99, 10)
    with pytest.raises(ValueError):
        r.choose([], 0)
