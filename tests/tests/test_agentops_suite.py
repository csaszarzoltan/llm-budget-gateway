import hashlib
import hmac

import pytest

from llm_budget_gateway.agentops_suite import (
    AuditChain,
    CarbonEstimator,
    ChangeRiskAssessor,
    CircuitBreakerPolicy,
    DelegationDepthPolicy,
    HumanApprovalGate,
    InjectionRiskScorer,
    LocaleNegotiator,
    MCPServerRegistry,
    ReplayProtector,
    ResidencyPolicy,
    SemanticCacheKey,
    SensitiveDataRedactor,
    SessionAffinity,
    SupportTriage,
    TaskCostMeter,
    TaskLease,
    TokenDensityMetric,
    ToolAccessPolicy,
    TraceSampler,
)


def test_twenty_agentops_capabilities():
    assert MCPServerRegistry().register("m", "https://x", ["b", "a"])["tools"] == [
        "a",
        "b",
    ]
    assert ToolAccessPolicy().decide("a", ["a"], [])["allowed"]
    assert DelegationDepthPolicy().evaluate(1, 2)["allowed"]
    assert TaskLease().evaluate("a", "b", 1, 2)["claimable"]
    body = b"x"
    sig = hmac.new(b"s", b"1." + body, hashlib.sha256).hexdigest()
    assert ReplayProtector().verify(body, 1, 1, sig, "s")["valid"]
    assert SessionAffinity().choose("s", ["a"])["backend"] == "a"
    assert CircuitBreakerPolicy().evaluate(2, 2, 0, 10, 5)["state"] == "half_open"
    assert SemanticCacheKey().build(" A ", "m", "n")["key"]
    assert SensitiveDataRedactor().redact("a@b.com")["findings"] == 1
    assert InjectionRiskScorer().score("ignore previous")["review_required"]
    assert not HumanApprovalGate().decide("delete", "high", None)["allowed"]
    assert AuditChain().append("", {"a": 1})["hash"]
    assert TraceSampler().decide("x", 0, True)["sampled"]
    assert TaskCostMeter().calculate(1000, 1000, 1, 1, 2)["cost"] > 0
    assert TokenDensityMetric().calculate(2, 1000)["per_1k_tokens"] == 2
    assert CarbonEstimator().estimate(2, 100)["grams_co2e"] == 200
    assert ChangeRiskAssessor().assess(1, 1, 1)["tier"] == "low"
    assert SupportTriage().prioritize(4, 500, False)["priority"] == "P1"
    assert (
        LocaleNegotiator().choose(["de-CH"], ["de-DE", "en-US"], "en-US")["locale"]
        == "de-DE"
    )
    assert not ResidencyPolicy().decide("eu", "us", [])["allowed"]


def test_fail_closed_edges():
    cases = [
        lambda: MCPServerRegistry().register("", "http://x", []),
        lambda: ToolAccessPolicy().decide("", [], []),
        lambda: DelegationDepthPolicy().evaluate(True, 1),
        lambda: TaskLease().evaluate("", "", 0, 0),
        lambda: SessionAffinity().choose("", []),
        lambda: CircuitBreakerPolicy().evaluate(0, 0, None, 0, 0),
        lambda: SemanticCacheKey().build("", "", ""),
        lambda: SensitiveDataRedactor().redact(None),
        lambda: InjectionRiskScorer().score(None),
        lambda: HumanApprovalGate().decide("", "bad", None),
        lambda: TraceSampler().decide("", 2),
        lambda: TaskCostMeter().calculate(0, 0, 0, 0, 0),
        lambda: TokenDensityMetric().calculate(1, 0),
        lambda: CarbonEstimator().estimate(-1, 1),
        lambda: ChangeRiskAssessor().assess(1, 0, 1),
        lambda: SupportTriage().prioritize(0, 0, False),
        lambda: LocaleNegotiator().choose([], ["en"], "de"),
        lambda: ResidencyPolicy().decide("", "", []),
    ]
    for case in cases:
        with pytest.raises((ValueError, TypeError)):
            case()
