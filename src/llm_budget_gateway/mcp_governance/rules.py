"""Reusable governance rules: SSRF guard, PII redaction, approval gates.

Normative per docs/architecture/mcp-governance.md §6.5. Constructors are
functional in the RED phase (default patterns, mcp_approvals table creation);
the behavioral methods are implemented here.
"""

import asyncio
import hashlib
import ipaddress
import json
import re
import secrets
import socket
import sqlite3
import time
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from .exceptions import ApprovalNotFoundError, ApprovalStateError
from .schemas import ApprovalRequest, RuleVerdict

if TYPE_CHECKING:
    from llm_budget_gateway.budget_enforcement import BudgetScope

    from .policy import ToolPolicy

#: Default PII patterns. Order matters for overlapping regexes: the long
#: api_key-class tokens must run before ``phone`` so a key's digit runs are
#: never consumed as a phone number (leaving a plaintext tail), and ``ssn``
#: must run before ``phone`` so ``123-45-6789`` redacts as an SSN, not a phone.
_DEFAULT_PII_PATTERNS: dict[str, str] = {
    "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "credit_card": r"\b(?:\d[ -]?){13,19}\b",
    "api_key": r"\bsk-[A-Za-z0-9]{20,}\b",
    "anthropic_key": r"\bsk-ant-[A-Za-z0-9_-]{20,}\b",
    "gemini_key": r"\bAIza[0-9A-Za-z_-]{30,}\b",
    "xai_key": r"\bxai-[A-Za-z0-9_-]{20,}\b",
    "jwt": r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
    "bearer_token": r"\bBearer\s+[A-Za-z0-9._~+/-]+=*\b",
    "aws_access_key": r"\bAKIA[0-9A-Z]{16}\b",
    "phone": r"\+?\d[\d\s().-]{7,}\d",
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

_APPROVAL_COLUMNS = (
    "approval_id, server_id, tool_name, caller, scope_kind, scope_key, "
    "args_json, args_hash, status, requested_at, decided_at, decided_by, expires_at"
)


def _row_to_approval(row: sqlite3.Row) -> ApprovalRequest:
    """Map a mcp_approvals row to an ApprovalRequest model."""
    return ApprovalRequest(
        approval_id=row["approval_id"],
        server_id=row["server_id"],
        tool_name=row["tool_name"],
        caller=row["caller"],
        scope_kind=row["scope_kind"],
        scope_key=row["scope_key"],
        args_redacted=json.loads(row["args_json"] or "{}"),
        args_hash=row["args_hash"],
        status=row["status"],
        requested_at=row["requested_at"],
        decided_at=row["decided_at"],
        decided_by=row["decided_by"],
        expires_at=row["expires_at"],
    )


#: Extra field names treated as URL-bearing in addition to the explicit
#: ``url_fields``. Matching is case-insensitive (M2).
_URL_FIELD_ALIASES = ("uri", "target", "link", "href")


class SSRFGuard:
    """Blocks http(s) URLs whose host resolves to private/reserved space."""

    def __init__(
        self,
        allowed_hosts: Sequence[str] = (),
        url_fields: Sequence[str] = ("url", "endpoint", "webhook", "callback_url"),
    ) -> None:
        self._allowed_hosts = {h.lower() for h in allowed_hosts}
        # Lowercased for case-insensitive matching; aliases always apply.
        self._url_fields = frozenset(
            name.lower() for name in (*url_fields, *_URL_FIELD_ALIASES)
        )

    def check(self, args: Mapping[str, Any]) -> RuleVerdict:
        """Inspect every url field recursively; block on bad addresses.

        Sync entry point: hostname resolution calls socket.getaddrinfo inline.
        Async callers (the engine) MUST use acheck() so the event loop is
        never blocked by DNS (S11).
        """
        urls = self.extract_urls(args)
        if not urls:
            return RuleVerdict(
                allowed=True, rule="ssrf_guard", reason="no url fields", detail=None
            )
        for url in urls:
            verdict = self._check_url(url)
            if not verdict.allowed:
                return verdict
        return RuleVerdict(allowed=True, rule="ssrf_guard", reason="urls allowed")

    async def acheck(self, args: Mapping[str, Any]) -> RuleVerdict:
        """Async twin of check(): DNS resolution off the event loop (S11).

        socket.getaddrinfo is a blocking syscall; called from the async engine
        it would stall every other coroutine. Resolve hostnames via
        asyncio.to_thread, the same pattern cost_tracking.py uses.
        """
        urls = self.extract_urls(args)
        if not urls:
            return RuleVerdict(
                allowed=True, rule="ssrf_guard", reason="no url fields", detail=None
            )
        for url in urls:
            verdict = await self._acheck_url(url)
            if not verdict.allowed:
                return verdict
        return RuleVerdict(allowed=True, rule="ssrf_guard", reason="urls allowed")

    def extract_urls(self, args: Mapping[str, Any]) -> list[str]:
        """Return the candidate URL strings in deterministic walk order (RED stub)."""
        urls: list[str] = []

        def walk(value: Any) -> None:
            if isinstance(value, Mapping):
                for key, val in value.items():
                    if key.lower() in self._url_fields and isinstance(val, str):
                        urls.append(val)
                    walk(val)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    walk(item)

        walk(args)
        return urls

    def _check_url(self, raw: str) -> RuleVerdict:
        """Evaluate one candidate URL; hostnames resolved inline (sync)."""
        preflight = self._preflight(raw)
        if preflight[0] is not None:
            return preflight[0]
        host = preflight[1]
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return RuleVerdict(
                allowed=False,
                rule="ssrf_guard",
                reason=f"ssrf: unknown host {host}",
                detail=raw,
            )
        return self._verdict_for_infos(host, infos, raw)

    async def _acheck_url(self, raw: str) -> RuleVerdict:
        """Evaluate one candidate URL; DNS via asyncio.to_thread (S11)."""
        preflight = self._preflight(raw)
        if preflight[0] is not None:
            return preflight[0]
        host = preflight[1]
        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
        except OSError:
            return RuleVerdict(
                allowed=False,
                rule="ssrf_guard",
                reason=f"ssrf: unknown host {host}",
                detail=raw,
            )
        return self._verdict_for_infos(host, infos, raw)

    def _preflight(self, raw: str) -> "tuple[RuleVerdict | None, str]":
        """Scheme/host validation shared by the sync and async paths.

        Returns (None, host) when the URL needs DNS resolution; otherwise a
        terminal RuleVerdict plus an empty host string.
        """
        try:
            parsed = urllib.parse.urlsplit(raw)
            scheme = parsed.scheme.lower()
            host = (parsed.hostname or "").lower()
        except ValueError:
            return (
                RuleVerdict(
                    allowed=False,
                    rule="ssrf_guard",
                    reason=f"ssrf: invalid url {raw}",
                    detail=raw,
                ),
                "",
            )
        if scheme not in ("http", "https"):
            return (
                RuleVerdict(
                    allowed=False,
                    rule="ssrf_guard",
                    reason=f"ssrf: unsupported scheme {scheme}",
                    detail=raw,
                ),
                "",
            )
        if host in self._allowed_hosts:
            return (
                RuleVerdict(
                    allowed=True,
                    rule="ssrf_guard",
                    reason=f"allowed by allowlist {host}",
                    detail=raw,
                ),
                "",
            )
        if not host:
            return (
                RuleVerdict(
                    allowed=False,
                    rule="ssrf_guard",
                    reason=f"ssrf: invalid url {raw}",
                    detail=raw,
                ),
                "",
            )
        # IP literal?
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            addr = None
        if addr is not None:
            blocked = self._blocked_reason(addr)
            if blocked:
                return (
                    RuleVerdict(
                        allowed=False, rule="ssrf_guard", reason=blocked, detail=raw
                    ),
                    "",
                )
            return (
                RuleVerdict(
                    allowed=True, rule="ssrf_guard", reason="urls allowed", detail=raw
                ),
                "",
            )
        return (None, host)

    def _verdict_for_infos(self, host: str, infos: Any, raw: str) -> RuleVerdict:
        """Any resolved address in private/reserved space blocks the URL."""
        for info in infos:
            sockaddr = info[4]
            try:
                resolved = ipaddress.ip_address(sockaddr[0])
            except ValueError:
                continue
            reason = self._blocked_reason(resolved)
            if reason:
                return RuleVerdict(
                    allowed=False,
                    rule="ssrf_guard",
                    reason=f"ssrf: hostname {host} resolves to blocked address {resolved}",
                    detail=raw,
                )
        return RuleVerdict(
            allowed=True, rule="ssrf_guard", reason="urls allowed", detail=raw
        )

    @staticmethod
    def _blocked_reason(addr: "ipaddress.IPv4Address | ipaddress.IPv6Address") -> str | None:
        """Return the deterministic block reason for a private/reserved address."""
        if addr.is_loopback:
            return f"ssrf: loopback address {addr}"
        if addr.is_link_local:
            return f"ssrf: link-local address {addr}"
        if addr.is_private:
            return f"ssrf: private address {addr}"
        if addr.is_reserved:
            return f"ssrf: reserved address {addr}"
        if addr.is_multicast:
            return f"ssrf: multicast address {addr}"
        return None


class PIIRedactor:
    """Deterministic PII masking for audit/approval persistence."""

    def __init__(self, patterns: Mapping[str, str] | None = None) -> None:
        """patterns: name -> regex; custom patterns replace the defaults entirely."""
        self._patterns = dict(patterns) if patterns is not None else dict(_DEFAULT_PII_PATTERNS)
        self._compiled = [
            (name, re.compile(pattern)) for name, pattern in self._patterns.items()
        ]

    def redact(self, value: Any) -> Any:
        """Deep copy of value with every string recursively scanned (RED stub)."""
        if isinstance(value, dict):
            return {key: self.redact(val) for key, val in value.items()}
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.redact(item) for item in value)
        if isinstance(value, str):
            return self.redact_text(value)
        return value

    def redact_text(self, text: str) -> str:
        """Redact a single string (RED stub)."""
        out = text
        for name, compiled in self._compiled:
            out = compiled.sub(f"[REDACTED:{name}]", out)
        return out

    def scan(self, value: Any) -> list[str]:
        """Sorted unique pattern names that WOULD match value (RED stub)."""
        found: set[str] = set()

        def walk(item: Any) -> None:
            if isinstance(item, dict):
                for val in item.values():
                    walk(val)
            elif isinstance(item, (list, tuple)):
                for val in item:
                    walk(val)
            elif isinstance(item, str):
                for name, compiled in self._compiled:
                    if compiled.search(item):
                        found.add(name)

        walk(value)
        return sorted(found)


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

    def now(self) -> int:
        """Current epoch seconds from the injected clock (public API, M3)."""
        return int(self._clock())

    def insert(self, approval: ApprovalRequest) -> ApprovalRequest:
        """Persist an approval request (RED stub)."""
        self._conn.execute(
            f"INSERT OR REPLACE INTO mcp_approvals ({_APPROVAL_COLUMNS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                approval.approval_id,
                approval.server_id,
                approval.tool_name,
                approval.caller,
                approval.scope_kind,
                approval.scope_key,
                json.dumps(approval.args_redacted),
                approval.args_hash,
                approval.status,
                approval.requested_at,
                approval.decided_at,
                approval.decided_by,
                approval.expires_at,
            ),
        )
        self._conn.commit()
        return approval

    def get(self, approval_id: str) -> ApprovalRequest:
        """Unknown id raises ApprovalNotFoundError (RED stub)."""
        row = self._conn.execute(
            "SELECT * FROM mcp_approvals WHERE approval_id = ?", (approval_id,)
        ).fetchone()
        if row is None:
            raise ApprovalNotFoundError(f"approval {approval_id!r} not found")
        return _row_to_approval(row)

    def update_status(
        self,
        approval_id: str,
        *,
        status: str,
        decided_by: str | None = None,
        decided_at: int | None = None,
    ) -> ApprovalRequest:
        """Set status (+ decided_by/decided_at when given) (RED stub)."""
        self.get(approval_id)  # raises ApprovalNotFoundError when unknown
        self._conn.execute(
            "UPDATE mcp_approvals SET status = ?, decided_by = ?, decided_at = ? "
            "WHERE approval_id = ?",
            (status, decided_by, decided_at, approval_id),
        )
        self._conn.commit()
        return self.get(approval_id)

    def list(
        self, *, status: str | None = None, caller: str | None = None
    ) -> list[ApprovalRequest]:
        """Order requested_at DESC, approval_id DESC (RED stub)."""
        clauses: list[str] = []
        params: list[object] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if caller is not None:
            clauses.append("caller = ?")
            params.append(caller)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM mcp_approvals{where} "
            "ORDER BY requested_at DESC, approval_id DESC",
            params,
        ).fetchall()
        return [_row_to_approval(r) for r in rows]


