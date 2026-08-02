"""Package-level interface tests for llm_budget_gateway.mcp_governance.

Verifies the public API surface per docs/architecture/mcp-governance.md §2.1
(exports) and §8 (exception hierarchy). These must pass immediately in the RED
phase — the package is importable and the exceptions are fully functional.
"""

import importlib

import pytest

import llm_budget_gateway.mcp_governance as mg

PUBLIC_NAMES = [
    "AccessDeniedError",
    "ApprovalGate",
    "ApprovalNotFoundError",
    "ApprovalNotifier",
    "ApprovalRequest",
    "ApprovalRequiredError",
    "ApprovalStateError",
    "AuditEvent",
    "AuditPage",
    "AuditStore",
    "BudgetNotFoundError",
    "CallContext",
    "DuplicateBudgetError",
    "DuplicatePolicyError",
    "DuplicateServerError",
    "MCPDiscoveryAdapter",
    "MCPDiscoveryError",
    "MCPGovernanceError",
    "MCPGovernanceReport",
    "MCPPolicyEngine",
    "MCPRegistry",
    "MCPRegistryRequest",
    "MCPServer",
    "MCPServerNotFoundError",
    "MCPToolNotFoundError",
    "NullApprovalNotifier",
    "PIIRedactor",
    "PolicyDecision",
    "PolicyEvaluator",
    "PolicyNotFoundError",
    "PolicyViolationError",
    "RuleVerdict",
    "SSRFGuard",
    "ToolBudget",
    "ToolBudgetRequest",
    "ToolBudgetService",
    "ToolBudgetStore",
    "ToolInfo",
    "ToolPolicy",
    "ToolPolicyRequest",
    "ToolPolicyStore",
    "create_mcp_governance_app",
    "open_mcp_db",
]

MODULES = [
    "exceptions",
    "schemas",
    "db",
    "registry",
    "policy",
    "budgets",
    "audit",
    "rules",
    "engine",
    "discovery",
    "integration",
    "api",
]


class TestPackageExports:
    def test_module_importable(self):
        assert mg.__name__ == "llm_budget_gateway.mcp_governance"

    def test_all_public_names_exist(self):
        missing = [n for n in PUBLIC_NAMES if not hasattr(mg, n)]
        assert missing == []

    def test_all_names_are_in_all(self):
        assert set(PUBLIC_NAMES) <= set(mg.__all__)

    def test_all_has_no_stale_names(self):
        assert set(mg.__all__) <= set(PUBLIC_NAMES)

    @pytest.mark.parametrize("mod", MODULES)
    def test_each_module_importable(self, mod):
        module = importlib.import_module(f"llm_budget_gateway.mcp_governance.{mod}")
        assert module is not None

    def test_create_app_is_callable(self):
        assert callable(mg.create_mcp_governance_app)

    def test_open_db_is_callable(self):
        assert callable(mg.open_mcp_db)

    def test_approval_store_importable_from_rules(self):
        """ApprovalStore is public via .rules (spec §2 layout), not package-level (§2.1)."""
        from llm_budget_gateway.mcp_governance.rules import ApprovalStore

        assert ApprovalStore is not None
        assert not hasattr(mg, "ApprovalStore")


class TestExceptionHierarchy:
    @pytest.mark.parametrize(
        "cls, status",
        [
            (mg.MCPServerNotFoundError, 404),
            (mg.MCPToolNotFoundError, 404),
            (mg.PolicyNotFoundError, 404),
            (mg.BudgetNotFoundError, 404),
            (mg.ApprovalNotFoundError, 404),
            (mg.DuplicateServerError, 409),
            (mg.DuplicatePolicyError, 409),
            (mg.DuplicateBudgetError, 409),
            (mg.AccessDeniedError, 403),
            (mg.PolicyViolationError, 403),
            (mg.ApprovalRequiredError, 409),
            (mg.ApprovalStateError, 409),
            (mg.MCPDiscoveryError, 502),
        ],
    )
    def test_status_codes(self, cls, status):
        assert issubclass(cls, mg.MCPGovernanceError)
        assert cls.status_code == status

    def test_base_status_400(self):
        assert mg.MCPGovernanceError.status_code == 400

    def test_approval_required_error_carries_id(self):
        err = mg.ApprovalRequiredError("aprv1", "approval required")
        assert err.approval_id == "aprv1"
        assert str(err) == "approval required"
        assert err.status_code == 409

    def test_approval_required_error_default_reason(self):
        err = mg.ApprovalRequiredError("aprv2")
        assert err.approval_id == "aprv2"
        assert str(err) == "approval required"

    def test_all_exceptions_are_raiseable(self):
        for cls in [mg.AccessDeniedError, mg.DuplicateServerError, mg.PolicyViolationError]:
            with pytest.raises(cls):
                raise cls("boom")
