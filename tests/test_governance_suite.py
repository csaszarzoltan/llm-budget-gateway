from llm_budget_gateway.governance import GovernanceService, PermissionDenied


def svc(tmp_path):
    return GovernanceService(str(tmp_path / "g.db"), clock=lambda: 1700000000)


def test_identity_governance_and_tenant_isolation(tmp_path):
    s = svc(tmp_path)
    s.add_membership("t1", "admin", "u1", "admin")
    s.add_membership("t2", "admin", "u1", "viewer")
    assert s.authorize("t1", "u1", "admin")
    try:
        s.authorize("t2", "u1", "admin")
    except PermissionDenied:
        pass
    else:
        raise AssertionError


def test_policy_advisor_requires_human_approval(tmp_path):
    s = svc(tmp_path)
    rec = s.propose("t", "operator", "budget", {"limit": 100}, "high rejection rate")
    assert rec["state"] == "proposed"
    s.approve("t", "admin", rec["id"])
    assert s.get_recommendation("t", rec["id"])["state"] == "approved"


def test_compliance_evidence_is_stable_and_promptless(tmp_path):
    s = svc(tmp_path)
    s.record_evidence(
        "t", "admin", "access_review", {"result": "pass", "prompt": "secret text"}
    )
    pack = s.evidence_package("t", "auditor")
    assert pack["sha256"] and "secret text" not in str(pack)


def test_anomaly_forecast_is_explainable(tmp_path):
    s = svc(tmp_path)
    out = s.forecast([10, 10, 10, 40], budget=100)
    assert out["anomaly"] is True and out["explanation"] and out["remaining"] == 30


def test_reliability_autopilot_recommends_and_rolls_back(tmp_path):
    s = svc(tmp_path)
    x = s.reliability_decision("t", "operator", "route-a", failures=3)
    assert x["action"] == "shift_traffic" and x["state"] == "awaiting_approval"
    s.rollback("t", "admin", x["id"])
    assert s.activity("t", "auditor")[0]["state"] == "rolled_back"


def test_retention_residency_export_delete(tmp_path):
    s = svc(tmp_path)
    s.set_privacy_policy("t", "privacy", retention_days=30, regions=["eu"])
    s.store_record("t", "eu", "usage", {"cost": 1})
    try:
        s.store_record("t", "us", "usage", {"cost": 2})
    except ValueError:
        pass
    else:
        raise AssertionError
    assert len(s.export_tenant("t", "privacy")) == 1
    assert s.delete_expired("t", "privacy") == 0
