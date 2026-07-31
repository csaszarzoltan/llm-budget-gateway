import pytest

from llm_budget_gateway import adoption_suite as s


def test_activation_funnel_identifies_dropoff_and_invalid_growth():
    r = s.ActivationFunnel().calculate({"visit": 100, "setup": 60, "request": 30})
    assert r["largest_dropoff"] == "setup" and r["completion"] == 0.3
    with pytest.raises(ValueError):
        s.ActivationFunnel().calculate({"a": 1, "b": 2})


def test_cohort_retention_calculates_churn_and_rejects_overcount():
    assert s.CohortRetention().calculate(10, 7)["retention"] == 0.7
    with pytest.raises(ValueError):
        s.CohortRetention().calculate(3, 4)


def test_feature_adoption_ranks_rates_and_unused():
    r = s.FeatureAdoption().summarize(10, {"budget": 8, "quality": 0})
    assert list(r["rates"])[0] == "budget" and r["unused"] == ["quality"]
    with pytest.raises(ValueError):
        s.FeatureAdoption().summarize(2, {"x": 3})


def test_experiment_assignment_is_stable_and_weighted():
    a = s.ExperimentAssignment().assign("onboarding", "tenant", {"a": 1, "b": 3})
    assert a == s.ExperimentAssignment().assign(
        "onboarding", "tenant", {"a": 1, "b": 3}
    )


def test_experiment_outcome_applies_regression_guardrail():
    r = s.ExperimentOutcome().evaluate(100, 40, 100, 39, 0.02)
    assert r["ship"] and r["absolute_lift"] == pytest.approx(-0.01)
    with pytest.raises(ValueError):
        s.ExperimentOutcome().evaluate(1, 2, 1, 1)


def test_feedback_themes_are_bounded_and_do_not_store_comments():
    assert s.FeedbackTheme().aggregate(["ui", "ui", "cost"])["top"] == "ui"
    with pytest.raises(ValueError):
        s.FeedbackTheme().aggregate(["raw-comment"])


def test_pricing_signal_validates_ordered_price_points():
    rows = [{"too_cheap": 1, "cheap": 2, "expensive": 5, "too_expensive": 9}]
    assert s.PricingSignal().summarize(rows)["acceptable_range"] == [2, 5]
    with pytest.raises(ValueError):
        s.PricingSignal().summarize(
            [{"too_cheap": 3, "cheap": 2, "expensive": 5, "too_expensive": 9}]
        )


def test_rollout_cohort_is_stable_and_validates_percentage():
    a = s.RolloutCohort().decide("tenant", 25)
    assert a == s.RolloutCohort().decide("tenant", 25)
    with pytest.raises(ValueError):
        s.RolloutCohort().decide("tenant", 101)


def test_success_threshold_reports_minimum_and_maximum_failures():
    r = s.SuccessThreshold().evaluate(
        {"activation": 0.4, "false_blocks": 0.03},
        {"activation": 0.45},
        {"false_blocks": 0.02},
    )
    assert len(r["failures"]) == 2


def test_adoption_report_is_canonical_and_integrity_protected():
    a = s.AdoptionReport().build("2026-Q3", {"b": 2, "a": 1}, ["ship", "learn"])
    b = s.AdoptionReport().build("2026-Q3", {"a": 1, "b": 2}, ["learn", "ship"])
    assert a == b and len(a["sha256"]) == 64
