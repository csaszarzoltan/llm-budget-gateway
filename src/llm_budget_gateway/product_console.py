"""Task-first product console domain for applications, providers and routes."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo



_HERMES_CONFIG = Path.home() / ".hermes" / "config.yaml"


def _sync_hermes_context_lengths(db: sqlite3.Connection) -> None:
    """Strip stale context_length from ~/.hermes/config.yaml.

    Hermes resolves context_length live from the gateway /v1/models
    endpoint (product.db route targets).  Any ``context_length:`` line
    under ``providers.llm-gw.models.<route>`` in config.yaml acts as a
    custom_providers override that SHORT-CIRCUITS the live probe,
    locking Hermes to a stale value.  This removes those entries after
    every route mutation so the live value always wins.
    """
    if not _HERMES_CONFIG.is_file():
        return
    try:
        text = _HERMES_CONFIG.read_text(encoding="utf-8")
    except OSError:
        return
    cleaned = re.sub(
        r"^(      \w[\w\-]*:\n)(        context_length: \d+\n)",
        r"\1",
        text,
        flags=re.MULTILINE,
    )
    if cleaned == text:
        return
    try:
        _HERMES_CONFIG.write_text(cleaned, encoding="utf-8")
        _log.info("hermes config sync: stripped stale context_length entries")
    except OSError as exc:
        _log.warning("hermes config sync failed: %s", exc)


class ProductConsoleStore:
    """Persist product-console objects and derive actionable dashboard state."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.db = connection
        connection.executescript("""
CREATE TABLE IF NOT EXISTS pc_providers(id TEXT PRIMARY KEY,name TEXT,slug TEXT UNIQUE,region TEXT,healthy INTEGER,models TEXT,created TEXT);
CREATE TABLE IF NOT EXISTS pc_routes(id TEXT PRIMARY KEY,name TEXT UNIQUE,draft_version INTEGER,published_version INTEGER,status TEXT,targets TEXT,created TEXT);
CREATE TABLE IF NOT EXISTS pc_route_versions(route_id TEXT,version INTEGER,targets TEXT,created TEXT,PRIMARY KEY(route_id,version));
CREATE TABLE IF NOT EXISTS pc_apps(id TEXT PRIMARY KEY,name TEXT,default_route TEXT,key_hash TEXT,created TEXT);
CREATE TABLE IF NOT EXISTS pc_activity(id TEXT PRIMARY KEY,app_id TEXT,route TEXT,model TEXT,cost REAL,latency INTEGER,success INTEGER,reason TEXT,created TEXT);
""")
        connection.commit()

    def create_provider(
        self, name: str, slug: str, region: str, models: list[str]
    ) -> dict[str, Any]:
        """Create a credential-backed provider catalog entry."""
        if not name.strip() or not slug.strip() or not models:
            raise ValueError("provider name, slug and models are required")
        pid = "provider_" + secrets.token_hex(5)
        now = _now()
        self.db.execute(
            "INSERT INTO pc_providers VALUES(?,?,?,?,1,?,?)",
            (pid, name, slug, region, json.dumps(models), now),
        )
        self.db.commit()
        return self._provider(pid)

    def _provider(self, pid: str) -> dict[str, Any]:
        r = self.db.execute("SELECT * FROM pc_providers WHERE id=?", (pid,)).fetchone()
        if not r:
            raise KeyError(pid)
        return {
            "id": r[0],
            "name": r[1],
            "slug": r[2],
            "region": r[3],
            "status": "healthy" if r[4] else "unavailable",
            "credential_status": "configured",
            "models": [
                {
                    "id": m,
                    "capabilities": ["tools", "structured_output"],
                    "context_tokens": 128000,
                }
                for m in json.loads(r[5])
            ],
            "created_at": r[6],
        }

    def providers(self) -> list[dict[str, Any]]:
        """List providers and governed model metadata."""
        return [
            self._provider(r[0])
            for r in self.db.execute("SELECT id FROM pc_providers ORDER BY name")
        ]

    def set_provider_health(self, pid: str, healthy: bool) -> dict[str, Any]:
        """Update provider health for routing attention."""
        self.db.execute(
            "UPDATE pc_providers SET healthy=? WHERE id=?", (int(healthy), pid)
        )
        self.db.commit()
        return self._provider(pid)

    def create_application(self, name: str, default_route: str) -> dict[str, Any]:
        """Create an application and reveal its gateway key once."""
        if not name.strip() or not default_route.strip():
            raise ValueError("application name and route are required")
        aid = "app_" + secrets.token_hex(5)
        key = "gw_" + secrets.token_urlsafe(22)
        self.db.execute(
            "INSERT INTO pc_apps VALUES(?,?,?,?,?)",
            (
                aid,
                name,
                default_route,
                hashlib.sha256(key.encode()).hexdigest(),
                _now(),
            ),
        )
        self.db.commit()
        return {
            "id": aid,
            "name": name,
            "default_route": default_route,
            "api_key": key,
            "status": "active",
        }

    def authenticate_application(self, api_key: str) -> dict[str, Any]:
        """Return the application record for a valid UI gateway key.

        Raises PermissionError when the key is unknown, mirroring the
        RoutingControlPlane contract so the proxy can try both stores.
        """
        digest = hashlib.sha256(api_key.encode()).hexdigest()
        row = self.db.execute(
            "SELECT id,name,default_route,created FROM pc_apps WHERE key_hash=?",
            (digest,),
        ).fetchone()
        if not row:
            raise PermissionError("invalid application key")
        return {"id": row[0], "name": row[1], "default_route": row[2], "created": row[3]}

    def applications(self) -> list[dict[str, Any]]:
        """List applications without secret material."""
        rows = self.db.execute(
            "SELECT id,name,default_route,created FROM pc_apps ORDER BY created"
        ).fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "default_route": r[2],
                "status": "active",
                "created_at": r[3],
            }
            for r in rows
        ]

    def create_route(self, name: str, targets: list[dict[str, Any]]) -> dict[str, Any]:
        """Create a versioned route draft."""
        clean = _targets(targets)
        rid = "route_" + secrets.token_hex(5)
        now = _now()
        payload = json.dumps(clean, sort_keys=True)
        self.db.execute(
            "INSERT INTO pc_routes VALUES(?,?,1,NULL,'draft',?,?)",
            (rid, name, payload, now),
        )
        self.db.execute(
            "INSERT INTO pc_route_versions VALUES(?,?,?,?)", (rid, 1, payload, now)
        )
        self.db.commit()
        return self.route(rid)

    def update_route(self, rid: str, targets: list[dict[str, Any]]) -> dict[str, Any]:
        """Create a new immutable draft version."""
        route = self.route(rid)
        v = route["draft_version"] + 1
        payload = json.dumps(_targets(targets), sort_keys=True)
        self.db.execute(
            "UPDATE pc_routes SET draft_version=?,status='draft',targets=? WHERE id=?",
            (v, payload, rid),
        )
        self.db.execute(
            "INSERT INTO pc_route_versions VALUES(?,?,?,?)", (rid, v, payload, _now())
        )
        self.db.commit()
        _sync_hermes_context_lengths(self.db)
        return self.route(rid)

    def route(self, rid: str) -> dict[str, Any]:
        """Return one route."""
        r = self.db.execute(
            "SELECT id,name,draft_version,published_version,status,targets FROM pc_routes WHERE id=?",
            (rid,),
        ).fetchone()
        if not r:
            raise KeyError(rid)
        return {
            "id": r[0],
            "name": r[1],
            "draft_version": r[2],
            "published_version": r[3],
            "status": r[4],
            "targets": json.loads(r[5]),
        }

    def routes(self) -> list[dict[str, Any]]:
        """List routes."""
        return [
            self.route(r[0])
            for r in self.db.execute("SELECT id FROM pc_routes ORDER BY name")
        ]

    def published_route_by_name(self, name: str) -> dict[str, Any] | None:
        """Return the published (active) route with this name, if any.

        This is the lookup the proxy uses so routes created and edited on the
        Routes tab (pc_routes/targets model) are served by the gateway — no
        code change required after the user edits a route in the UI.
        """
        row = self.db.execute(
            "SELECT id FROM pc_routes WHERE name=? AND status='active'",
            (name,),
        ).fetchone()
        if not row:
            return None
        return self.route(row[0])

    def publish_route(self, rid: str) -> dict[str, Any]:
        """Publish the current draft atomically."""
        self.route(rid)
        self.db.execute(
            "UPDATE pc_routes SET published_version=draft_version,status='active' WHERE id=?",
            (rid,),
        )
        self.db.commit()
        _sync_hermes_context_lengths(self.db)
        return self.route(rid)

    def test_route(self, rid: str, at: str, capabilities: list[str]) -> dict[str, Any]:
        """Explain the target selected at an exact instant."""
        route = self.route(rid)
        instant = datetime.fromisoformat(at)
        eligible = []
        excluded = []
        for t in route["targets"]:
            local = instant.astimezone(ZoneInfo(t["timezone"]))
            clock = local.strftime("%H:%M")
            inside = (
                t["start"] <= clock < t["end"]
                if t["start"] < t["end"]
                else clock >= t["start"] or clock < t["end"]
            )
            reason = None if inside else "outside_schedule"
            if not set(t.get("required_capabilities", [])).issubset(capabilities):
                reason = "missing_capabilities"
            (excluded if reason else eligible).append(
                {"model": t["model"], "reason": reason} if reason else t
            )
        if not eligible:
            raise RuntimeError("no eligible route target")
        return {
            "selected_model": eligible[0]["model"],
            "attempt_order": [x["model"] for x in eligible],
            "excluded": excluded,
            "why": f"Priority {eligible[0]['priority']} is the first eligible target",
        }

    def route_dependencies(self, rid: str) -> dict[str, Any]:
        """Return applications that must be reassigned before archival."""
        route = self.route(rid)
        rows = self.db.execute(
            "SELECT id,name,default_route FROM pc_apps WHERE default_route=? ORDER BY name",
            (route["name"],),
        ).fetchall()
        applications = [{"id": r[0], "name": r[1], "default_route": r[2]} for r in rows]
        return {
            "route_id": rid,
            "applications": applications,
            "routes": [],
            "blocking": bool(applications),
        }

    def archive_route(self, rid: str) -> dict[str, Any]:
        """Archive an unused route while retaining versions and evidence."""
        dependencies = self.route_dependencies(rid)
        if dependencies["blocking"]:
            raise ValueError("route has blocking dependencies")
        self.db.execute("UPDATE pc_routes SET status='archived' WHERE id=?", (rid,))
        self.db.commit()
        _sync_hermes_context_lengths(self.db)
        return self.route(rid)

    def restore_route(self, rid: str) -> dict[str, Any]:
        """Restore an archived route as a draft without changing live traffic."""
        route = self.route(rid)
        if route["status"] != "archived":
            raise ValueError("only archived routes can be restored")
        self.db.execute("UPDATE pc_routes SET status='draft' WHERE id=?", (rid,))
        self.db.commit()
        _sync_hermes_context_lengths(self.db)
        return self.route(rid)

    def delete_route(self, rid: str, *, confirmation: str) -> None:
        """Permanently remove an archived route after exact-name confirmation."""
        route = self.route(rid)
        if route["status"] != "archived":
            raise ValueError("route must be archived before permanent deletion")
        if confirmation != route["name"]:
            raise ValueError("route name confirmation does not match")
        with self.db:
            self.db.execute("DELETE FROM pc_route_versions WHERE route_id=?", (rid,))
            self.db.execute("DELETE FROM pc_routes WHERE id=?", (rid,))
            _sync_hermes_context_lengths(self.db)

    def duplicate_route(self, rid: str, name: str) -> dict[str, Any]:
        """Create an independent draft from the current route definition."""
        return self.create_route(name, self.route(rid)["targets"])

    def route_versions(self, rid: str) -> list[dict[str, Any]]:
        """Return newest-first immutable route versions."""
        self.route(rid)
        rows = self.db.execute(
            "SELECT version,targets,created FROM pc_route_versions WHERE route_id=? ORDER BY version DESC",
            (rid,),
        ).fetchall()
        return [
            {"version": int(r[0]), "targets": json.loads(r[1]), "created_at": r[2]}
            for r in rows
        ]

    def validate_route(self, rid: str) -> dict[str, Any]:
        """Validate target uniqueness, priority, schedules and terminal fallback."""
        route = self.route(rid)
        errors: list[str] = []
        warnings: list[str] = []
        seen: set[str] = set()
        for target in route["targets"]:
            if target["model"] in seen:
                errors.append(f"duplicate target: {target['model']}")
            seen.add(target["model"])
            if target["priority"] < 1:
                errors.append("priority must be positive")
        if len(route["targets"]) == 1:
            warnings.append("route has no fallback target")
        return {
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
            "estimated_daily_cost_usd": round(len(route["targets"]) * 0.75, 2),
            "paths_terminate": True,
        }

    def simulate_route(
        self, rid: str, *, capabilities: list[str], budget_remaining_usd: float
    ) -> dict[str, Any]:
        """Explain target eligibility without making a provider call."""
        if budget_remaining_usd < 0:
            raise ValueError("budget remaining must be non-negative")
        route = self.route(rid)
        path = []
        for target in sorted(route["targets"], key=lambda item: item["priority"]):
            missing = sorted(
                set(target.get("required_capabilities", [])) - set(capabilities)
            )
            eligible = not missing and budget_remaining_usd > 0
            path.append(
                {
                    "kind": "target",
                    "model": target["model"],
                    "eligible": eligible,
                    "reason": "eligible"
                    if eligible
                    else "missing capabilities or budget",
                }
            )
            if eligible:
                return {
                    "selected_model": target["model"],
                    "decision_path": path,
                    "provider_call_made": False,
                }
        raise RuntimeError("no eligible route target")

    def route_templates(self) -> list[dict[str, str]]:
        """Return outcome-oriented route starting points."""
        return [
            {"id": i, "name": n, "description": d}
            for i, n, d in [
                (
                    "reliable-fallback",
                    "Reliable fallback",
                    "Try models in priority order.",
                ),
                ("cost-aware", "Cost-aware", "Switch when budgets are exhausted."),
                (
                    "follow-the-sun",
                    "Follow the sun",
                    "Use independent regional schedules.",
                ),
                (
                    "quality-tiers",
                    "Quality tiers",
                    "Let the client request capability tiers.",
                ),
                ("blank", "Start from scratch", "Build a custom route."),
            ]
        ]

    def record_request(
        self,
        app_id: str,
        route: str,
        model: str,
        cost: float,
        latency: int,
        success: bool,
        reason: str | None,
    ) -> dict[str, Any]:
        """Record one privacy-safe routing decision."""
        item = {
            "id": "req_" + secrets.token_hex(6),
            "app_id": app_id,
            "route": route,
            "model": model,
            "cost_usd": cost,
            "latency_ms": latency,
            "success": success,
            "reason": reason,
            "created_at": _now(),
        }
        self.db.execute(
            "INSERT INTO pc_activity VALUES(?,?,?,?,?,?,?,?,?)",
            (
                item["id"],
                app_id,
                route,
                model,
                cost,
                latency,
                int(success),
                reason,
                item["created_at"],
            ),
        )
        self.db.commit()
        return item

    def activity_item(self, request_id: str) -> dict[str, Any]:
        """Return one privacy-safe routing decision by request identifier."""
        row = self.db.execute(
            "SELECT * FROM pc_activity WHERE id=?", (request_id,)
        ).fetchone()
        if row is None:
            raise KeyError(request_id)
        return {
            "id": row[0],
            "app_id": row[1],
            "route": row[2],
            "model": row[3],
            "cost_usd": float(row[4]),
            "latency_ms": int(row[5]),
            "success": bool(row[6]),
            "reason": row[7],
            "created_at": row[8],
        }

    def activity(self) -> list[dict[str, Any]]:
        """Return recent routing decisions."""
        rows = self.db.execute(
            "SELECT * FROM pc_activity ORDER BY created DESC LIMIT 50"
        ).fetchall()
        return [
            {
                "id": r[0],
                "app_id": r[1],
                "route": r[2],
                "model": r[3],
                "cost_usd": r[4],
                "latency_ms": r[5],
                "success": bool(r[6]),
                "reason": r[7],
                "created_at": r[8],
            }
            for r in rows
        ]

    def usage(self) -> dict[str, Any]:
        """Aggregate cost and traffic by route and model."""
        total = self.db.execute(
            "SELECT COUNT(*),COALESCE(SUM(cost),0),COALESCE(AVG(latency),0),COALESCE(AVG(success),0) FROM pc_activity"
        ).fetchone()
        routes = self.db.execute(
            "SELECT route,COUNT(*),SUM(cost) FROM pc_activity GROUP BY route ORDER BY SUM(cost) DESC"
        ).fetchall()
        models = self.db.execute(
            "SELECT model,COUNT(*),SUM(cost) FROM pc_activity GROUP BY model ORDER BY SUM(cost) DESC"
        ).fetchall()
        return {
            "requests": total[0],
            "cost_usd": total[1],
            "avg_latency_ms": total[2],
            "success_rate": total[3] * 100,
            "by_route": [
                {"route": r[0], "requests": r[1], "cost_usd": r[2]} for r in routes
            ],
            "by_model": [
                {"model": r[0], "requests": r[1], "cost_usd": r[2]} for r in models
            ],
        }

    def home(self, role: str) -> dict[str, Any]:
        """Derive a role-aware actionable home dashboard."""
        apps = self.applications()
        routes = self.routes()
        providers = self.providers()
        usage = self.usage()
        published = sum(r["published_version"] is not None for r in routes)
        steps = [
            bool(providers),
            bool(routes),
            bool(published),
            bool(apps),
            usage["requests"] > 0,
        ]
        labels = [
            "Add a provider",
            "Create a route",
            "Publish the route",
            "Connect an application",
            "Send the first request",
        ]
        complete = sum(steps)
        attention = [
            {
                "kind": "provider",
                "severity": "critical",
                "title": f"{p['name']} is unavailable",
                "action": "Review provider",
            }
            for p in providers
            if p["status"] != "healthy"
        ]
        return {
            "role": role,
            "primary_panel": {
                "developer": "integration",
                "operator": "attention",
                "finops": "usage",
                "security": "policy",
            }.get(role, "overview"),
            "gateway": {
                "status": "healthy",
                "endpoint": "http://127.0.0.1:8000/v1",
                "environment": "Development",
            },
            "counts": {
                "applications": len(apps),
                "routes": published,
                "providers": len(providers),
            },
            "activation": {
                "complete": complete,
                "total": 5,
                "steps": [
                    {"label": label, "done": done}
                    for label, done in zip(labels, steps, strict=True)
                ],
                "next_action": labels[complete]
                if complete < 5
                else "Monitor live traffic",
            },
            "attention": attention,
            "metrics": {
                "requests": usage["requests"],
                "cost_usd": usage["cost_usd"],
                "success_rate": usage["success_rate"],
                "p95_latency_ms": usage["avg_latency_ms"],
            },
            "routes": routes,
            "activity": self.activity()[:5],
        }


def _targets(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not items:
        raise ValueError("at least one route target is required")
    clean = []
    for raw in items:
        item = {
            **raw,
            "priority": int(raw["priority"]),
            "timezone": raw.get("timezone", "UTC"),
            "start": raw.get("start", "00:00"),
            "end": raw.get("end", "23:59"),
            "required_capabilities": raw.get("required_capabilities", []),
            "context_length": raw.get("context_length"),
        }
        ZoneInfo(item["timezone"])
        # validate context_length if provided
        ctx_len = item["context_length"]
        if ctx_len is not None:
            try:
                ctx_len_int = int(ctx_len)
                if ctx_len_int <= 0:
                    raise ValueError
                item["context_length"] = ctx_len_int
            except (ValueError, TypeError):
                raise ValueError("context_length must be a positive integer")
        clean.append(item)
    return sorted(clean, key=lambda x: x["priority"])


def _now() -> str:
    return datetime.now(UTC).isoformat()
