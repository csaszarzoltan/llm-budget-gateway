"""Assurance Center hook and approval notifier.

Normative per docs/architecture/mcp-governance.md §6.8. The report methods and
the no-op notifier body are implemented here.
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
        """No-op: the extension point for Slack/webhook/automation hooks."""
        return None


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
        servers = registry.list_servers(include_retired=True)
        active_servers = [s for s in servers if s.status == "active"]
        total_servers = len(servers)
        total_tools = 0
        covered_by_policy: set[tuple[str, str]] = set()
        covered_by_budget: set[tuple[str, str]] = set()
        for server in active_servers:
            tools = registry.list_tools(server.server_id)
            total_tools += len(tools)
            for tool in tools:
                key = (server.server_id, tool.name)
                if _covered(policies.list_policies(), server.server_id, tool.name):
                    covered_by_policy.add(key)
                if _covered(budgets.list_budgets(), server.server_id, tool.name):
                    covered_by_budget.add(key)
        pending = len(approvals.list(status="pending"))
        audit_page = audit.query(since=since_epoch)
        ssrf_blocks = sum(
            1
            for e in audit_page.data
            if e.decision == "denied" and e.reason and e.reason.startswith("ssrf:")
        )
        pii_redactions = sum(1 for e in audit_page.data if e.redacted)
        budget_breaches = sum(
            1
            for e in audit_page.data
            if e.decision == "denied" and e.reason and e.reason.startswith("budget")
        )
        tools_with_policy = len(covered_by_policy)
        tools_with_budget = len(covered_by_budget)
        if pending > 0 or ssrf_blocks > 0:
            risk_tier = "high"
        elif tools_with_policy < total_tools or total_tools == 0:
            risk_tier = "medium"
        else:
            risk_tier = "low"
        return {
            "total_servers": total_servers,
            "active_servers": len(active_servers),
            "retired_servers": total_servers - len(active_servers),
            "total_tools": total_tools,
            "tools_with_policy": tools_with_policy,
            "tools_with_budget": tools_with_budget,
            "pending_approvals": pending,
            "ssrf_blocks_24h": ssrf_blocks,
            "pii_redactions_24h": pii_redactions,
            "budget_breaches_24h": budget_breaches,
            "risk_tier": risk_tier,
        }

    def assess(self, report: Mapping[str, object]) -> dict[str, object]:
        """Pure-function assessment: risk_tier / gaps / recommendation (RED stub)."""
        total_tools = int(report.get("total_tools", 0) or 0)
        tools_with_policy = int(report.get("tools_with_policy", 0) or 0)
        tools_with_budget = int(report.get("tools_with_budget", 0) or 0)
        gaps: list[str] = []
        if int(report.get("pending_approvals", 0) or 0) > 0:
            gaps.append("pending approvals")
        if int(report.get("ssrf_blocks_24h", 0) or 0) > 0:
            gaps.append("ssrf blocks in window")
        if tools_with_policy < total_tools:
            gaps.append("ungoverned tools")
        if tools_with_budget < total_tools:
            gaps.append("tools without budget ceilings")
        if int(report.get("total_servers", 0) or 0) == 0:
            gaps.append("no servers registered")
        if gaps:
            recommendation = "Address: " + "; ".join(gaps)
        else:
            recommendation = "No action required"
        return {
            "risk_tier": report.get("risk_tier", "low"),
            "gaps": gaps,
            "recommendation": recommendation,
        }


def _covered(
    selectors: list[object], server_id: str, tool_name: str
) -> bool:
    """True iff any policy/budget selector covers the (server, tool) pair."""
    for item in selectors:
        item_server = getattr(item, "server_id", None)
        item_tool = getattr(item, "tool_name", None)
        if item_server is not None and item_server != server_id:
            continue
        if item_tool is not None and item_tool != tool_name:
            continue
        return True
    return False
