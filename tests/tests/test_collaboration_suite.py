import pytest

from llm_budget_gateway.collaboration_suite import (
    ApprovalDelegation,
    InvitationService,
    KeyLifecycle,
    MemberBudget,
    RolePolicy,
)


def test_roles():
    r = RolePolicy()
    assert r.authorize("admin", "members:write", ["*"], "p")["allowed"]
    assert not r.authorize("developer", "members:write", ["p"], "p")["allowed"]
    assert not r.authorize("viewer", "usage:read", ["a"], "b")["allowed"]
    with pytest.raises(ValueError):
        r.authorize("bad", "x", [])


def test_invites(tmp_path):
    now = [1]
    s = InvitationService(str(tmp_path / "i.db"), lambda: now[0])
    x = s.issue("t", "A@B.COM", "developer", 10)
    assert s.accept(x["token"])["email"] == "a@b.com"
    with pytest.raises(ValueError):
        s.accept(x["token"])
    y = s.issue("t", "c@d.com", "viewer", 1)
    now[0] = 2
    with pytest.raises(ValueError):
        s.accept(y["token"])


def test_key_lifecycle():
    k = KeyLifecycle()
    assert k.evaluate(0, 0, 91 * 86400)["action"] == "rotate"
    assert (
        k.evaluate(0, 0, 40 * 86400, max_age_days=90, idle_days=30)["action"]
        == "revoke"
    )
    assert k.evaluate(0, 0, 1)["action"] == "keep"
    with pytest.raises(ValueError):
        k.evaluate(5, None, 4)


def test_member_budget():
    b = MemberBudget()
    x = b.evaluate(5, 2, 10, 1, 2)
    assert x["request_allowed"] and x["key_creation_allowed"]
    assert not b.evaluate(9, 2, 10, 2, 2)["request_allowed"]
    with pytest.raises(ValueError):
        b.evaluate(-1, 0, 1, 0, 1)


def test_delegation():
    a = ApprovalDelegation()
    d = [{"owner": "o", "delegate": "a", "starts": 1, "expires": 5}]
    assert a.decide("r", "a", d, 2)["allowed"]
    assert not a.decide("r", "a", d, 5)["allowed"]
    with pytest.raises(ValueError):
        a.decide("a", "a", d, 2)
