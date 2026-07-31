"""Enterprise workflow, identity, routing, privacy and tool governance services."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence


class PermissionDenied(Exception):
    """Raised when a tenant-scoped action is not authorized."""


_ROLE = {
    "viewer": 0,
    "auditor": 1,
    "operator": 2,
    "security": 3,
    "privacy": 3,
    "admin": 4,
    "scim-admin": 4,
}


class EnterprisePlatform:
    """Additive SQLite implementation of the six enterprise requirements."""

    def __init__(self, path: str, clock: Callable[[], int] | None = None) -> None:
        self.clock = clock or (lambda: int(time.time()))
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS approval(id TEXT PRIMARY KEY,tenant TEXT,requester TEXT,kind TEXT,payload TEXT,state TEXT,required INTEGER,expires INTEGER,idem TEXT,UNIQUE(tenant,idem));
        CREATE TABLE IF NOT EXISTS approval_vote(approval_id TEXT,actor TEXT,decision TEXT,created INTEGER,PRIMARY KEY(approval_id,actor));
        CREATE TABLE IF NOT EXISTS control_def(tenant TEXT,name TEXT,interval_sec INTEGER,PRIMARY KEY(tenant,name));
        CREATE TABLE IF NOT EXISTS control_evidence(id TEXT PRIMARY KEY,tenant TEXT,control_name TEXT,payload TEXT,created INTEGER);
        CREATE TABLE IF NOT EXISTS scim_user(tenant TEXT,external_id TEXT,email TEXT,role TEXT,active INTEGER,updated INTEGER,PRIMARY KEY(tenant,external_id));
        CREATE TABLE IF NOT EXISTS subject_record(id TEXT PRIMARY KEY,tenant TEXT,subject TEXT,region TEXT,payload TEXT,created INTEGER);
        CREATE TABLE IF NOT EXISTS privacy_case(id TEXT PRIMARY KEY,tenant TEXT,subject TEXT,kind TEXT,state TEXT,idem TEXT,created INTEGER,UNIQUE(tenant,idem));
        CREATE TABLE IF NOT EXISTS legal_hold(tenant TEXT,subject TEXT,active INTEGER,PRIMARY KEY(tenant,subject));
        CREATE TABLE IF NOT EXISTS tool_policy(tenant TEXT,tool TEXT,requires_approval INTEGER,max_cost REAL,PRIMARY KEY(tenant,tool));
        CREATE TABLE IF NOT EXISTS tool_run(id TEXT PRIMARY KEY,tenant TEXT,agent TEXT,tool TEXT,args TEXT,cost REAL,state TEXT,result TEXT,idem TEXT,UNIQUE(tenant,idem));
        CREATE TABLE IF NOT EXISTS enterprise_audit(id TEXT PRIMARY KEY,tenant TEXT,actor TEXT,action TEXT,object_id TEXT,state TEXT,created INTEGER);
        """)
        self.db.commit()

    def _need(self, role: str, minimum: str) -> None:
        if _ROLE.get(role, -1) < _ROLE[minimum]:
            raise PermissionDenied(f"{minimum} role required")

    def _audit(
        self, tenant: str, actor: str, action: str, object_id: str, state: str
    ) -> None:
        self.db.execute(
            "INSERT INTO enterprise_audit VALUES(?,?,?,?,?,?,?)",
            (
                secrets.token_hex(8),
                tenant,
                actor,
                action,
                object_id,
                state,
                self.clock(),
            ),
        )
        self.db.commit()

    def create_approval(
        self,
        tenant: str,
        requester: str,
        kind: str,
        payload: Mapping[str, object],
        approvals_required: int,
        expires_at: int,
        idempotency_key: str,
    ) -> dict[str, object]:
        if approvals_required < 1 or expires_at <= self.clock() or not idempotency_key:
            raise ValueError(
                "valid approval count, future expiry and idempotency key required"
            )
        old = self.db.execute(
            "SELECT id,state,required,expires FROM approval WHERE tenant=? AND idem=?",
            (tenant, idempotency_key),
        ).fetchone()
        if old:
            return dict(old)
        aid = secrets.token_hex(8)
        safe = {
            k: v
            for k, v in payload.items()
            if k not in {"secret", "prompt", "authorization"}
        }
        self.db.execute(
            "INSERT INTO approval VALUES(?,?,?,?,?,'pending',?,?,?)",
            (
                aid,
                tenant,
                requester,
                kind,
                json.dumps(safe, sort_keys=True),
                approvals_required,
                expires_at,
                idempotency_key,
            ),
        )
        self.db.commit()
        self._audit(tenant, requester, "approval.create", aid, "pending")
        return {
            "id": aid,
            "state": "pending",
            "required": approvals_required,
            "expires": expires_at,
        }

    def decide(
        self, tenant: str, actor: str, approval_id: str, decision: str
    ) -> dict[str, object]:
        if decision not in {"approve", "reject"}:
            raise ValueError("invalid decision")
        row = self.db.execute(
            "SELECT requester,state,required,expires FROM approval WHERE tenant=? AND id=?",
            (tenant, approval_id),
        ).fetchone()
        if not row:
            raise KeyError(approval_id)
        if row["expires"] <= self.clock():
            self.db.execute(
                "UPDATE approval SET state='expired' WHERE id=?", (approval_id,)
            )
            self.db.commit()
            return {"id": approval_id, "state": "expired"}
        if actor == row["requester"]:
            raise PermissionDenied("requester cannot approve")
        if row["state"] not in {"pending"}:
            return {"id": approval_id, "state": row["state"]}
        self.db.execute(
            "INSERT OR IGNORE INTO approval_vote VALUES(?,?,?,?)",
            (approval_id, actor, decision, self.clock()),
        )
        if decision == "reject":
            state = "rejected"
        else:
            count = self.db.execute(
                "SELECT count(*) FROM approval_vote WHERE approval_id=? AND decision='approve'",
                (approval_id,),
            ).fetchone()[0]
            state = "approved" if count >= row["required"] else "pending"
        self.db.execute("UPDATE approval SET state=? WHERE id=?", (state, approval_id))
        self.db.commit()
        self._audit(tenant, actor, "approval.decide", approval_id, state)
        return {"id": approval_id, "state": state}

    def define_control(
        self, tenant: str, role: str, name: str, interval_sec: int
    ) -> None:
        self._need(role, "admin")
        if not name or interval_sec < 1:
            raise ValueError("name and positive interval required")
        self.db.execute(
            "INSERT OR REPLACE INTO control_def VALUES(?,?,?)",
            (tenant, name, interval_sec),
        )
        self.db.commit()

    def capture_evidence(
        self, tenant: str, role: str, control: str, payload: Mapping[str, object]
    ) -> str:
        self._need(role, "admin")
        if not self.db.execute(
            "SELECT 1 FROM control_def WHERE tenant=? AND name=?", (tenant, control)
        ).fetchone():
            raise KeyError(control)
        safe = {
            k: v
            for k, v in payload.items()
            if k not in {"prompt", "secret", "authorization"}
        }
        eid = secrets.token_hex(8)
        self.db.execute(
            "INSERT INTO control_evidence VALUES(?,?,?,?,?)",
            (eid, tenant, control, json.dumps(safe, sort_keys=True), self.clock()),
        )
        self.db.commit()
        return eid

    def evidence_report(self, tenant: str, role: str) -> dict[str, object]:
        self._need(role, "auditor")
        controls = list(
            self.db.execute(
                "SELECT name,interval_sec FROM control_def WHERE tenant=? ORDER BY name",
                (tenant,),
            )
        )
        items = [
            dict(x)
            for x in self.db.execute(
                "SELECT id,control_name,payload,created FROM control_evidence WHERE tenant=? ORDER BY id",
                (tenant,),
            )
        ]
        latest = {x["control_name"]: x["created"] for x in items}
        missing = [
            x["name"]
            for x in controls
            if latest.get(x["name"], 0) + x["interval_sec"] < self.clock()
        ]
        raw = json.dumps(items, sort_keys=True, separators=(",", ":"))
        return {
            "items": items,
            "missing": missing,
            "sha256": hashlib.sha256(raw.encode()).hexdigest(),
        }

    def scim_upsert(
        self, tenant: str, role: str, external_id: str, email: str, assigned_role: str
    ) -> dict[str, object]:
        self._need(role, "scim-admin")
        if assigned_role not in _ROLE or not external_id or "@" not in email:
            raise ValueError("invalid SCIM user")
        self.db.execute(
            "INSERT INTO scim_user VALUES(?,?,?,?,1,?) ON CONFLICT(tenant,external_id) DO UPDATE SET email=excluded.email,role=excluded.role,active=1,updated=excluded.updated",
            (tenant, external_id, email, assigned_role, self.clock()),
        )
        self.db.commit()
        self._audit(tenant, role, "scim.upsert", external_id, "active")
        return {"external_id": external_id, "active": True, "role": assigned_role}

    def scim_deactivate(self, tenant: str, role: str, external_id: str) -> None:
        self._need(role, "scim-admin")
        self.db.execute(
            "UPDATE scim_user SET active=0,updated=? WHERE tenant=? AND external_id=?",
            (self.clock(), tenant, external_id),
        )
        self.db.commit()
        self._audit(tenant, role, "scim.deactivate", external_id, "inactive")

    def authorize(self, tenant: str, external_id: str, required: str) -> bool:
        row = self.db.execute(
            "SELECT role,active FROM scim_user WHERE tenant=? AND external_id=?",
            (tenant, external_id),
        ).fetchone()
        if not row or not row["active"] or _ROLE[row["role"]] < _ROLE[required]:
            raise PermissionDenied(required)
        return True

    def access_review(self, tenant: str, role: str) -> dict[str, int]:
        self._need(role, "admin")
        rows = list(
            self.db.execute("SELECT active FROM scim_user WHERE tenant=?", (tenant,))
        )
        return {"total": len(rows), "active": sum(x[0] for x in rows)}

    def choose_model(
        self, candidates: Sequence[Mapping[str, object]], weights: Mapping[str, float]
    ) -> dict[str, object]:
        if not candidates or any(
            float(weights.get(k, 0)) < 0 for k in ("quality", "cost", "latency")
        ):
            raise ValueError("candidates and non-negative weights required")
        max_cost = max(float(x["cost"]) for x in candidates) or 1
        max_latency = max(float(x["latency"]) for x in candidates) or 1
        scores = {
            str(x["name"]): float(weights.get("quality", 0)) * float(x["quality"])
            + float(weights.get("cost", 0)) * (1 - float(x["cost"]) / max_cost)
            + float(weights.get("latency", 0)) * (1 - float(x["latency"]) / max_latency)
            for x in candidates
        }
        model = max(scores, key=scores.get)
        return {
            "model": model,
            "scores": scores,
            "explanation": "Weighted quality reward plus normalized cost and latency efficiency.",
        }

    def store_subject_record(
        self, tenant: str, subject: str, region: str, payload: Mapping[str, object]
    ) -> str:
        safe = {
            k: v
            for k, v in payload.items()
            if k not in {"prompt", "secret", "authorization"}
        }
        rid = secrets.token_hex(8)
        self.db.execute(
            "INSERT INTO subject_record VALUES(?,?,?,?,?,?)",
            (rid, tenant, subject, region, json.dumps(safe), self.clock()),
        )
        self.db.commit()
        return rid

    def open_privacy_request(
        self, tenant: str, role: str, subject: str, kind: str, idempotency_key: str
    ) -> dict[str, object]:
        self._need(role, "privacy")
        if kind not in {"export", "delete"}:
            raise ValueError("unsupported privacy request")
        old = self.db.execute(
            "SELECT id,state FROM privacy_case WHERE tenant=? AND idem=?",
            (tenant, idempotency_key),
        ).fetchone()
        if old:
            return dict(old)
        cid = secrets.token_hex(8)
        self.db.execute(
            "INSERT INTO privacy_case VALUES(?,?,?,?, 'open',?,?)",
            (cid, tenant, subject, kind, idempotency_key, self.clock()),
        )
        self.db.commit()
        self._audit(tenant, role, "privacy.open", cid, "open")
        return {"id": cid, "state": "open"}

    def set_legal_hold(
        self, tenant: str, role: str, subject: str, active: bool
    ) -> None:
        self._need(role, "privacy")
        self.db.execute(
            "INSERT OR REPLACE INTO legal_hold VALUES(?,?,?)",
            (tenant, subject, int(active)),
        )
        self.db.commit()

    def process_privacy_request(
        self, tenant: str, role: str, case_id: str
    ) -> dict[str, object]:
        self._need(role, "privacy")
        c = self.db.execute(
            "SELECT subject,kind,state FROM privacy_case WHERE tenant=? AND id=?",
            (tenant, case_id),
        ).fetchone()
        if not c:
            raise KeyError(case_id)
        hold = self.db.execute(
            "SELECT active FROM legal_hold WHERE tenant=? AND subject=?",
            (tenant, c["subject"]),
        ).fetchone()
        if c["kind"] == "delete" and hold and hold[0]:
            state = "blocked_by_hold"
        else:
            if c["kind"] == "delete":
                self.db.execute(
                    "DELETE FROM subject_record WHERE tenant=? AND subject=?",
                    (tenant, c["subject"]),
                )
            state = "completed"
        self.db.execute("UPDATE privacy_case SET state=? WHERE id=?", (state, case_id))
        self.db.commit()
        self._audit(tenant, role, "privacy.process", case_id, state)
        return {"id": case_id, "state": state}

    def define_tool_policy(
        self,
        tenant: str,
        role: str,
        tool: str,
        requires_approval: bool,
        max_cost: float,
    ) -> None:
        self._need(role, "security")
        if not tool or max_cost < 0:
            raise ValueError("valid tool and cost required")
        self.db.execute(
            "INSERT OR REPLACE INTO tool_policy VALUES(?,?,?,?)",
            (tenant, tool, int(requires_approval), max_cost),
        )
        self.db.commit()

    def request_tool(
        self,
        tenant: str,
        agent: str,
        tool: str,
        args: Mapping[str, object],
        cost: float,
        idempotency_key: str,
    ) -> dict[str, object]:
        old = self.db.execute(
            "SELECT id,state FROM tool_run WHERE tenant=? AND idem=?",
            (tenant, idempotency_key),
        ).fetchone()
        if old:
            return dict(old)
        p = self.db.execute(
            "SELECT requires_approval,max_cost FROM tool_policy WHERE tenant=? AND tool=?",
            (tenant, tool),
        ).fetchone()
        if not p or cost > p["max_cost"]:
            raise PermissionDenied("tool denied or budget exceeded")
        safe = {k: v for k, v in args.items() if k not in {"secret", "authorization"}}
        state = "awaiting_approval" if p["requires_approval"] else "approved"
        rid = secrets.token_hex(8)
        self.db.execute(
            "INSERT INTO tool_run VALUES(?,?,?,?,?,?,?,NULL,?)",
            (rid, tenant, agent, tool, json.dumps(safe), cost, state, idempotency_key),
        )
        self.db.commit()
        self._audit(tenant, agent, "tool.request", rid, state)
        return {"id": rid, "state": state}

    def approve_tool(self, tenant: str, role: str, run_id: str) -> None:
        self._need(role, "admin")
        self.db.execute(
            "UPDATE tool_run SET state='approved' WHERE tenant=? AND id=? AND state='awaiting_approval'",
            (tenant, run_id),
        )
        self.db.commit()
        self._audit(tenant, role, "tool.approve", run_id, "approved")

    def complete_tool(
        self, tenant: str, agent: str, run_id: str, result: Mapping[str, object]
    ) -> dict[str, object]:
        row = self.db.execute(
            "SELECT state FROM tool_run WHERE tenant=? AND id=?", (tenant, run_id)
        ).fetchone()
        if not row or row["state"] != "approved":
            raise PermissionDenied("tool run is not approved")
        safe = {k: v for k, v in result.items() if k not in {"secret", "authorization"}}
        self.db.execute(
            "UPDATE tool_run SET state='completed',result=? WHERE id=?",
            (json.dumps(safe), run_id),
        )
        self.db.commit()
        self._audit(tenant, agent, "tool.complete", run_id, "completed")
        return {"id": run_id, "state": "completed"}
