import pytest

from llm_budget_gateway import assurance_suite as s


def test_twenty_capabilities():
    assert s.RiskTier().classify(5, 5, True)["tier"] == "critical"
    assert s.ControlTest().evaluate(95, 100)["effective"]
    assert s.EvaluationGate().decide({"q": 1}, {"q": 0.9})["allowed"]
    assert s.CalibrationMetric().calculate([1], [True])["error"] == 0
    assert s.RefusalQuality().calculate([True], [True])["accuracy"] == 1
    assert s.FairnessGap().calculate({"a": 0.9, "b": 0.85})["acceptable"]
    assert s.RobustnessScore().calculate(1, 0.8)["retention"] == 0.8
    assert s.HallucinationRate().calculate(1, 10)["rate"] == 0.1
    assert s.ProvenanceRecord().build("m", "p", "d", "x")["digest"]
    assert s.ChangeApproval().decide("high", ["a", "b"])["allowed"]
    assert s.IncidentSeverity().classify(1000, True, 50000)["severity"] == "P1"
    assert s.CorrectiveAction().status(1, 2, 1)["escalate"]
    assert s.VendorRisk().assess(5, 5, 5)["approved"]
    assert s.DataQuality().calculate(1, 1, 1)["ready"]
    assert s.DriftAlert().detect(1, 0.5, 0.1)["alert"]
    assert s.RedTeamCoverage().calculate(["a"], ["a"])["coverage"] == 1
    assert s.EvidenceFreshness().evaluate(0, 1, 2)["fresh"]
    assert s.MaturityScore().calculate({"a": 5})["level"] == "optimized"
    assert s.AssuranceReport().build([{"a": 1}])["digest"]
    assert s.BenefitRealization().calculate(10, 12, 2)["net_value"] == 10


def test_negative_edges():
    cases = [
        lambda: s.RiskTier().classify(0, 0, False),
        lambda: s.ControlTest().evaluate(2, 1),
        lambda: s.EvaluationGate().decide({"a": 1}, {}),
        lambda: s.CalibrationMetric().calculate([], []),
        lambda: s.RefusalQuality().calculate([], []),
        lambda: s.FairnessGap().calculate({"a": 1}),
        lambda: s.RobustnessScore().calculate(0, 0),
        lambda: s.HallucinationRate().calculate(2, 1),
        lambda: s.ProvenanceRecord().build("", "", "", ""),
        lambda: s.ChangeApproval().decide("x", []),
        lambda: s.IncidentSeverity().classify(-1, False, 0),
        lambda: s.CorrectiveAction().status(2, 1, 0),
        lambda: s.VendorRisk().assess(0, 1, 1),
        lambda: s.DataQuality().calculate(2, 1, 1),
        lambda: s.DriftAlert().detect(1, 1, -1),
        lambda: s.EvidenceFreshness().evaluate(-1, 0, 0),
        lambda: s.MaturityScore().calculate({}),
        lambda: s.BenefitRealization().calculate(0, 0, 0),
    ]
    for c in cases:
        with pytest.raises((ValueError, TypeError)):
            c()
