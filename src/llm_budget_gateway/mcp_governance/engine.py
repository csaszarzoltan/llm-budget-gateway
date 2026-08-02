"""Policy engine gating MCP tool calls before/after execution.

Normative per docs/architecture/mcp-governance.md §6.6. CallContext is a
fully functional dataclass in the RED phase; MCPPolicyEngine.__init__ stores
its dependencies and every behavioral method raises NotImplementedError.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .schemas import AuditEvent

if TYPE_CHECKING:
    from collections.abc import Callable

    from llm_budget_gateway.budget_enforcement import BudgetScope

    from .audit import AuditStore
    from .budgets import ToolBudgetService
    from .integration import ApprovalNotifier
    from .policy import ToolPolicyStore
    from .registry import MCPRegistry
    from .rules import ApprovalStore, PIIRedactor, SSRFGuard


@dataclass
class CallContext:
    """Everything after_call needs to record the outcome of a gated call."""

    call_id: str
    request_id: str | None
    caller: str
    scopes: list["BudgetScope"]
    server_id: str
    tool_name: str
    args_redacted: dict[str, Any]
    decision: str  # "allowed" | "approved"
    policy_id: str | None
    approval_id: str | None
    reason: str | None


class MCPPolicyEngine:
    """Gate a tool call BEFORE execution, record the outcome AFTER."""

    def __init__(
        self,
        *,
        registry: "MCPRegistry",
        policies: "ToolPolicyStore",
        budgets: "ToolBudgetService",
        audit: "AuditStore",
        approvals: "ApprovalStore",
        redactor: "PIIRedactor",
        ssrf: "SSRFGuard",
        notifier: "ApprovalNotifier | None" = None,
        request_id_factory: "Callable[[], str] | None" = None,
    ) -> None:
        self._registry = registry
        self._policies = policies
        self._budgets = budgets
        self._audit = audit
        self._approvals = approvals
        self._redactor = redactor
        self._ssrf = ssrf
        self._notifier = notifier
        self._request_id_factory = request_id_factory

    async def before_call(
        self,
        *,
        caller: str,
        scopes: list["BudgetScope"],
        server_id: str,
        tool_name: str,
        args: Any,
        request_id: str | None = None,
    ) -> CallContext:
        """Gate a tool call BEFORE execution (RED stub)."""
        raise NotImplementedError

    async def after_call(
        self, ctx: CallContext, *, status: str, cost: float, latency_ms: int
    ) -> AuditEvent:
        """Record the outcome AFTER execution (RED stub)."""
        raise NotImplementedError
