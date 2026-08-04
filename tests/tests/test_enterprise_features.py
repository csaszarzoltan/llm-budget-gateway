from llm_budget_gateway.enterprise_features import EnterprisePlatform, PermissionDenied


def ep(tmp_path):
    return EnterprisePlatform(str(tmp_path / "e.db"), clock=lambda: 1700000000)


def test_four_eyes_approval_idempotent_and_expiring(tmp_path):
    e = ep(tmp_path)
    w = e.create_approval(
        "t",
        "u1",
        "deploy",
        {"route": "r"},
        approvals_required=2,
        expires_at=1700000100,
        idempotency_key="x",
    )
    assert w == e.create_approval(
        "t",
        "u1",
        "deploy",
        {"route": "r"},
        approvals_required=2,
        expires_at=1700000100,
        idempotency_key="x",
    )
    assert e.decide("t", "u2", w["id"], "approve")["state"] == "pending"
    assert e.decide("t", "u3", w["id"], "approve")["state"] == "approved"


def test_continuous_evidence_detects_missing_and_hashes(tmp_path):
    e = ep(tmp_path)
    e.define_control("t", "admin", "access_review", 86400)
    e.capture_evidence(
        "t", "admin", "access_review", {"result": "pass", "prompt": "secret"}
    )
    pack = e.evidence_report("t", "auditor")
    assert pack["sha256"] and pack["missing"] == [] and "secret" not in str(pack)


def test_scim_provision_deactivate_and_access_review(tmp_path):
    e = ep(tmp_path)
    u = e.scim_upsert("t", "scim-admin", "external-1", "a@example.test", "operator")
    assert u["active"]
    e.scim_deactivate("t", "scim-admin", "external-1")
    try:
        e.authorize("t", "external-1", "viewer")
    except PermissionDenied:
        pass
    else:
        raise AssertionError
    assert e.access_review("t", "admin")["total"] == 1


def test_quality_cost_routing_is_explainable(tmp_path):
    e = ep(tmp_path)
    d = e.choose_model(
        [
            {"name": "a", "quality": 0.9, "cost": 4, "latency": 2},
            {"name": "b", "quality": 0.8, "cost": 1, "latency": 1},
        ],
        {"quality": 0.6, "cost": 0.3, "latency": 0.1},
    )
    assert d["model"] in {"a", "b"} and d["scores"] and d["explanation"]


def test_privacy_request_export_delete_and_legal_hold(tmp_path):
    e = ep(tmp_path)
    e.store_subject_record("t", "s1", "eu", {"value": 1})
    case = e.open_privacy_request("t", "privacy", "s1", "delete", "req1")
    e.set_legal_hold("t", "privacy", "s1", True)
    assert (
        e.process_privacy_request("t", "privacy", case["id"])["state"]
        == "blocked_by_hold"
    )
    e.set_legal_hold("t", "privacy", "s1", False)
    assert e.process_privacy_request("t", "privacy", case["id"])["state"] == "completed"


def test_agent_tool_governance_budget_approval_and_dedupe(tmp_path):
    e = ep(tmp_path)
    e.define_tool_policy(
        "t", "security", "send_email", requires_approval=True, max_cost=2.0
    )
    x = e.request_tool(
        "t", "agent1", "send_email", {"to": "redacted@example.test"}, 1.0, "tool-1"
    )
    assert x["state"] == "awaiting_approval"
    e.approve_tool("t", "admin", x["id"])
    done = e.complete_tool("t", "agent1", x["id"], {"status": "sent"})
    assert done["state"] == "completed"
    assert (
        e.request_tool(
            "t", "agent1", "send_email", {"to": "redacted@example.test"}, 1.0, "tool-1"
        )["id"]
        == x["id"]
    )
