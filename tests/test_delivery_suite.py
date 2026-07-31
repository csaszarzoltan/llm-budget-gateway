import pytest

from llm_budget_gateway import delivery_suite as s


def test_environment_readiness_reports_names_only():
    r = s.EnvironmentReadiness().evaluate(["A", "B"], ["A"])
    assert r["missing"] == ["B"] and not r["ready"]
    with pytest.raises(ValueError):
        s.EnvironmentReadiness().evaluate([], [])


def test_configuration_drift_is_sorted_redacted_and_deterministic():
    a = s.ConfigurationDrift().compare({"b": 1, "a": 1}, {"b": 2, "a": 1})
    b = s.ConfigurationDrift().compare({"a": 1, "b": 1}, {"a": 1, "b": 2})
    assert a == b and a["changed_fields"] == ["b"] and len(a["sha256"]) == 64


def test_capacity_plan_checks_both_dimensions_and_invalid_reserve():
    assert s.CapacityPlanner().plan(120, 1200, 100, 1000)["ready"]
    assert not s.CapacityPlanner().plan(110, 1200, 100, 1000)["ready"]
    with pytest.raises(ValueError):
        s.CapacityPlanner().plan(1, 1, 1, 1, 2)


def test_dependency_health_blocks_only_required_down_dependencies():
    r = s.HealthDependencyPolicy().evaluate(
        [
            {"name": "db", "status": "down", "required": True},
            {"name": "alerts", "status": "down", "required": False},
        ]
    )
    assert (
        r["state"] == "down" and r["blocking"] == ["db"] and r["degraded"] == ["alerts"]
    )
    with pytest.raises(ValueError):
        s.HealthDependencyPolicy().evaluate([])


def test_rollout_plan_requires_increasing_stages_ending_at_100():
    assert s.RolloutPlanner().build([5, 25, 100], 10)["total_observation_minutes"] == 30
    with pytest.raises(ValueError):
        s.RolloutPlanner().build([25, 10, 100], 10)


def test_rollback_decision_names_each_breached_guardrail():
    r = s.RollbackDecision().decide(0.2, 0.01, 0.3, 0.1, 0.02, 0.2)
    assert r == {"rollback": True, "reasons": ["quality_drop", "latency_increase"]}
    with pytest.raises(ValueError):
        s.RollbackDecision().decide(-1, 0, 0, 0, 0, 0)


def test_observability_coverage_requires_every_signal():
    r = s.ObservabilityCoverage().assess(
        ["logs", "metrics", "traces"], ["logs", "metrics"]
    )
    assert r["coverage"] == pytest.approx(2 / 3) and r["missing"] == ["traces"]


def test_alert_routes_require_signed_webhooks():
    assert not s.AlertRouteValidator().validate(
        [{"severity": "high", "channel": "webhook", "signed": False}]
    )["valid"]
    assert s.AlertRouteValidator().validate(
        [{"severity": "high", "channel": "webhook", "signed": True}]
    )["valid"]


def test_runbook_coverage_requires_owner_and_steps():
    r = s.RunbookCoverage().assess(
        ["timeout", "budget"],
        [{"failure_mode": "timeout", "owner": "sre", "steps": ["retry"]}],
    )
    assert r["missing"] == ["budget"]


def test_release_manifest_is_order_independent_and_validates_semver():
    x = s.ReleaseManifest().build("6.0.0", {"b": "2", "a": "1"})
    y = s.ReleaseManifest().build("6.0.0", {"a": "1", "b": "2"})
    assert x == y
    with pytest.raises(ValueError):
        s.ReleaseManifest().build("6", {"a": "1"})
