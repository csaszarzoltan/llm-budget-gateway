import pytest

from llm_budget_gateway.security_suite import (
    ChangeRiskAssessor,
    ProviderCompliancePolicy,
    ReplayProtector,
    SecretScanner,
    SecurityPosture,
    integrity_digest,
)


def test_secret_scanner_redacts_and_validates():
    r = SecretScanner().scan("Bearer abcdefghijklmnop sk_abcdefghijk token=abcdefghijk")
    assert r.count == 3 and "abcdefghijk" not in r.text
    assert SecretScanner().scan("").count == 0
    with pytest.raises(TypeError):
        SecretScanner().scan(None)


def test_replay_ttl_and_tenants(tmp_path):
    now = [10]
    r = ReplayProtector(str(tmp_path / "r.db"), lambda: now[0])
    assert r.reserve("a", "e", 5)["accepted"]
    assert r.reserve("a", "e", 5)["duplicate"]
    assert r.reserve("b", "e", 5)["accepted"]
    now[0] = 15
    assert r.reserve("a", "e", 5)["accepted"]
    with pytest.raises(ValueError):
        r.reserve("", "", "0")


def test_provider_compliance_fail_closed():
    p = ProviderCompliancePolicy()
    ok = p.evaluate(
        {
            "name": "p",
            "certifications": ["soc2"],
            "no_training": True,
            "regions": ["eu"],
        },
        {"certifications": ["SOC2"], "no_training": True, "allowed_regions": ["eu"]},
    )
    assert ok["allowed"]
    denied = p.evaluate({"name": "p"}, {"gdpr": True, "no_logging": True})
    assert not denied["allowed"] and len(denied["missing"]) == 2
    with pytest.raises(ValueError):
        p.evaluate({}, {})


def test_change_risk_and_validation():
    r = ChangeRiskAssessor()
    assert r.assess(["auth", "routing"])["approvals_required"] == 2
    assert r.assess(["docs"], False)["can_auto_apply"]
    with pytest.raises(ValueError):
        r.assess([])
    with pytest.raises(ValueError):
        r.assess(["unknown"])


def test_posture_and_digest():
    p = SecurityPosture()
    a = p.evaluate({"auth_configured": True})
    assert a["grade"] == "F" and len(a["missing"]) == 5
    assert p.evaluate({x: True for x in p._controls})["grade"] == "A"
    assert integrity_digest({"b": 2, "a": 1}) == integrity_digest({"a": 1, "b": 2})
    with pytest.raises(TypeError):
        p.evaluate(None)
