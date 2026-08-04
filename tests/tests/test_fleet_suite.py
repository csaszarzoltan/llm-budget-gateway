# ruff: noqa: F403,F405
import pytest

from llm_budget_gateway.fleet_suite import *  # noqa:F403


def test_all_capabilities():
    assert AgentIdentityCard().issue("agent-1", "o", "p", 1)["fingerprint"]
    assert AgentInventory().summarize([{"id": "a", "owner": "", "sanctioned": False}])[
        "shadow"
    ] == ["a"]
    assert LifecyclePolicy().transition("draft", "active")["allowed"]
    assert CredentialExpiry().evaluate(10, 9, 2)["action"] == "renew"
    assert CapabilityGrant().decide("read", ["read"], "x", ["x"], 2, 1)["allowed"]
    assert PlatformAuthorization().decide("p", ["p"], "1", {"p": "1"})["allowed"]
    assert not KillSwitch().decide("a", "t", ["organization"])["allowed"]
    assert PolicySimulation().compare([True], [False])["newly_blocked"] == [0]
    assert BlastRadiusEstimator().estimate(1, 1, 1)["tier"] == "low"
    assert HumanResponsibility().resolve("a", "w", None)["accountable"] == "w"
    assert EvidenceBundle().build({"a": "x"})["digest"]
    assert PolicyCoverage().calculate(1, 1, 1)["governance_coverage"] == 1
    assert ShadowAgentDetector().detect(["a"], [])["unknown"] == ["a"]
    assert CostCeiling().decide(1, 1, 2)["allowed"]
    assert RunawayDetector().detect(
        2, 0, 0, {"steps": 1, "retries": 1, "repeated_tool_calls": 1}
    )["runaway"]
    assert OutcomeEconomics().calculate(2, 2, 3)["cost_per_outcome"] == 1
    assert (
        ModelTierPolicy().choose(0.5, [{"name": "s", "max_complexity": 1, "cost": 1}])[
            "tier"
        ]
        == "s"
    )
    assert ToolCostLedger().aggregate([{"tool": "x", "cost": 1}])["total"] == 1
    assert DataReadiness().assess(1, 1, 1)["ready"]
    assert ReproducibilityRecord().build("1", "m", ["t"], "1")["fingerprint"]
    assert ComplianceCrosswalk().evaluate({"r": ["c"]}, ["c"])["compliant"]


def test_negative_edges():
    cases = [
        lambda: AgentIdentityCard().issue("x", "", "", 0),
        lambda: AgentInventory().summarize([]),
        lambda: LifecyclePolicy().transition("retired", "active"),
        lambda: CredentialExpiry().evaluate(True, 0, 0),
        lambda: PolicySimulation().compare([True], []),
        lambda: BlastRadiusEstimator().estimate(0, 0, 6),
        lambda: HumanResponsibility().resolve("", "", None),
        lambda: EvidenceBundle().build({"../x": "a"}),
        lambda: PolicyCoverage().calculate(0, 0, 0),
        lambda: CostCeiling().decide(-1, 0, 0),
        lambda: RunawayDetector().detect(1, 1, 1, {}),
        lambda: OutcomeEconomics().calculate(1, 0, 1),
        lambda: ModelTierPolicy().choose(2, []),
        lambda: ToolCostLedger().aggregate([{"tool": "", "cost": 1}]),
        lambda: DataReadiness().assess(2, 1, 1),
        lambda: ReproducibilityRecord().build("", "", "", ""),
    ]
    for case in cases:
        with pytest.raises((ValueError, TypeError)):
            case()