class ApprovalGate:
    """Four-eyes gate with consume-once semantics."""

    def __init__(self, store: ApprovalStore, ttl_seconds: int | None = 3600) -> None:
        """ttl_seconds None disables expiry."""
        self._store = store
        self._ttl_seconds = ttl_seconds

    def requires_approval(self, policy: "ToolPolicy") -> bool:
        """True iff policy.effect == \"approval\" (RED stub)."""
        return policy.effect == "approval"

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
        now = self._store.now()
        scope = scopes[0] if scopes else _default_scope()
        request = ApprovalRequest(
            approval_id=secrets.token_hex(8),
            server_id=server_id,
            tool_name=tool_name,
            caller=caller,
            scope_kind=scope.kind,
            scope_key=scope.key,
            args_redacted=PIIRedactor().redact(dict(args)),
            args_hash=args_hash_of(dict(args)),
            status="pending",
            requested_at=now,
            decided_at=None,
            decided_by=None,
            expires_at=now + self._ttl_seconds if self._ttl_seconds is not None else None,
        )
        return self._store.insert(request)

    def approve(self, approval_id: str, actor: str) -> ApprovalRequest:
        """pending -> approved; any other status raises (RED stub)."""
        approval = self._store.get(approval_id)
        if approval.status != "pending":
            raise ApprovalStateError(
                f"approval {approval_id!r} is {approval.status}, not pending"
            )
        return self._store.update_status(
            approval_id,
            status="approved",
            decided_by=actor,
            decided_at=self._store.now(),
        )

    def reject(self, approval_id: str, actor: str) -> ApprovalRequest:
        """pending -> rejected; any other status raises (RED stub)."""
        approval = self._store.get(approval_id)
        if approval.status != "pending":
            raise ApprovalStateError(
                f"approval {approval_id!r} is {approval.status}, not pending"
            )
        return self._store.update_status(
            approval_id,
            status="rejected",
            decided_by=actor,
            decided_at=self._store.now(),
        )

    def consume(self, approval_id: str, actor: str) -> ApprovalRequest:
        """approved -> consumed (single use); any other status raises (RED stub)."""
        approval = self._store.get(approval_id)
        if approval.status != "approved":
            raise ApprovalStateError(
                f"approval {approval_id!r} is {approval.status}, not approved"
            )
        return self._store.update_status(
            approval_id,
            status="consumed",
            decided_by=actor,
            decided_at=self._store.now(),
        )

    def find_approved(
        self,
        *,
        caller: str,
        server_id: str,
        tool_name: str,
        args_hash: str,
    ) -> ApprovalRequest | None:
        """Most recent unexpired approved approval matching the call (RED stub)."""
        now = self._store.now()
        row = self._store._conn.execute(
            """
            SELECT * FROM mcp_approvals
            WHERE status = 'approved' AND caller = ? AND server_id = ?
              AND tool_name = ? AND args_hash = ?
              AND (expires_at IS NULL OR expires_at >= ?)
            ORDER BY requested_at DESC, approval_id DESC
            LIMIT 1
            """,
            (caller, server_id, tool_name, args_hash, now),
        ).fetchone()
        return _row_to_approval(row) if row is not None else None

    def consume_approved(
        self,
        *,
        caller: str,
        server_id: str,
        tool_name: str,
        args_hash: str,
    ) -> ApprovalRequest | None:
        """Atomically claim the newest matching approved approval (S15).

        find_approved + consume as separate statements let two concurrent
        callers both observe the same row and double-consume it. Running the
        find and the status flip inside one BEGIN IMMEDIATE transaction makes
        the claim single-winner: the second caller blocks on the write lock,
        then sees status='consumed', gets None and creates a fresh request.
        """
        now = self._store.now()
        conn = self._store._conn
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM mcp_approvals
                WHERE status = 'approved' AND caller = ? AND server_id = ?
                  AND tool_name = ? AND args_hash = ?
                  AND (expires_at IS NULL OR expires_at >= ?)
                ORDER BY requested_at DESC, approval_id DESC
                LIMIT 1
                """,
                (caller, server_id, tool_name, args_hash, now),
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            conn.execute(
                "UPDATE mcp_approvals SET status = 'consumed', "
                "decided_by = ?, decided_at = ? WHERE approval_id = ?",
                (caller, now, row["approval_id"]),
            )
            updated = conn.execute(
                "SELECT * FROM mcp_approvals WHERE approval_id = ?",
                (row["approval_id"],),
            ).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return _row_to_approval(updated)

    def expire_stale(self, now: int | None = None) -> int:
        """pending requests with expires_at < now -> expired; return count (RED stub)."""
        now = now if now is not None else self._store.now()
        cursor = self._store._conn.execute(
            "UPDATE mcp_approvals SET status = 'expired' "
            "WHERE status = 'pending' AND expires_at IS NOT NULL AND expires_at < ?",
            (now,),
        )
        self._store._conn.commit()
        return cursor.rowcount


def _default_scope() -> "BudgetScope":
    """Fallback scope when a gate call carries no scopes."""
    from llm_budget_gateway.budget_enforcement import BudgetScope

    return BudgetScope("global", "default")


def args_hash_of(args: Mapping[str, Any]) -> str:
    """sha256 hexdigest of canonical JSON (sort_keys, compact separators)."""
    canonical = json.dumps(
        dict(args), sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
