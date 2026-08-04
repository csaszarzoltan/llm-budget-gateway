"""Per-tool access policies and the resolution evaluator.

Normative per docs/architecture/mcp-governance.md §6.2. Constructors are
functional in the RED phase (mcp_policies table creation, default_effect
storage); the behavioral methods are implemented here.
"""

import secrets
import sqlite3
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from .exceptions import DuplicatePolicyError, PolicyNotFoundError
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

_SCOPE_RANK = {"user": 3, "team": 2, "project": 1, "global": 0}
_EFFECT_PRECEDENCE = {"deny": 0, "approval": 1, "allow": 2}


def _row_to_policy(row: sqlite3.Row) -> ToolPolicy:
    """Map a mcp_policies row to a ToolPolicy model."""
    return ToolPolicy(
        policy_id=row["policy_id"],
        scope_kind=row["scope_kind"],
        scope_key=row["scope_key"],
        server_id=row["server_id"],
        tool_name=row["tool_name"],
        effect=row["effect"],
        description=row["description"],
        created_by=row["created_by"],
        created_at=row["created_at"],
    )


def _tool_rank(server_id: str | None, tool_name: str | None) -> int:
    """Tool-selector specificity: exact tool > server-wide > global wildcard."""
    if server_id is not None and tool_name is not None:
        return 3
    if server_id is not None:
        return 2
    return 1


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
        existing = self._conn.execute(
            """
            SELECT 1 FROM mcp_policies
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
            raise DuplicatePolicyError(
                "policy already exists for "
                f"{request.scope_kind}:{request.scope_key} "
                f"server={request.server_id!r} tool={request.tool_name!r}"
            )
        policy_id = secrets.token_hex(8)
        created_at = int(self._clock())
        self._conn.execute(
            """
            INSERT INTO mcp_policies (
                policy_id, scope_kind, scope_key, server_id, tool_name,
                effect, description, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                policy_id,
                request.scope_kind,
                request.scope_key,
                request.server_id,
                request.tool_name,
                request.effect,
                request.description,
                "admin",
                created_at,
            ),
        )
        self._conn.commit()
        return ToolPolicy(
            policy_id=policy_id,
            scope_kind=request.scope_kind,
            scope_key=request.scope_key,
            server_id=request.server_id,
            tool_name=request.tool_name,
            effect=request.effect,
            description=request.description,
            created_by="admin",
            created_at=created_at,
        )

    def list_policies(
        self,
        *,
        scope_kind: str | None = None,
        scope_key: str | None = None,
        server_id: str | None = None,
        tool_name: str | None = None,
    ) -> list[ToolPolicy]:
        """Exact-equality filters; created_at asc, policy_id asc (RED stub)."""
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
            f"SELECT * FROM mcp_policies{where} "
            "ORDER BY created_at ASC, policy_id ASC",
            params,
        ).fetchall()
        return [_row_to_policy(r) for r in rows]

    def get_policy(self, policy_id: str) -> ToolPolicy:
        """Unknown id raises PolicyNotFoundError (RED stub)."""
        row = self._conn.execute(
            "SELECT * FROM mcp_policies WHERE policy_id = ?", (policy_id,)
        ).fetchone()
        if row is None:
            raise PolicyNotFoundError(f"policy {policy_id!r} not found")
        return _row_to_policy(row)

    def delete_policy(self, policy_id: str) -> None:
        """Unknown id raises PolicyNotFoundError; deletion permanent (RED stub)."""
        cursor = self._conn.execute(
            "DELETE FROM mcp_policies WHERE policy_id = ?", (policy_id,)
        )
        self._conn.commit()
        if cursor.rowcount == 0:
            raise PolicyNotFoundError(f"policy {policy_id!r} not found")


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
        caller_scopes = {(s.kind, s.key) for s in scopes}
        candidates: list[tuple[int, ToolPolicy]] = []
        for policy in self._store.list_policies():
            if policy.scope_kind != "global" and (
                policy.scope_kind,
                policy.scope_key,
            ) not in caller_scopes:
                continue
            if policy.server_id is not None and policy.server_id != server_id:
                continue
            if policy.tool_name is not None and policy.tool_name != tool_name:
                continue
            scope_rank = _SCOPE_RANK.get(policy.scope_kind, 0)
            score = scope_rank * 10 + _tool_rank(policy.server_id, policy.tool_name)
            candidates.append((score, policy))
        if not candidates:
            effect = self._store.default_effect
            return PolicyDecision(
                effect=effect,
                policy_id=None,
                reason=f"no policy matched; default {effect}",
                matched_scope=None,
            )
        max_score = max(score for score, _ in candidates)
        winners = [
            policy for score, policy in candidates if score == max_score
        ]
        winner = min(
            winners,
            key=lambda p: (_EFFECT_PRECEDENCE[p.effect], p.policy_id),
        )
        matched_scope = (
            f"{winner.scope_kind}:{winner.scope_key}"
            if winner.scope_kind != "global"
            else None
        )
        reasons = {
            "allow": f"allowed by policy {winner.policy_id}",
            "deny": f"denied by policy {winner.policy_id}",
            "approval": f"approval required by policy {winner.policy_id}",
        }
        return PolicyDecision(
            effect=winner.effect,
            policy_id=winner.policy_id,
            reason=reasons[winner.effect],
            matched_scope=matched_scope,
        )
