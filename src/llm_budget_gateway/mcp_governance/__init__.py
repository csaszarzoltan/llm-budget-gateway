"""MCP server governance: registry, per-tool policies, budgets, audit, REST API.

RED-phase stub package per docs/architecture/mcp-governance.md. Constructors,
schemas, exceptions and db helpers are functional so interface tests pass
immediately; behavioral methods are implemented and validated while the
implementer lands the feature.
"""

from .api import create_mcp_governance_app
from .audit import AuditStore
from .budgets import ToolBudgetService, ToolBudgetStore
from .db import open_mcp_db
from .discovery import MCPDiscoveryAdapter
from .engine import CallContext, MCPPolicyEngine
from .exceptions import (
    AccessDeniedError,
    ApprovalNotFoundError,
    ApprovalRequiredError,
    ApprovalStateError,
    BudgetNotFoundError,
    DuplicateBudgetError,
    DuplicatePolicyError,
    DuplicateServerError,
    InvalidArgumentsError,
    MCPDiscoveryError,
    MCPGovernanceError,
    MCPServerNotFoundError,
    MCPToolNotFoundError,
    PolicyNotFoundError,
    PolicyViolationError,
)
from .integration import ApprovalNotifier, MCPGovernanceReport, NullApprovalNotifier
from .policy import PolicyEvaluator, ToolPolicyStore
from .registry import MCPRegistry
from .rules import ApprovalGate, PIIRedactor, SSRFGuard
from .schemas import (
    ApprovalRequest,
    AuditEvent,
    AuditPage,
    MCPRegistryRequest,
    MCPServer,
    PolicyDecision,
    RuleVerdict,
    ToolBudget,
    ToolBudgetRequest,
    ToolInfo,
    ToolPolicy,
    ToolPolicyRequest,
)

__all__ = [
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
    "InvalidArgumentsError",
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
