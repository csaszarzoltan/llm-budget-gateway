import pytest

from llm_budget_gateway.platform_suite import (
    AdoptionFunnel,
    AlertRuleEvaluator,
    CanaryPlanner,
    ContractCompatibility,
    CostAllocator,
    DatasetCurator,
    DLPClassifier,
    ExportManifest,
    FeedbackAggregator,
    IncidentDigest,
    ModelCatalog,
    PromptCatalog,
    ProviderScorecard,
    QualityDriftDetector,
    QuotaPlanner,
    RegionRouter,
    RetentionPolicy,
    RollbackDecision,
    SLOCalculator,
    UsageTagger,
)


def test_twenty_capabilities_happy_paths():
    assert PromptCatalog().register("p", "1.0.0", ["prod"])["version"] == "1.0.0"
    assert (
        ModelCatalog().register("m", 1, ["chat"], True)["classification"] == "external"
    )
    assert UsageTagger().normalize({"Team": " A "}) == {"team": "A"}
    assert sum(CostAllocator().allocate(10, {"a": 1, "b": 1}).values()) == 10
    assert QuotaPlanner().plan(2, 20, 1, 10)["allowed"]
    assert AlertRuleEvaluator().evaluate(2, ">", 1)["triggered"]
    assert SLOCalculator().calculate(100, 0, 0.99)["met"]
    assert (
        IncidentDigest().summarize([{"timestamp": 1, "kind": "outage"}])[
            "duration_seconds"
        ]
        == 0
    )
    assert RetentionPolicy().expiry(0, 1, False)["expires_at"] == 86400
    assert DLPClassifier().classify("a@b.com")["blocked"]
    assert (
        RegionRouter().choose(
            [{"name": "p", "healthy": True, "region": "eu", "latency_ms": 1}], ["eu"]
        )["provider"]
        == "p"
    )
    assert ProviderScorecard().score(0, 0, 1, 1)["grade"] == "A"
    assert CanaryPlanner().plan([10, 50, 100])["count"] == 3
    assert RollbackDecision().decide(-0.1, 0, 0)["rollback"]
    assert FeedbackAggregator().aggregate([4, 5])["positive_share"] == 1
    assert QualityDriftDetector().detect([1], [0.8], 0.1)["drifted"]
    assert DatasetCurator().curate([{"x": 1}, {"x": 1}])["duplicates_removed"] == 1
    assert ExportManifest().build({"a": b"x"})["sha256"]
    assert ContractCompatibility().compare(["a"], ["a", "b"])["compatible"]
    assert AdoptionFunnel().calculate({"view": 10, "use": 5})["overall"] == 0.5


def test_twenty_capabilities_fail_closed_edges():
    cases = [
        lambda: PromptCatalog().register("", "x", []),
        lambda: ModelCatalog().register("", 0, [], False),
        lambda: UsageTagger().normalize({"bad/key": "x"}),
        lambda: CostAllocator().allocate(-1, {}),
        lambda: QuotaPlanner().plan(True, 1, 1, 1),
        lambda: AlertRuleEvaluator().evaluate(1, "=", 1),
        lambda: SLOCalculator().calculate(0, 0, 0.9),
        lambda: IncidentDigest().summarize([]),
        lambda: RetentionPolicy().expiry(-1, 0, False),
        lambda: DLPClassifier().classify(None),
        lambda: RegionRouter().choose([], ["eu"]),
        lambda: ProviderScorecard().score(-1, 0, 1, 1),
        lambda: CanaryPlanner().plan([50]),
        lambda: FeedbackAggregator().aggregate([]),
        lambda: QualityDriftDetector().detect([], [], 0.1),
        lambda: ExportManifest().build({"../x": b""}),
        lambda: AdoptionFunnel().calculate({"a": 1, "b": 2}),
    ]
    for case in cases:
        with pytest.raises((ValueError, TypeError)):
            case()
