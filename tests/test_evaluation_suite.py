import pytest

from llm_budget_gateway.evaluation_suite import (
    AuditReport,
    BatchManifest,
    EvaluationStore,
    ReleaseGate,
    RuleEvaluator,
    TraceContext,
)


def test_rule_evaluator_pass_fail_and_edges():
    e = RuleEvaluator()
    good = e.evaluate(
        "Hello world",
        {"contains": ["Hello"], "forbidden": ["secret"], "max_length": 20},
    )
    assert good.passed and good.score == 1 and all(good.checks.values())
    bad = e.evaluate("secret", {"equals": "safe", "forbidden": ["secret"]})
    assert not bad.passed and bad.score == 0
    with pytest.raises(ValueError):
        e.evaluate("x", {})
    with pytest.raises(ValueError):
        e.evaluate("x", {"max_length": True})
    with pytest.raises(TypeError):
        e.evaluate(None, {})


def test_release_gate_thresholds_and_regression():
    g = ReleaseGate()
    assert g.decide([0.9, 1], 0.9, 0.1, 0.95)["passed"]
    assert not g.decide([0.7, 0.8], 0.8, 0.05, 0.9)["passed"]
    for args in (
        ([], 0.8, 0.1, None),
        ([1.1], 0.8, 0.1, None),
        ([0.9], 2, 0.1, None),
        ([0.9], 0.8, -1, None),
    ):
        with pytest.raises(ValueError):
            g.decide(*args)


def test_trace_context_priority_fallback_and_validation():
    t = TraceContext()
    assert t.resolve({"X-Gateway-Trace-Id": "trace_123"})["trace_id"] == "trace_123"
    assert (
        t.resolve({"X-Gateway-Session-Id": "session_123"})["session_id"]
        == "session_123"
    )
    assert t.resolve({"X-Vendor-Session-Id": "vendor_123"})["trace_id"] == "vendor_123"
    with pytest.raises(ValueError):
        t.resolve({})
    with pytest.raises(ValueError):
        t.resolve({"X-Gateway-Trace-Id": "bad id"})


def test_batch_manifest_cost_and_validation():
    b = BatchManifest()
    out = b.build(
        [
            {"custom_id": "1", "model": "m", "estimated_cost": 2},
            {"custom_id": "2", "model": "m", "estimated_cost": 4},
        ],
        0.5,
    )
    assert out["count"] == 2 and out["estimated_cost"] == 3
    with pytest.raises(ValueError):
        b.build([])
    with pytest.raises(ValueError):
        b.build(
            [
                {"custom_id": "1", "model": "a", "estimated_cost": 1},
                {"custom_id": "2", "model": "b", "estimated_cost": 1},
            ]
        )
    with pytest.raises(ValueError):
        b.build([{"custom_id": "1", "model": "m", "estimated_cost": True}])


def test_audit_report_redaction_integrity_and_tamper():
    a = AuditReport()
    report = a.create(
        [{"control": "keys", "prompt": "drop", "detail": "key sk_abcdef123"}], 10
    )
    assert (
        report["schema"] == "audit-report.v1"
        and "drop" not in str(report)
        and "sk_abcdef123" not in str(report)
    )
    assert a.verify(report)
    report["generated_at"] = 11
    assert not a.verify(report)
    assert not a.verify({})
    with pytest.raises(ValueError):
        a.create([], -1)


def test_evaluation_store_tenant_isolation(tmp_path):
    s = EvaluationStore(str(tmp_path / "e.db"), clock=lambda: 10)
    r = RuleEvaluator().evaluate("ok", {"equals": "ok"})
    stored = s.record("t1", "smoke", r)
    assert stored["passed"] and len(s.list("t1")) == 1 and s.list("t2") == []
    with pytest.raises(ValueError):
        s.record("", "", r)
