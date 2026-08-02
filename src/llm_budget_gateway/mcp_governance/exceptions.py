"""Exception hierarchy for the mcp_governance module.

Normative per docs/architecture/mcp-governance.md §8. Each exception carries a
``status_code`` used by the REST layer (§8.1). Fully functional in the RED
phase — interface tests rely on the hierarchy and status codes immediately.
"""


class MCPGovernanceError(Exception):
    """Base error for the module; defaults to HTTP 400."""

    status_code = 400


class MCPServerNotFoundError(MCPGovernanceError):
    status_code = 404


class MCPToolNotFoundError(MCPGovernanceError):
    status_code = 404


class PolicyNotFoundError(MCPGovernanceError):
    status_code = 404


class BudgetNotFoundError(MCPGovernanceError):
    status_code = 404


class ApprovalNotFoundError(MCPGovernanceError):
    status_code = 404


class DuplicateServerError(MCPGovernanceError):
    status_code = 409


class DuplicatePolicyError(MCPGovernanceError):
    status_code = 409


class DuplicateBudgetError(MCPGovernanceError):
    status_code = 409


class AccessDeniedError(MCPGovernanceError):
    status_code = 403


class PolicyViolationError(MCPGovernanceError):
    status_code = 403


class ApprovalRequiredError(MCPGovernanceError):
    status_code = 409

    def __init__(self, approval_id: str, reason: str = "approval required") -> None:
        self.approval_id = approval_id
        super().__init__(reason)


class ApprovalStateError(MCPGovernanceError):
    status_code = 409


class MCPDiscoveryError(MCPGovernanceError):
    status_code = 502
