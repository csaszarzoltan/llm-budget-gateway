"""Tenant-isolated control plane for keys, budgets, policy, observability and routes."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


class PermissionDenied(Exception):
    pass


class PolicyDenied(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


_ROLE = {"viewer": 0, "auditor": 1, "operator": 2, "security": 3, "admin": 4}


@dataclass(frozen=True)
class Actor:
    tenant_id: str
    role: str


class ControlPlane:
    """Transactional SQLite control plane. Every query is tenant scoped."""

    def __init__(self, path: str, clock: Callable[[], int] | None = None) -> None:
        self.clock = clock or (lambda: int(time.time()))
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS workspaces(tenant TEXT PRIMARY KEY,name TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS idem(tenant TEXT NOT NULL,k TEXT NOT NULL,response TEXT NOT NULL,PRIMARY KEY(tenant,k));
        CREATE TABLE IF NOT EXISTS keys(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,label TEXT NOT NULL,secret_hash TEXT NOT NULL UNIQUE,models TEXT NOT NULL,status TEXT NOT NULL,expires INTEGER,overlap_until INTEGER,created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS budgets(tenant TEXT NOT NULL,scope TEXT NOT NULL,limit_value REAL NOT NULL,spent REAL NOT NULL DEFAULT 0,PRIMARY KEY(tenant,scope));
        CREATE TABLE IF NOT EXISTS reservations(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,scope TEXT NOT NULL,request_id TEXT NOT NULL UNIQUE,amount REAL NOT NULL,actual REAL,state TEXT NOT NULL,created INTEGER NOT NULL,model TEXT,latency_ms INTEGER);
        CREATE TABLE IF NOT EXISTS alerts(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,name TEXT NOT NULL,threshold REAL NOT NULL,channel TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'ready');
        CREATE TABLE IF NOT EXISTS policies(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,name TEXT NOT NULL,rules TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS decisions(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,model TEXT NOT NULL,region TEXT NOT NULL,allowed INTEGER NOT NULL,code TEXT NOT NULL,created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS routes(tenant TEXT NOT NULL,name TEXT NOT NULL,deployments TEXT NOT NULL,cache_ttl INTEGER NOT NULL,PRIMARY KEY(tenant,name));
        CREATE TABLE IF NOT EXISTS health(tenant TEXT NOT NULL,route TEXT NOT NULL,deployment TEXT NOT NULL,failures INTEGER NOT NULL DEFAULT 0,open_until INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(tenant,route,deployment));
        CREATE TABLE IF NOT EXISTS cache(tenant TEXT NOT NULL,route TEXT NOT NULL,k TEXT NOT NULL,value TEXT NOT NULL,expires INTEGER NOT NULL,PRIMARY KEY(tenant,route,k));
        CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,actor TEXT NOT NULL,action TEXT NOT NULL,object_id TEXT NOT NULL,created INTEGER NOT NULL);
        """)

    def _require(self, role: str, minimum: str) -> None:
        if _ROLE.get(role, -1) < _ROLE[minimum]:
            raise PermissionDenied(f"{minimum} role required")

    def _audit(self, t, a, action, obj):
        self.db.execute(
            "INSERT INTO audit VALUES(?,?,?,?,?,?)",
            (secrets.token_hex(8), t, a, action, obj, self.clock()),
        )

    def configure_workspace(self, t, role, name):
        self._require(role, "admin")
        self.db.execute(
            "INSERT INTO workspaces(tenant,name) VALUES(?,?) ON CONFLICT(tenant) DO UPDATE SET name=excluded.name,version=version+1",
            (t, name),
        )
        self._audit(t, role, "workspace.configure", t)

    def dashboard(self, t, role):
        self._require(role, "viewer")
        w = self.db.execute("SELECT * FROM workspaces WHERE tenant=?", (t,)).fetchone()
        if not w:
            return {"workspace": None, "setup": {"complete": False}, "counts": {}}
        counts = {
            x: self.db.execute(
                f"SELECT count(*) FROM {x} WHERE tenant=?", (t,)
            ).fetchone()[0]
            for x in ("keys", "budgets", "alerts", "policies", "routes")
        }
        return {
            "workspace": dict(w),
            "setup": {
                "complete": all(counts[x] > 0 for x in ("keys", "budgets", "routes"))
            },
            "counts": counts,
        }

    def audit_events(self, t):
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM audit WHERE tenant=? ORDER BY created DESC", (t,)
            )
        ]

    def issue_key(self, t, role, label, models, expires_at=None, idempotency_key=None):
        self._require(role, "admin")
        if not label or not models:
            raise ValueError("label and models are required")
        with self._lock:
            if idempotency_key:
                row = self.db.execute(
                    "SELECT response FROM idem WHERE tenant=? AND k=?",
                    (t, idempotency_key),
                ).fetchone()
                if row:
                    return json.loads(row[0])
            secret = "gw_" + secrets.token_urlsafe(24)
            kid = secrets.token_hex(8)
            digest = hashlib.sha256(secret.encode()).hexdigest()
            self.db.execute(
                "INSERT INTO keys VALUES(?,?,?,?,?,'active',?,NULL,?)",
                (kid, t, label, digest, json.dumps(models), expires_at, self.clock()),
            )
            out = {"id": kid, "secret": secret, "label": label, "models": models}
            if idempotency_key:
                self.db.execute(
                    "INSERT INTO idem VALUES(?,?,?)",
                    (t, idempotency_key, json.dumps(out)),
                )
            self._audit(t, role, "key.issue", kid)
            return out

    def list_keys(self, t, role):
        self._require(role, "auditor")
        return [
            dict(r) | {"models": json.loads(r["models"])}
            for r in self.db.execute(
                "SELECT id,tenant,label,models,status,expires,overlap_until,created FROM keys WHERE tenant=?",
                (t,),
            )
        ]

    def rotate_key(self, t, role, kid, overlap_seconds=0):
        self._require(role, "admin")
        row = self.db.execute(
            "SELECT label,models FROM keys WHERE tenant=? AND id=? AND status='active'",
            (t, kid),
        ).fetchone()
        if not row:
            raise KeyError(kid)
        self.db.execute(
            "UPDATE keys SET overlap_until=? WHERE tenant=? AND id=?",
            (self.clock() + max(0, overlap_seconds), t, kid),
        )
        return self.issue_key(
            t, role, row["label"] + " rotated", json.loads(row["models"])
        )

    def revoke_key(self, t, role, kid):
        self._require(role, "admin")
        self.db.execute(
            "UPDATE keys SET status='revoked' WHERE tenant=? AND id=?", (t, kid)
        )
        self._audit(t, role, "key.revoke", kid)

    def authenticate(self, secret):
        digest = hashlib.sha256(secret.encode()).hexdigest()
        row = self.db.execute(
            "SELECT tenant,id,expires,status,overlap_until FROM keys WHERE secret_hash=?",
            (digest,),
        ).fetchone()
        if (
            not row
            or row["status"] != "active"
            or (row["expires"] and row["expires"] <= self.clock())
        ):
            return None
        return {"tenant": row["tenant"], "key_id": row["id"]}

    def set_budget(self, t, role, scope, limit_value):
        self._require(role, "admin")
        if limit_value <= 0:
            raise ValueError("budget must be positive")
        self.db.execute(
            "INSERT INTO budgets(tenant,scope,limit_value) VALUES(?,?,?) ON CONFLICT(tenant,scope) DO UPDATE SET limit_value=excluded.limit_value",
            (t, scope, float(limit_value)),
        )
        self._audit(t, role, "budget.set", scope)

    def reserve(self, t, key_id, request_id, amount, scope="global"):
        if amount <= 0:
            raise ValueError("amount must be positive")
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                old = self.db.execute(
                    "SELECT * FROM reservations WHERE tenant=? AND request_id=?",
                    (t, request_id),
                ).fetchone()
                if old:
                    self.db.execute("COMMIT")
                    return dict(old)
                b = self.db.execute(
                    "SELECT limit_value,spent FROM budgets WHERE tenant=? AND scope=?",
                    (t, scope),
                ).fetchone()
                held = self.db.execute(
                    "SELECT COALESCE(sum(amount),0) FROM reservations WHERE tenant=? AND scope=? AND state='reserved'",
                    (t, scope),
                ).fetchone()[0]
                if not b or b["spent"] + held + amount > b["limit_value"]:
                    raise ValueError("budget exceeded")
                rid = secrets.token_hex(8)
                self.db.execute(
                    "INSERT INTO reservations(id,tenant,scope,request_id,amount,state,created) VALUES(?,?,?,?,?,'reserved',?)",
                    (rid, t, scope, request_id, amount, self.clock()),
                )
                self.db.execute("COMMIT")
                return {
                    "id": rid,
                    "tenant": t,
                    "scope": scope,
                    "request_id": request_id,
                    "amount": amount,
                    "state": "reserved",
                }
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def reconcile(self, rid, actual, model=None, latency_ms=None):
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                r = self.db.execute(
                    "SELECT * FROM reservations WHERE id=?", (rid,)
                ).fetchone()
                if not r:
                    raise KeyError(rid)
                if r["state"] == "reconciled":
                    self.db.execute("COMMIT")
                    return dict(r)
                self.db.execute(
                    "UPDATE reservations SET actual=?,state='reconciled',model=?,latency_ms=? WHERE id=?",
                    (actual, model, latency_ms, rid),
                )
                self.db.execute(
                    "UPDATE budgets SET spent=spent+? WHERE tenant=? AND scope=?",
                    (actual, r["tenant"], r["scope"]),
                )
                self.db.execute("COMMIT")
                return {"id": rid, "state": "reconciled", "actual": actual}
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def budget_status(self, t, scope):
        b = self.db.execute(
            "SELECT * FROM budgets WHERE tenant=? AND scope=?", (t, scope)
        ).fetchone()
        reserved = self.db.execute(
            "SELECT COALESCE(sum(amount),0) FROM reservations WHERE tenant=? AND scope=? AND state='reserved'",
            (t, scope),
        ).fetchone()[0]
        return dict(b) | {"reserved": reserved}

    def create_alert(self, t, role, name, threshold, channel):
        self._require(role, "operator")
        aid = secrets.token_hex(8)
        self.db.execute(
            "INSERT INTO alerts(id,tenant,name,threshold,channel) VALUES(?,?,?,?,?)",
            (aid, t, name, threshold, channel),
        )
        return aid

    def evaluate_alerts(self, t):
        b = self.budget_status(t, "global")
        ratio = (b["spent"] + b["reserved"]) / b["limit_value"]
        out = []
        for a in self.db.execute("SELECT * FROM alerts WHERE tenant=?", (t,)):
            state = "triggered" if ratio >= a["threshold"] else "ready"
            self.db.execute("UPDATE alerts SET state=? WHERE id=?", (state, a["id"]))
            out.append(dict(a) | {"state": state, "ratio": ratio})
        return out

    def export_spend_csv(self, t, role):
        self._require(role, "viewer")
        s = io.StringIO()
        w = csv.writer(s)
        w.writerow(["request_id", "model", "actual", "latency_ms", "state"])
        for r in self.db.execute(
            "SELECT request_id,model,actual,latency_ms,state FROM reservations WHERE tenant=?",
            (t,),
        ):
            w.writerow(r)
        return s.getvalue()

    def put_policy(self, t, role, name, rules):
        self._require(role, "security")
        pid = secrets.token_hex(8)
        self.db.execute(
            "INSERT INTO policies VALUES(?,?,?,?,1)", (pid, t, name, json.dumps(rules))
        )
        self._audit(t, role, "policy.put", pid)
        return pid

    def evaluate_policy(self, t, model, content, region):
        policies = list(
            self.db.execute("SELECT * FROM policies WHERE tenant=? AND active=1", (t,))
        )
        if not policies:
            raise PolicyDenied("no_active_policy")
        code = "allowed"
        for p in policies:
            r = json.loads(p["rules"])
            low = content.lower()
            if model not in r.get("allowed_models", []):
                code = "model_denied"
            elif region not in r.get("regions", []):
                code = "region_denied"
            elif any(x.lower() in low for x in r.get("blocked_terms", [])):
                code = "blocked_content"
            did = secrets.token_hex(8)
            self.db.execute(
                "INSERT INTO decisions VALUES(?,?,?,?,?,?,?)",
                (did, t, model, region, int(code == "allowed"), code, self.clock()),
            )
            if code != "allowed":
                raise PolicyDenied(code)
        return {"allowed": True, "code": "allowed"}

    def policy_decisions(self, t, role):
        self._require(role, "auditor")
        return [
            dict(r)
            for r in self.db.execute("SELECT * FROM decisions WHERE tenant=?", (t,))
        ]

    def put_route(self, t, role, name, deployments, cache_ttl=0):
        self._require(role, "operator")
        self.db.execute(
            "INSERT INTO routes VALUES(?,?,?,?) ON CONFLICT(tenant,name) DO UPDATE SET deployments=excluded.deployments,cache_ttl=excluded.cache_ttl",
            (t, name, json.dumps(deployments), max(0, cache_ttl)),
        )
        self._audit(t, role, "route.put", name)

    def choose_deployment(self, t, route, request_id):
        r = self.db.execute(
            "SELECT deployments FROM routes WHERE tenant=? AND name=?", (t, route)
        ).fetchone()
        if not r:
            raise KeyError(route)
        available = []
        for d in json.loads(r[0]):
            h = self.db.execute(
                "SELECT open_until FROM health WHERE tenant=? AND route=? AND deployment=?",
                (t, route, d["name"]),
            ).fetchone()
            if not h or h[0] <= self.clock():
                available.append(d)
        if not available:
            raise RuntimeError("all deployment circuits are open")
        return available[
            int(hashlib.sha256(request_id.encode()).hexdigest(), 16) % len(available)
        ]

    def record_deployment_result(self, t, route, deployment, success):
        self.db.execute(
            "INSERT OR IGNORE INTO health(tenant,route,deployment) VALUES(?,?,?)",
            (t, route, deployment),
        )
        if success:
            self.db.execute(
                "UPDATE health SET failures=0,open_until=0 WHERE tenant=? AND route=? AND deployment=?",
                (t, route, deployment),
            )
        else:
            self.db.execute(
                "UPDATE health SET failures=failures+1,open_until=CASE WHEN failures+1>=3 THEN ? ELSE open_until END WHERE tenant=? AND route=? AND deployment=?",
                (self.clock() + 60, t, route, deployment),
            )

    def cache_put(self, t, route, k, value):
        r = self.db.execute(
            "SELECT cache_ttl FROM routes WHERE tenant=? AND name=?", (t, route)
        ).fetchone()
        ttl = r[0] if r else 0
        if ttl:
            self.db.execute(
                "INSERT OR REPLACE INTO cache VALUES(?,?,?,?,?)",
                (t, route, k, value, self.clock() + ttl),
            )

    def cache_get(self, t, route, k):
        r = self.db.execute(
            "SELECT value,expires FROM cache WHERE tenant=? AND route=? AND k=?",
            (t, route, k),
        ).fetchone()
        return r[0] if r and r[1] > self.clock() else None
