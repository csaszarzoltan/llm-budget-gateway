"""ToolPolicyStore + PolicyEvaluator interface and behavioral (RED) tests.

Normative per docs/architecture/mcp-governance.md §6.2. Interface tests pass
immediately; behavioral tests fail with NotImplementedError until the
implementer lands the store CRUD and the §6.2.1 resolution algorithm.
"""

import inspect

import pytest

from llm_budget_gateway.budget_enforcement import BudgetScope
from llm_budget_gateway.mcp_governance import (
    DuplicatePolicyError,
    PolicyEvaluator,
    PolicyNotFoundError,
    ToolPolicyRequest,
    ToolPolicyStore,
    open_mcp_db,
)

ALICE_SCOPES = [
    BudgetScope("user", "alice"),
    BudgetScope("team", "eng"),
    BudgetScope("project", "p1"),
    BudgetScope("global", "default"),
]


@pytest.fixture
def conn():
    c = open_mcp_db(":memory:")
    yield c
    c.close()


def policy_request(**overrides):
    base = dict(scope_kind="user", scope_key="alice", effect="allow")
    base.update(overrides)
    return ToolPolicyRequest(**base)


class TestToolPolicyStoreInterface:
    def test_constructor_accepts_conn(self, conn):
        store = ToolPolicyStore(conn)
        assert store is not None

    def test_constructor_default_effect_deny(self, conn):
        store = ToolPolicyStore(conn)
        assert store.default_effect == "deny"

    def test_constructor_custom_default_effect(self, conn):
        store = ToolPolicyStore(conn, default_effect="allow")
        assert store.default_effect == "allow"

    def test_constructor_rejects_bad_default_effect(self, conn):
        with pytest.raises(ValueError):
            ToolPolicyStore(conn, default_effect="maybe")

    def test_constructor_creates_policies_table(self, conn):
        ToolPolicyStore(conn)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mcp_policies'"
        ).fetchall()
        assert len(rows) == 1

    @pytest.mark.parametrize("method", ["create_policy", "list_policies", "get_policy", "delete_policy"])
    def test_has_method(self, method):
        assert hasattr(ToolPolicyStore, method)

    def test_list_policies_keyword_only(self):
        sig = inspect.signature(ToolPolicyStore.list_policies)
        for name in ("scope_kind", "scope_key", "server_id", "tool_name"):
            assert sig.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY

    def test_create_policy_signature(self):
        sig = inspect.signature(ToolPolicyStore.create_policy)
        assert "request" in sig.parameters


class TestPolicyEvaluatorInterface:
    def test_constructor_accepts_store(self, conn):
        store = ToolPolicyStore(conn)
        ev = PolicyEvaluator(store)
        assert ev is not None

    def test_decide_signature(self):
        sig = inspect.signature(PolicyEvaluator.decide)
        assert sig.parameters["scopes"].kind is inspect.Parameter.KEYWORD_ONLY
        assert sig.parameters["server_id"].kind is inspect.Parameter.KEYWORD_ONLY
        assert sig.parameters["tool_name"].kind is inspect.Parameter.KEYWORD_ONLY


