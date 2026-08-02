"""Reusable governance rules: SSRF guard, PII redaction, approval gates.

Normative per docs/architecture/mcp-governance.md §6.5. Constructors are
functional in the RED phase (default patterns, mcp_approvals table creation);
every behavioral method raises NotImplementedError until the implementer
lands it.
"""

import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from .schemas import ApprovalRequest, RuleVerdict

if TYPE_CHECKING:
    from llm_budget_gateway.budget_enforcement import BudgetScope

    from .policy import ToolPolicy

_DEFAULT_PII_PATTERNS: dict[str, str] = {
    "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    "phone": r"\+?\d[\d\s().-]{7,}\d",
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "credit_card": r"\b(?:\d[ -]?){13,19}\b",
    "api_key": r"\bsk-[A-Za-z0-9]{20,}\b",
    "bearer_token": r"\bBearer\s+[A-Za-z0-9._~+/-]+=*\b",
    "aws_access_key": r"\bAKIA[0-9A-Z]{16}\b",
}

_CREATE_APPROVALS = """
CREATE TABLE IF NOT EXISTS mcp_approvals (
    approval_id  TEXT PRIMARY KEY,
    server_id    TEXT NOT NULL,
    tool_name    TEXT NOT NULL,
    caller       TEXT NOT NULL,
    scope_kind   TEXT NOT NULL,
    scope_key    TEXT NOT NULL,
    args_json    TEXT NOT NULL,
    args_hash    TEXT NOT NULL,
    status       TEXT NOT NULL,
    requested_at INTEGER NOT NULL,
    decided_at   INTEGER,
    decided_by   TEXT,
    expires_at   INTEGER
)
"""

_CREATE_APPROVAL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_mcp_approvals_hash ON mcp_approvals (args_hash)",
    "CREATE INDEX IF NOT EXISTS idx_mcp_approvals_status ON mcp_approvals (status, caller)",
]


class SSRFGuard:
    """Blocks http(s) URLs whose host resolves to private/reserved space."""

    def __init__(
        self,
        allowed_hosts: Sequence[str] = (),
        url_fields: Sequence[str] = ("url", "endpoint", "webhook", "callback_url"),
    ) -> None:
        self._allowed_hosts = {h.lower() for h in allowed_hosts}
        self._url_fields = tuple(url_fields)

    def check(self, args: Mapping[str, Any]) -> RuleVerdict:
        """Inspect every url field recursively; block on bad addresses (RED stub)."""
        raise NotImplementedError

    def extract_urls(self, args: Mapping[str, Any]) -> list[str]:
        """Return the candidate URL strings in deterministic walk order (RED stub)."""
        raise NotImplementedError


class PIIRedactor:
    """Deterministic PII masking for audit/approval persistence."""

    def __init__(self, patterns: Mapping[str, str] | None = None) -> None:
        """patterns: name -> regex; custom patterns replace the defaults entirely."""
        self._patterns = dict(patterns) if patterns is not None else dict(_DEFAULT_PII_PATTERNS)

    def redact(self, value: Any) -> Any:
        """Deep copy of value with every string recursively scanned (RED stub)."""
        raise NotImplementedError

    def redact_text(self, text: str) -> str:
        """Redact a single string (RED stub)."""
        raise NotImplementedError

    def scan(self, value: Any) -> list[str]:
        """Sorted unique pattern names that WOULD match value (RED stub)."""
        raise NotImplementedError


class ApprovalStore:
    """Persistent approval requests (four-eyes gates)."""

    def __init__(
        self, conn: sqlite3.Connection, clock: Callable[[], int] | None = None
    ) -> None:
        """Creates the mcp_approvals table and its indexes."""
        self._conn = conn
        self._clock = clock if clock is not None else (lambda: int(time.time()))
        conn.execute(_CREATE_APPROVALS)
        for stmt in _CREATE_APPROVAL_INDEXES:
            conn.execute(stmt)
        conn.commit()

    def insert(self, approval: ApprovalRequest) -> ApprovalRequest:
        """Persist an approval request (RED stub)."""
        raise NotImplementedError

    def get(self, approval_id: str) -> ApprovalRequest:
        """Unknown id raises ApprovalNotFoundError (RED stub)."""
        raise NotImplementedError

    def update_status(
        self,
        approval_id: str,
        *,
        status: str,
        decided_by: str | None = None,
        decided_at: int | None = None,
    ) -> ApprovalRequest:
        """Set status (+ decided_by/decided_at when given) (RED stub)."""
        raise NotImplementedError

    def list(
        self, *, status: str | None = None, caller: str | None = None
    ) -> list[ApprovalRequest]:
        """Order requested_at DESC, approval_id DESC (RED stub)."""
        raise NotImplementedError


class ApprovalGate:
    """Four-eyes gate with consume-once semantics."""

    def __init__(self, store: ApprovalStore, ttl_seconds: int | None = 3600) -> None:
        """ttl_seconds None disables expiry."""
        self._store = store
        self._ttl_seconds = ttl_seconds

    def requires_approval(self, policy: "ToolPolicy") -> bool:
        """True iff policy.effect == \"approval\" (RED stub)."""
        raise NotImplementedError

    def create_request(
        self,
        *,
        policy: "ToolPolicy",
        caller: str,
        scopes: list["BudgetScope"],
        server_id: str,
        tool_name: str,
        args: Mapping[str, Any],
    ) -> ApprovalRequest:
        """Build and insert a pending ApprovalRequest (RED stub)."""
        raise NotImplementedError

    def approve(self, approval_id: str, actor: str) -> ApprovalRequest:
        """pending -> approved; any other status raises (RED stub)."""
        raise NotImplementedError

    def reject(self, approval_id: str, actor: str) -> ApprovalRequest:
        """pending -> rejected; any other status raises (RED stub)."""
        raise NotImplementedError

    def consume(self, approval_id: str, actor: str) -> ApprovalRequest:
        """approved -> consumed (single use); any other status raises (RED stub)."""
        raise NotImplementedError

    def find_approved(
        self,
        *,
        caller: str,
        server_id: str,
        tool_name: str,
        args_hash: str,
    ) -> ApprovalRequest | None:
        """Most recent unexpired approved approval matching the call (RED stub)."""
        raise NotImplementedError

    def expire_stale(self, now: int | None = None) -> int:
        """pending requests with expires_at < now -> expired; return count (RED stub)."""
        raise NotImplementedError
