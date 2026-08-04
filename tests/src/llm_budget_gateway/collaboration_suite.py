"""Team collaboration controls for membership, roles, keys, budgets, and approvals."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence


class RolePolicy:
    """Evaluate least-privilege organization and project permissions."""

    _permissions = {
        "owner": {
            "members:write",
            "budgets:write",
            "keys:write",
            "approvals:write",
            "usage:read",
        },
        "admin": {
            "members:write",
            "budgets:write",
            "keys:write",
            "approvals:write",
            "usage:read",
        },
        "developer": {"keys:own", "usage:own", "requests:create"},
        "viewer": {"usage:read"},
    }

    def authorize(
        self,
        role: str,
        permission: str,
        scopes: Sequence[str],
        project: str | None = None,
    ) -> dict[str, object]:
        """Return an explainable role and project-scope decision."""
        if role not in self._permissions or not permission:
            raise ValueError("valid role and permission required")
        allowed = permission in self._permissions[role] and (
            project is None or "*" in scopes or project in scopes
        )
        return {
            "allowed": allowed,
            "role": role,
            "permission": permission,
            "reason": "allowed" if allowed else "insufficient_scope",
        }


class InvitationService:
    """Issue expiring one-time team invitation tokens without storing plaintext."""

    def __init__(self, path: str, clock: Callable[[], int] | None = None) -> None:
        self.clock = clock or (lambda: int(time.time()))
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS invitation(token_hash TEXT PRIMARY KEY,tenant TEXT,email TEXT,role TEXT,expires INTEGER,accepted INTEGER)"
        )
        self.db.commit()

    def issue(
        self, tenant: str, email: str, role: str, ttl: int = 86400
    ) -> dict[str, object]:
        """Create an invitation and return its one-time token."""
        if (
            not tenant
            or "@" not in email
            or role not in RolePolicy._permissions
            or ttl <= 0
        ):
            raise ValueError("invalid invitation")
        token = secrets.token_urlsafe(24)
        digest = hashlib.sha256(token.encode()).hexdigest()
        self.db.execute(
            "INSERT INTO invitation VALUES(?,?,?,?,?,0)",
            (digest, tenant, email.lower(), role, self.clock() + ttl),
        )
        self.db.commit()
        return {
            "token": token,
            "expires": self.clock() + ttl,
            "email": email.lower(),
            "role": role,
        }

    def accept(self, token: str) -> dict[str, object]:
        """Accept a valid invitation exactly once."""
        digest = hashlib.sha256(token.encode()).hexdigest()
        row = self.db.execute(
            "SELECT * FROM invitation WHERE token_hash=?", (digest,)
        ).fetchone()
        if not row or row["expires"] <= self.clock() or row["accepted"]:
            raise ValueError("invitation is invalid, expired, or already accepted")
        self.db.execute(
            "UPDATE invitation SET accepted=1 WHERE token_hash=?", (digest,)
        )
        self.db.commit()
        return {"tenant": row["tenant"], "email": row["email"], "role": row["role"]}


class KeyLifecycle:
    """Track key age and produce rotation decisions without storing key material."""

    def evaluate(
        self,
        created_at: int,
        last_used_at: int | None,
        now: int,
        max_age_days: int = 90,
        idle_days: int = 30,
    ) -> dict[str, object]:
        """Return rotation/revocation guidance from key age and idle time."""
        if (
            any(isinstance(x, bool) for x in (created_at, now, max_age_days, idle_days))
            or created_at < 0
            or now < created_at
            or max_age_days < 1
            or idle_days < 1
            or last_used_at is not None
            and not created_at <= last_used_at <= now
        ):
            raise ValueError("invalid key lifecycle data")
        age = (now - created_at) // 86400
        idle = (
            now - (last_used_at if last_used_at is not None else created_at)
        ) // 86400
        action = (
            "rotate"
            if age >= max_age_days
            else "revoke"
            if idle >= idle_days
            else "keep"
        )
        return {
            "action": action,
            "age_days": age,
            "idle_days": idle,
            "rotate_by": created_at + max_age_days * 86400,
        }


class MemberBudget:
    """Enforce member spend and active-key limits before provider dispatch."""

    def evaluate(
        self,
        spent: float,
        request_cost: float,
        limit: float,
        active_keys: int,
        max_keys: int,
    ) -> dict[str, object]:
        """Return whether a request and new-key operation remain allowed."""
        values = (spent, request_cost, limit)
        if any(
            isinstance(x, bool) or not isinstance(x, (int, float)) or x < 0
            for x in values
        ) or any(
            isinstance(x, bool) or not isinstance(x, int) or x < 0
            for x in (active_keys, max_keys)
        ):
            raise ValueError("invalid budget data")
        remaining = max(0.0, limit - spent)
        request_allowed = request_cost <= remaining
        key_allowed = active_keys < max_keys
        return {
            "request_allowed": request_allowed,
            "key_creation_allowed": key_allowed,
            "remaining": remaining,
            "projected_spend": spent + request_cost,
        }


class ApprovalDelegation:
    """Validate time-limited approval delegation and prevent self-approval."""

    def decide(
        self,
        requester: str,
        approver: str,
        delegations: Sequence[Mapping[str, object]],
        now: int,
    ) -> dict[str, object]:
        """Return whether an approver has direct or delegated authority."""
        if not requester or not approver or requester == approver or now < 0:
            raise ValueError("requester and distinct approver required")
        match = next(
            (
                x
                for x in delegations
                if x.get("delegate") == approver
                and int(x.get("starts", 0)) <= now < int(x.get("expires", 0))
            ),
            None,
        )
        return {
            "allowed": match is not None,
            "approver": approver,
            "delegated_by": match.get("owner") if match else None,
            "reason": "active_delegation" if match else "no_active_delegation",
        }