class TestToolPolicyStoreBehavior:
    """RED-phase: every behavioral path raises NotImplementedError today."""

    def test_create_policy_returns_tool_policy(self, conn):
        store = ToolPolicyStore(conn)
        p = store.create_policy(policy_request())
        assert p.policy_id
        assert p.effect == "allow"
        assert p.created_at > 0

    def test_create_policy_duplicate_4tuple_raises(self, conn):
        store = ToolPolicyStore(conn)
        store.create_policy(policy_request(server_id="srv1", tool_name="t1"))
        with pytest.raises(DuplicatePolicyError):
            store.create_policy(policy_request(server_id="srv1", tool_name="t1"))

    def test_list_policies_returns_all(self, conn):
        store = ToolPolicyStore(conn)
        store.create_policy(policy_request())
        store.create_policy(policy_request(scope_kind="team", scope_key="eng"))
        assert len(store.list_policies()) == 2

    def test_list_policies_filters_by_scope(self, conn):
        store = ToolPolicyStore(conn)
        store.create_policy(policy_request())
        store.create_policy(policy_request(scope_kind="team", scope_key="eng"))
        assert len(store.list_policies(scope_kind="user")) == 1

    def test_get_policy_returns_policy(self, conn):
        store = ToolPolicyStore(conn)
        p = store.create_policy(policy_request())
        assert store.get_policy(p.policy_id).effect == "allow"

    def test_get_policy_unknown_raises(self, conn):
        store = ToolPolicyStore(conn)
        with pytest.raises(PolicyNotFoundError):
            store.get_policy("nope")

    def test_delete_policy_removes(self, conn):
        store = ToolPolicyStore(conn)
        p = store.create_policy(policy_request())
        store.delete_policy(p.policy_id)
        with pytest.raises(PolicyNotFoundError):
            store.get_policy(p.policy_id)

    def test_delete_policy_unknown_raises(self, conn):
        store = ToolPolicyStore(conn)
        with pytest.raises(PolicyNotFoundError):
            store.delete_policy("nope")


class TestPolicyEvaluatorBehavior:
    """RED-phase: the resolution algorithm is not implemented yet."""

    def test_default_deny_with_no_policies(self, conn):
        ev = PolicyEvaluator(ToolPolicyStore(conn))
        d = ev.decide(scopes=ALICE_SCOPES, server_id="srv1", tool_name="t1")
        assert d.effect == "deny"
        assert d.policy_id is None

    def test_user_scope_beats_team_scope(self, conn):
        store = ToolPolicyStore(conn)
        store.create_policy(policy_request(scope_kind="team", scope_key="eng", effect="deny"))
        store.create_policy(policy_request(scope_kind="user", scope_key="alice", effect="allow"))
        ev = PolicyEvaluator(store)
        d = ev.decide(scopes=ALICE_SCOPES, server_id="srv1", tool_name="t1")
        assert d.effect == "allow"

    def test_deny_beats_approval_and_allow(self, conn):
        store = ToolPolicyStore(conn)
        store.create_policy(policy_request(effect="allow"))
        store.create_policy(policy_request(server_id="srv1", tool_name="t1", effect="deny"))
        ev = PolicyEvaluator(store)
        d = ev.decide(scopes=ALICE_SCOPES, server_id="srv1", tool_name="t1")
        assert d.effect == "deny"

    def test_exact_tool_beats_wildcard(self, conn):
        store = ToolPolicyStore(conn)
        store.create_policy(policy_request(server_id="srv1", tool_name="t1", effect="allow"))
        store.create_policy(policy_request(server_id="srv1", effect="deny"))
        ev = PolicyEvaluator(store)
        d = ev.decide(scopes=ALICE_SCOPES, server_id="srv1", tool_name="t1")
        assert d.effect == "allow"

    def test_global_policy_matches_any_caller(self, conn):
        store = ToolPolicyStore(conn)
        store.create_policy(
            policy_request(scope_kind="global", scope_key="default", effect="deny")
        )
        ev = PolicyEvaluator(store)
        d = ev.decide(scopes=ALICE_SCOPES, server_id="other", tool_name="t9")
        assert d.effect == "deny"
        assert d.matched_scope is None

    def test_approval_effect_returns_approval(self, conn):
        store = ToolPolicyStore(conn)
        store.create_policy(
            policy_request(server_id="srv1", tool_name="t1", effect="approval")
        )
        ev = PolicyEvaluator(store)
        d = ev.decide(scopes=ALICE_SCOPES, server_id="srv1", tool_name="t1")
        assert d.effect == "approval"

    def test_reason_includes_policy_id(self, conn):
        store = ToolPolicyStore(conn)
        p = store.create_policy(policy_request(server_id="srv1", tool_name="t1"))
        ev = PolicyEvaluator(store)
        d = ev.decide(scopes=ALICE_SCOPES, server_id="srv1", tool_name="t1")
        assert p.policy_id in d.reason
