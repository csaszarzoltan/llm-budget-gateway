"""Ten production-oriented iterations for the task-first product console."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import UTC, datetime
from typing import Any


class ProductExtensions:
    """Operational settings, lifecycle controls, audit and portability."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        db.executescript("""
CREATE TABLE IF NOT EXISTS px_keys(id TEXT PRIMARY KEY,app_id TEXT,status TEXT,key_hash TEXT,created TEXT,revoked TEXT);
CREATE TABLE IF NOT EXISTS px_budgets(scope TEXT PRIMARY KEY,limit_usd REAL,spent_usd REAL,reset_day INTEGER);
CREATE TABLE IF NOT EXISTS px_alerts(id TEXT PRIMARY KEY,name TEXT,metric TEXT,threshold REAL,enabled INTEGER,created TEXT);
CREATE TABLE IF NOT EXISTS px_environments(id TEXT PRIMARY KEY,name TEXT UNIQUE,base_url TEXT,is_default INTEGER);
CREATE TABLE IF NOT EXISTS px_views(id TEXT PRIMARY KEY,name TEXT,role TEXT,filters TEXT,created TEXT);
CREATE TABLE IF NOT EXISTS px_audit(id TEXT PRIMARY KEY,actor TEXT,action TEXT,resource TEXT,detail TEXT,created TEXT);
CREATE TABLE IF NOT EXISTS px_provider_checks(id TEXT PRIMARY KEY,provider_id TEXT,healthy INTEGER,latency_ms INTEGER,created TEXT);
CREATE TABLE IF NOT EXISTS px_route_snapshots(route_id TEXT,version INTEGER,payload TEXT,created TEXT,PRIMARY KEY(route_id,version));
CREATE TABLE IF NOT EXISTS px_archives(kind TEXT,resource_id TEXT,payload TEXT,created TEXT,PRIMARY KEY(kind,resource_id));
""")
        db.commit()

    def rotate_key(self, app_id: str) -> dict[str, Any]:
        """Revoke active keys and reveal one replacement once."""
        now = _now()
        self.db.execute(
            "UPDATE px_keys SET status='revoked',revoked=? WHERE app_id=? AND status='active'",
            (now, app_id),
        )
        key = "gw_" + secrets.token_urlsafe(24)
        kid = "key_" + secrets.token_hex(5)
        self.db.execute(
            "INSERT INTO px_keys VALUES(?,?,'active',?,?,NULL)",
            (kid, app_id, hashlib.sha256(key.encode()).hexdigest(), now),
        )
        self._audit("operator", "key.rotate", app_id, kid)
        self.db.commit()
        return {"id": kid, "app_id": app_id, "api_key": key, "status": "active"}

    def revoke_key(self, key_id: str) -> dict[str, str]:
        now = _now()
        cur = self.db.execute(
            "UPDATE px_keys SET status='revoked',revoked=? WHERE id=? AND status='active'",
            (now, key_id),
        )
        self.db.commit()
        if not cur.rowcount:
            raise KeyError(key_id)
        self._audit("operator", "key.revoke", key_id, "revoked")
        return {"id": key_id, "status": "revoked"}

    def set_budget(
        self, scope: str, limit: float, reset_day: int = 1
    ) -> dict[str, Any]:
        if limit <= 0 or not 1 <= reset_day <= 28:
            raise ValueError("budget limit and reset day are invalid")
        self.db.execute(
            "INSERT INTO px_budgets VALUES(?,?,0,?) ON CONFLICT(scope) DO UPDATE SET limit_usd=excluded.limit_usd,reset_day=excluded.reset_day",
            (scope, limit, reset_day),
        )
        self.db.commit()
        self._audit("finops", "budget.set", scope, str(limit))
        return self.budget(scope)

    def budget(self, scope: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT scope,limit_usd,spent_usd,reset_day FROM px_budgets WHERE scope=?",
            (scope,),
        ).fetchone()
        if not row:
            raise KeyError(scope)
        return {
            "scope": row[0],
            "limit_usd": row[1],
            "spent_usd": row[2],
            "remaining_usd": max(0, row[1] - row[2]),
            "percent_used": min(100, row[2] / row[1] * 100),
            "reset_day": row[3],
        }

    def add_spend(self, scope: str, amount: float) -> dict[str, Any]:
        if amount < 0:
            raise ValueError("spend must be non-negative")
        self.db.execute(
            "UPDATE px_budgets SET spent_usd=spent_usd+? WHERE scope=?", (amount, scope)
        )
        self.db.commit()
        return self.budget(scope)

    def create_alert(self, name: str, metric: str, threshold: float) -> dict[str, Any]:
        if (
            metric not in {"cost", "error_rate", "latency", "fallback_rate"}
            or threshold <= 0
        ):
            raise ValueError("invalid alert")
        aid = "alert_" + secrets.token_hex(5)
        self.db.execute(
            "INSERT INTO px_alerts VALUES(?,?,?,?,1,?)",
            (aid, name, metric, threshold, _now()),
        )
        self.db.commit()
        return {
            "id": aid,
            "name": name,
            "metric": metric,
            "threshold": threshold,
            "enabled": True,
        }

    def alerts(self) -> list[dict[str, Any]]:
        return [
            {
                "id": r[0],
                "name": r[1],
                "metric": r[2],
                "threshold": r[3],
                "enabled": bool(r[4]),
            }
            for r in self.db.execute(
                "SELECT id,name,metric,threshold,enabled FROM px_alerts ORDER BY created DESC"
            )
        ]

    def create_environment(
        self, name: str, base_url: str, default: bool = False
    ) -> dict[str, Any]:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must be HTTP or HTTPS")
        if default:
            self.db.execute("UPDATE px_environments SET is_default=0")
        eid = "env_" + secrets.token_hex(4)
        self.db.execute(
            "INSERT INTO px_environments VALUES(?,?,?,?)",
            (eid, name, base_url, int(default)),
        )
        self.db.commit()
        return {"id": eid, "name": name, "base_url": base_url, "default": default}

    def environments(self) -> list[dict[str, Any]]:
        return [
            {"id": r[0], "name": r[1], "base_url": r[2], "default": bool(r[3])}
            for r in self.db.execute(
                "SELECT id,name,base_url,is_default FROM px_environments ORDER BY is_default DESC,name"
            )
        ]

    def save_view(
        self, name: str, role: str, filters: dict[str, Any]
    ) -> dict[str, Any]:
        vid = "view_" + secrets.token_hex(5)
        self.db.execute(
            "INSERT INTO px_views VALUES(?,?,?,?,?)",
            (vid, name, role, json.dumps(filters, sort_keys=True), _now()),
        )
        self.db.commit()
        return {"id": vid, "name": name, "role": role, "filters": filters}

    def views(self, role: str) -> list[dict[str, Any]]:
        return [
            {"id": r[0], "name": r[1], "role": r[2], "filters": json.loads(r[3])}
            for r in self.db.execute(
                "SELECT id,name,role,filters FROM px_views WHERE role=? ORDER BY created DESC",
                (role,),
            )
        ]

    def provider_check(
        self, provider_id: str, healthy: bool, latency_ms: int
    ) -> dict[str, Any]:
        if latency_ms < 0:
            raise ValueError("latency must be non-negative")
        cid = "check_" + secrets.token_hex(5)
        item = {
            "id": cid,
            "provider_id": provider_id,
            "healthy": healthy,
            "latency_ms": latency_ms,
            "checked_at": _now(),
        }
        self.db.execute(
            "INSERT INTO px_provider_checks VALUES(?,?,?,?,?)",
            (cid, provider_id, int(healthy), latency_ms, item["checked_at"]),
        )
        self.db.commit()
        self._audit("operator", "provider.check", provider_id, json.dumps(item))
        return item

    def snapshot_route(
        self, route_id: str, version: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.db.execute(
            "INSERT OR REPLACE INTO px_route_snapshots VALUES(?,?,?,?)",
            (route_id, version, json.dumps(payload, sort_keys=True), _now()),
        )
        self.db.commit()
        return {"route_id": route_id, "version": version, "payload": payload}

    def rollback_route(self, route_id: str, version: int) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT payload FROM px_route_snapshots WHERE route_id=? AND version=?",
            (route_id, version),
        ).fetchone()
        if not row:
            raise KeyError((route_id, version))
        payload = json.loads(row[0])
        self._audit("operator", "route.rollback", route_id, str(version))
        return {"route_id": route_id, "restored_version": version, "payload": payload}

    def archive(
        self, kind: str, resource_id: str, payload: dict[str, Any]
    ) -> dict[str, str]:
        self.db.execute(
            "INSERT OR REPLACE INTO px_archives VALUES(?,?,?,?)",
            (kind, resource_id, json.dumps(payload, sort_keys=True), _now()),
        )
        self.db.commit()
        self._audit("operator", f"{kind}.archive", resource_id, "archived")
        return {"kind": kind, "id": resource_id, "status": "archived"}

    def export_bundle(self) -> dict[str, Any]:
        """Export non-secret portable configuration."""
        return {
            "schema": "gateway-console/v1",
            "environments": self.environments(),
            "alerts": self.alerts(),
            "budgets": [
                self.budget(r[0])
                for r in self.db.execute("SELECT scope FROM px_budgets")
            ],
            "views": [
                {"id": r[0], "name": r[1], "role": r[2], "filters": json.loads(r[3])}
                for r in self.db.execute("SELECT id,name,role,filters FROM px_views")
            ],
        }

    def import_bundle(self, bundle: dict[str, Any]) -> dict[str, int]:
        if bundle.get("schema") != "gateway-console/v1":
            raise ValueError("unsupported bundle schema")
        count = 0
        for item in bundle.get("alerts", []):
            self.create_alert(item["name"], item["metric"], float(item["threshold"]))
            count += 1
        for item in bundle.get("budgets", []):
            self.set_budget(
                item["scope"], float(item["limit_usd"]), int(item["reset_day"])
            )
            count += 1
        return {"imported": count}

    def recommendations(self) -> list[dict[str, str]]:
        out = []
        for b in self.db.execute("SELECT scope,limit_usd,spent_usd FROM px_budgets"):
            if b[1] and b[2] / b[1] >= 0.8:
                out.append(
                    {
                        "kind": "budget",
                        "severity": "warning",
                        "title": f"{b[0]} budget is {b[2] / b[1]:.0%} used",
                        "action": "Review route costs",
                    }
                )
        for p in self.db.execute(
            "SELECT provider_id,healthy FROM px_provider_checks WHERE created IN (SELECT MAX(created) FROM px_provider_checks GROUP BY provider_id)"
        ):
            if not p[1]:
                out.append(
                    {
                        "kind": "provider",
                        "severity": "critical",
                        "title": f"{p[0]} is unavailable",
                        "action": "Test credentials or fail over",
                    }
                )
        return out

    def audit(self) -> list[dict[str, str]]:
        return [
            {
                "id": r[0],
                "actor": r[1],
                "action": r[2],
                "resource": r[3],
                "detail": r[4],
                "created_at": r[5],
            }
            for r in self.db.execute(
                "SELECT * FROM px_audit ORDER BY created DESC LIMIT 100"
            )
        ]

    def _audit(self, actor: str, action: str, resource: str, detail: str) -> None:
        self.db.execute(
            "INSERT INTO px_audit VALUES(?,?,?,?,?,?)",
            ("audit_" + secrets.token_hex(6), actor, action, resource, detail, _now()),
        )
        self.db.commit()


def _now() -> str:
    return datetime.now(UTC).isoformat()
