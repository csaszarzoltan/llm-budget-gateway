"""Per-tool access policies and the resolution evaluator.

Normative per docs/architecture/mcp-governance.md §6.2. Constructors are
functional in the RED phase (mcp_policies table creation, default_effect
storage); every behavioral method raises NotImplementedError until the
implementer lands it.
"""

import sqlite3
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from .schemas import PolicyDecision, ToolPolicy, ToolPolicyRequest

if TYPE_CHECKING:
    from llm_budget_gateway.budget_enforcement import BudgetScope

_CREATE_POLICIES = """
CREATE TABLE IF NOT EXISTS mcp_policies (
    policy_id   TEXT PRIMARY KEY,
    scope_kind  TEXT NOT NULL,
    scope_key   TEXT NOT NULL,
    server_id   TEXT,
    tool_name   TEXT,
    effect      TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_by  TEXT NOT NULL,
    created_at  INTEGER NOT NULL
)
"""


class ToolPolicyStore:
    """Allow / deny / approval policies keyed by scope + tool selector."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        clock: Callable[[], int] | None = None,
        default_effect: str = "deny",
    ) -> None:
        """Creates the mcp_policies table; default_effect in allow/deny/approval."""
        if default_effect not in ("allow", "deny", "approval"):
            raise ValueError(f"invalid default_effect: {default_effect!r}")
        self._conn = conn
        self._clock = clock if clock is not None else (lambda: int(time.time()))
        self._default_effect = default_effect
        conn.execute(_CREATE_POLICIES)
        conn.commit()

    @property
    def default_effect(self) -> str:
        """The store's default effect used when no policy matches."""
        return self._default_effect

    def create_policy(self, request: ToolPolicyRequest) -> ToolPolicy:
        """Insert a policy; duplicate 4-tuple raises (RED stub)."""
        raise NotImplementedError

    def list_policies(
        self,
        *,
        scope_kind: str | None = None,
        scope_key: str | None = None,
        server_id: str | None = None,
        tool_name: str | None = None,
    ) -> list[ToolPolicy]:
        """Exact-equality filters; created_at asc, policy_id asc (RED stub)."""
        raise NotImplementedError

    def get_policy(self, policy_id: str) -> ToolPolicy:
        """Unknown id raises PolicyNotFoundError (RED stub)."""
        raise NotImplementedError

    def delete_policy(self, policy_id: str) -> None:
        """Unknown id raises PolicyNotFoundError; deletion permanent (RED stub)."""
        raise NotImplementedError


class PolicyEvaluator:
    """Resolve the effective allow/deny/approval decision for a tool call."""

    def __init__(self, store: ToolPolicyStore) -> None:
        self._store = store

    def decide(
        self,
        *,
        scopes: list["BudgetScope"],
        server_id: str,
        tool_name: str,
    ) -> PolicyDecision:
        """Resolution algorithm per spec §6.2.1 (RED stub)."""
        raise NotImplementedError
