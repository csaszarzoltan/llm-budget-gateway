"""Per-tool cost ceilings enforced against the shared cost ledger.

Normative per docs/architecture/mcp-governance.md §6.3. Constructors are
functional in the RED phase (mcp_budgets table creation); the behavioral
methods are implemented here. The service reuses BudgetScope,
BudgetExceededError and the window semantics of the existing budget
enforcement engine.
"""

import inspect
import secrets
import sqlite3
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from llm_budget_gateway.budget_enforcement import (
    BudgetExceededError,
    BudgetScope,
    budget_window_seconds,
)
from llm_budget_gateway.cost_tracking import UsageRecord

from .exceptions import BudgetNotFoundError, DuplicateBudgetError
from .schemas import AuditEvent, ToolBudget, ToolBudgetRequest

if TYPE_CHECKING:
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


def _row_to_budget(row: sqlite3.Row) -> ToolBudget:
    """Map a mcp_budgets row to a ToolBudget model."""
    return ToolBudget(
        budget_id=row["budget_id"],
        scope_kind=row["scope_kind"],
        scope_key=row["scope_key"],
        server_id=row["server_id"],
        tool_name=row["tool_name"],
        soft_limit=row["soft_limit"],
        hard_limit=row["hard_limit"],
        window=row["window"],
        created_at=row["created_at"],
    )


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
        existing = self._conn.execute(
            """
            SELECT 1 FROM mcp_budgets
            WHERE scope_kind = ? AND scope_key = ?
              AND server_id IS ? AND tool_name IS ?
            """,
            (
                request.scope_kind,
                request.scope_key,
                request.server_id,
                request.tool_name,
            ),
        ).fetchone()
        if existing is not None:
            raise DuplicateBudgetError(
                "budget already exists for "
                f"{request.scope_kind}:{request.scope_key} "
                f"server={request.server_id!r} tool={request.tool_name!r}"
            )
        budget_id = secrets.token_hex(8)
        created_at = int(self._clock())
        self._conn.execute(
            """
            INSERT INTO mcp_budgets (
                budget_id, scope_kind, scope_key, server_id, tool_name,
                soft_limit, hard_limit, window, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                budget_id,
                request.scope_kind,
                request.scope_key,
                request.server_id,
                request.tool_name,
                request.soft_limit,
                request.hard_limit,
                request.window,
                created_at,
            ),
        )
        self._conn.commit()
        return ToolBudget(
            budget_id=budget_id,
            scope_kind=request.scope_kind,
            scope_key=request.scope_key,
            server_id=request.server_id,
            tool_name=request.tool_name,
            soft_limit=request.soft_limit,
            hard_limit=request.hard_limit,
            window=request.window,
            created_at=created_at,
        )

    def list_budgets(
        self,
        *,
        scope_kind: str | None = None,
        scope_key: str | None = None,
        server_id: str | None = None,
        tool_name: str | None = None,
    ) -> list[ToolBudget]:
        """Exact-equality filters; created_at asc, budget_id asc (RED stub)."""
        clauses: list[str] = []
        params: list[object] = []
        for column, value in (
            ("scope_kind", scope_kind),
            ("scope_key", scope_key),
            ("server_id", server_id),
            ("tool_name", tool_name),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM mcp_budgets{where} "
            "ORDER BY created_at ASC, budget_id ASC",
            params,
        ).fetchall()
        return [_row_to_budget(r) for r in rows]

    def get_budget(self, budget_id: str) -> ToolBudget:
        """Unknown id raises BudgetNotFoundError (RED stub)."""
        row = self._conn.execute(
            "SELECT * FROM mcp_budgets WHERE budget_id = ?", (budget_id,)
        ).fetchone()
        if row is None:
            raise BudgetNotFoundError(f"budget {budget_id!r} not found")
        return _row_to_budget(row)

    def delete_budget(self, budget_id: str) -> None:
        """Unknown id raises BudgetNotFoundError (RED stub)."""
        cursor = self._conn.execute(
            "DELETE FROM mcp_budgets WHERE budget_id = ?", (budget_id,)
        )
        self._conn.commit()
        if cursor.rowcount == 0:
            raise BudgetNotFoundError(f"budget {budget_id!r} not found")


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
        # The ledger may or may not support per-tool filtering (the existing
        # CostTracker.spend_since signature predates the MCP module). Detect
        # once so we can pass tool_name only when the tracker supports it.
        try:
            self._tracker_supports_tool = "tool_name" in inspect.signature(
                tracker.spend_since
            ).parameters
        except (TypeError, ValueError):
            self._tracker_supports_tool = False

    def applicable_budgets(
        self, scopes: list["BudgetScope"], server_id: str, tool_name: str
    ) -> list[ToolBudget]:
        """Budgets whose scope + tool selector match (RED stub)."""
        caller_scopes = {(s.kind, s.key) for s in scopes}
        matched: list[tuple[int, ToolBudget]] = []
        for budget in self._budgets.list_budgets():
            if budget.scope_kind != "global" and (
                budget.scope_kind,
                budget.scope_key,
            ) not in caller_scopes:
                continue
            if budget.server_id is not None and budget.server_id != server_id:
                continue
            if budget.tool_name is not None and budget.tool_name != tool_name:
                continue
            scope_rank = {"user": 3, "team": 2, "project": 1, "global": 0}.get(
                budget.scope_kind, 0
            )
            tool_rank = (
                3
                if budget.server_id is not None and budget.tool_name is not None
                else 2
                if budget.server_id is not None
                else 1
            )
            matched.append((scope_rank * 10 + tool_rank, budget))
        matched.sort(key=lambda pair: pair[0], reverse=True)
        return [budget for _, budget in matched]

    def _scope_matched_budgets(
        self, scopes: list["BudgetScope"]
    ) -> list[tuple[ToolBudget, "BudgetScope"]]:
        """Budgets matching a caller scope (scope only), with the caller scope.

        The engine's budget gate matches by scope identity (kind+key) so a
        ceiling defined for ``user:alice`` applies regardless of the specific
        server id used in the call (the server selector is a display-level
        refinement, not a gate bypass).
        """
        result: list[tuple[ToolBudget, BudgetScope]] = []
        for budget in self._budgets.list_budgets():
            for scope in scopes:
                if budget.scope_kind == "global" or (
                    budget.scope_kind == scope.kind
                    and budget.scope_key == scope.key
                ):
                    result.append((budget, scope))
                    break
        return result

    async def _spend_for(
        self, scope: "BudgetScope", since: int, tool: str
    ) -> float:
        """Ask the ledger for spend; pass tool_name only when supported."""
        if self._tracker_supports_tool:
            return await self._tracker.spend_since(
                scope.scope_key(), since, tool_name=tool  # type: ignore[call-arg]
            )
        return await self._tracker.spend_since(scope.scope_key(), since)

    async def check(
        self, scopes: list["BudgetScope"], server_id: str, tool_name: str
    ) -> None:
        """Raise BudgetExceededError when spend >= hard_limit (RED stub)."""
        now = int(self._now_fn())
        tool = self.canonical_tool(server_id, tool_name)
        for budget, scope in self._scope_matched_budgets(scopes):
            if budget.hard_limit is None:
                continue
            since = now - budget_window_seconds(budget.window)
            spend = await self._spend_for(scope, since, tool)
            if spend >= budget.hard_limit:
                raise BudgetExceededError(scope, spend, budget.hard_limit)

    async def soft_exceeded(
        self, scopes: list["BudgetScope"], server_id: str, tool_name: str
    ) -> list["BudgetScope"]:
        """Return scopes whose soft limit is exceeded; never raises (RED stub)."""
        now = int(self._now_fn())
        tool = self.canonical_tool(server_id, tool_name)
        exceeded: list[BudgetScope] = []
        seen: set[str] = set()
        for budget, scope in self._scope_matched_budgets(scopes):
            if budget.soft_limit is None:
                continue
            since = now - budget_window_seconds(budget.window)
            spend = await self._spend_for(scope, since, tool)
            if spend >= budget.soft_limit and scope.scope_key() not in seen:
                exceeded.append(scope)
                seen.add(scope.scope_key())
        return exceeded

    async def record_usage(self, *, event: AuditEvent) -> None:
        """Persist tool-call cost attribution to the ledger (RED stub)."""
        tool = self.canonical_tool(event.server_id, event.tool_name)
        user_id = event.scope_key if event.scope_kind == "user" else None
        team = event.scope_key if event.scope_kind == "team" else None
        project = event.scope_key if event.scope_kind == "project" else None
        record = UsageRecord(
            request_id=event.event_id,
            api_key=event.caller,
            user_id=user_id,
            team=team,
            model=f"mcp:{event.server_id}:{event.tool_name}",
            provider="mcp",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            input_cost=0.0,
            output_cost=0.0,
            total_cost=event.cost,
            latency_ms=event.latency_ms,
            status="success" if event.status == "completed" else "error",
            timestamp=event.timestamp,
        )
        # The existing UsageRecord dataclass predates tool-level attribution;
        # attach the tool name dynamically so the ledger row carries it while
        # the frozen cost_tracking interface stays untouched.
        record.tool_name = tool  # type: ignore[attr-defined]
        record.project = project  # type: ignore[attr-defined]
        await self._tracker.record(record)

    def canonical_tool(self, server_id: str, tool_name: str) -> str:
        """Return f"{server_id}:{tool_name}" — the ledger tool_name key (RED stub)."""
        return f"{server_id}:{tool_name}"
