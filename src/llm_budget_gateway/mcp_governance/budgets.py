"""Per-tool cost ceilings enforced against the shared cost ledger.

Normative per docs/architecture/mcp-governance.md §6.3. Constructors are
functional in the RED phase (mcp_budgets table creation); every behavioral
method raises NotImplementedError until the implementer lands it. The service
reuses BudgetScope, BudgetExceededError and the window semantics of the
existing budget enforcement engine.
"""

import sqlite3
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from .schemas import AuditEvent, ToolBudget, ToolBudgetRequest

if TYPE_CHECKING:
    from llm_budget_gateway.budget_enforcement import BudgetScope
    from llm_budget_gateway.cost_tracking import CostTracker

_CREATE_BUDGETS = """
CREATE TABLE IF NOT EXISTS mcp_budgets (
    budget_id   TEXT PRIMARY KEY,
    scope_kind  TEXT NOT NULL,
    scope_key   TEXT NOT NULL,
    server_id   TEXT,
    tool_name   TEXT,
    soft_limit  REAL,
    hard_limit  REAL,
    window      TEXT NOT NULL DEFAULT '30d',
    created_at  INTEGER NOT NULL
)
"""


class ToolBudgetStore:
    """Per-tool budget definitions keyed by scope + tool selector."""

    def __init__(
        self, conn: sqlite3.Connection, clock: Callable[[], int] | None = None
    ) -> None:
        """Creates the mcp_budgets table."""
        self._conn = conn
        self._clock = clock if clock is not None else (lambda: int(time.time()))
        conn.execute(_CREATE_BUDGETS)
        conn.commit()

    def create_budget(self, request: ToolBudgetRequest) -> ToolBudget:
        """Insert a budget; duplicate 4-tuple raises (RED stub)."""
        raise NotImplementedError

    def list_budgets(
        self,
        *,
        scope_kind: str | None = None,
        scope_key: str | None = None,
        server_id: str | None = None,
        tool_name: str | None = None,
    ) -> list[ToolBudget]:
        """Exact-equality filters; created_at asc, budget_id asc (RED stub)."""
        raise NotImplementedError

    def get_budget(self, budget_id: str) -> ToolBudget:
        """Unknown id raises BudgetNotFoundError (RED stub)."""
        raise NotImplementedError

    def delete_budget(self, budget_id: str) -> None:
        """Unknown id raises BudgetNotFoundError (RED stub)."""
        raise NotImplementedError


class ToolBudgetService:
    """Per-tool cost ceilings, enforced against the shared cost ledger."""

    def __init__(
        self,
        tracker: "CostTracker",
        budgets: ToolBudgetStore,
        now_fn: Callable[[], int] | None = None,
    ) -> None:
        self._tracker = tracker
        self._budgets = budgets
        self._now_fn = now_fn if now_fn is not None else (lambda: int(time.time()))

    def applicable_budgets(
        self, scopes: list["BudgetScope"], server_id: str, tool_name: str
    ) -> list[ToolBudget]:
        """Budgets whose scope + tool selector match (RED stub)."""
        raise NotImplementedError

    async def check(
        self, scopes: list["BudgetScope"], server_id: str, tool_name: str
    ) -> None:
        """Raise BudgetExceededError when spend >= hard_limit (RED stub)."""
        raise NotImplementedError

    async def soft_exceeded(
        self, scopes: list["BudgetScope"], server_id: str, tool_name: str
    ) -> list["BudgetScope"]:
        """Return scopes whose soft limit is exceeded; never raises (RED stub)."""
        raise NotImplementedError

    async def record_usage(self, *, event: AuditEvent) -> None:
        """Persist tool-call cost attribution to the ledger (RED stub)."""
        raise NotImplementedError

    def canonical_tool(self, server_id: str, tool_name: str) -> str:
        """Return f"{server_id}:{tool_name}" — the ledger tool_name key (RED stub)."""
        raise NotImplementedError
