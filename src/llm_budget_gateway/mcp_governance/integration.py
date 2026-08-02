"""Assurance Center hook and approval notifier.

Normative per docs/architecture/mcp-governance.md §6.8. The report methods and
the notifier body raise NotImplementedError in the RED phase; the protocol and
constructors are functional.
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol

from .schemas import ApprovalRequest

if TYPE_CHECKING:
    from .audit import AuditStore
    from .budgets import ToolBudgetStore
    from .policy import ToolPolicyStore
    from .registry import MCPRegistry
    from .rules import ApprovalStore


class ApprovalNotifier(Protocol):
    """Extension point for Slack/webhook/Assurance Center automation."""

    def notify(self, approval: ApprovalRequest) -> None: ...


class NullApprovalNotifier:
    """No-op default notifier (the implementer lands the no-op body)."""

    def notify(self, approval: ApprovalRequest) -> None:
        raise NotImplementedError


class MCPGovernanceReport:
    """Deterministic governance posture snapshot for the Assurance Center."""

    def build(
        self,
        *,
        registry: "MCPRegistry",
        policies: "ToolPolicyStore",
        budgets: "ToolBudgetStore",
        audit: "AuditStore",
        approvals: "ApprovalStore",
        since_epoch: int,
    ) -> dict[str, object]:
        """Return the posture snapshot dict (RED stub)."""
        raise NotImplementedError

    def assess(self, report: Mapping[str, object]) -> dict[str, object]:
        """Pure-function assessment: risk_tier / gaps / recommendation (RED stub)."""
        raise NotImplementedError
