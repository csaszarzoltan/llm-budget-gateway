"""Prioritized multi-model routes with per-target timezone schedules."""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SAFE_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}


class PriorityRouteStore:
    """Persist, version and evaluate ordered model fallback chains."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS priority_routes(id TEXT PRIMARY KEY,name TEXT UNIQUE NOT NULL,draft_version INTEGER NOT NULL,published_version INTEGER,status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS priority_route_versions(route_id TEXT NOT NULL,version INTEGER NOT NULL,config_json TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(route_id,version));
        CREATE TABLE IF NOT EXISTS priority_route_spend(route_name TEXT NOT NULL,model TEXT NOT NULL,period TEXT NOT NULL,spend REAL NOT NULL,PRIMARY KEY(route_name,model,period));
        CREATE TABLE IF NOT EXISTS priority_route_health(route_name TEXT NOT NULL,model TEXT NOT NULL,healthy INTEGER NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(route_name,model));
        """)
        connection.commit()

    def create_route(self, name: str, targets: list[dict[str, Any]]) -> dict[str, Any]:
        """Create a validated priority route as draft version one."""
        config = _validate(name, targets)
        route_id = f"chain_{secrets.token_hex(6)}"
        try:
            with self.connection:
                self.connection.execute(
                    "INSERT INTO priority_routes VALUES(?,?,1,NULL,'draft')",
                    (route_id, config["name"]),
                )
                self.connection.execute(
                    "INSERT INTO priority_route_versions VALUES(?,?,?,?)",
                    (route_id, 1, _json(config), _now()),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("route name already exists") from exc
        return self.get_route(route_id)

    def update_route(
        self, route_id: str, name: str, targets: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Create a new immutable draft while retaining the published version."""
        route = self.get_route(route_id)
        config = _validate(name, targets)
        version = int(route["draft_version"]) + 1
        with self.connection:
            self.connection.execute(
                "UPDATE priority_routes SET name=?,draft_version=?,status='draft' WHERE id=?",
                (config["name"], version, route_id),
            )
            self.connection.execute(
                "INSERT INTO priority_route_versions VALUES(?,?,?,?)",
                (route_id, version, _json(config), _now()),
            )
        return self.get_route(route_id)

    def get_route(self, route_id: str) -> dict[str, Any]:
        """Return one route with draft and published configuration."""
        row = self.connection.execute(
            "SELECT id,name,draft_version,published_version,status FROM priority_routes WHERE id=?",
            (route_id,),
        ).fetchone()
        if row is None:
            raise KeyError(route_id)
        return {
            "id": row[0],
            "name": row[1],
            "draft_version": row[2],
            "published_version": row[3],
            "status": row[4],
            "draft": self._config(route_id, int(row[2])),
            "published": self._config(route_id, int(row[3]))
            if row[3] is not None
            else None,
        }

    def list_routes(self) -> list[dict[str, Any]]:
        """List routes in name order."""
        return [
            self.get_route(row[0])
            for row in self.connection.execute(
                "SELECT id FROM priority_routes ORDER BY name"
            ).fetchall()
        ]

    def publish(self, route_id: str) -> dict[str, Any]:
        """Atomically activate the current draft."""
        self.get_route(route_id)
        with self.connection:
            self.connection.execute(
                "UPDATE priority_routes SET published_version=draft_version,status='active' WHERE id=?",
                (route_id,),
            )
        return self.get_route(route_id)

    def resolve(
        self, name: str, *, at: datetime, capabilities: list[str]
    ) -> dict[str, Any]:
        """Resolve the first eligible target and explain every exclusion."""
        row = self.connection.execute(
            "SELECT id,published_version FROM priority_routes WHERE name=? AND published_version IS NOT NULL",
            (name,),
        ).fetchone()
        if row is None:
            raise KeyError(name)
        config = self._config(str(row[0]), int(row[1]))
        eligible: list[dict[str, Any]] = []
        excluded: list[dict[str, str]] = []
        for target in config["targets"]:
            reason = self._excluded(name, target, at, set(capabilities))
            if reason:
                excluded.append({"model": target["model"], "reason": reason})
            else:
                eligible.append(target)
        if not eligible:
            raise RuntimeError("no eligible target in priority route")
        return {
            "route": name,
            "route_id": row[0],
            "route_version": row[1],
            "selected_model": eligible[0]["model"],
            "selected_priority": eligible[0]["priority"],
            "attempt_order": [x["model"] for x in eligible],
            "eligible_targets": eligible,
            "excluded": excluded,
            "attempt_index": 0,
            "decision_id": f"dec_{secrets.token_hex(8)}",
        }

    def next_after_failure(
        self, decision: dict[str, Any], *, status_code: int
    ) -> dict[str, Any]:
        """Advance to the next target when the current target permits this status."""
        current = decision["eligible_targets"][int(decision["attempt_index"])]
        if status_code not in current["fallback_statuses"]:
            raise ValueError(f"status {status_code} is not configured for fallback")
        index = int(decision["attempt_index"]) + 1
        if index >= len(decision["eligible_targets"]):
            raise RuntimeError("fallback chain exhausted")
        return {
            **decision,
            "selected_model": decision["eligible_targets"][index]["model"],
            "selected_priority": decision["eligible_targets"][index]["priority"],
            "attempt_index": index,
            "fallback_reason": f"provider_status_{status_code}",
        }

    def record_spend(
        self, route: str, model: str, amount: float, *, at: datetime
    ) -> None:
        """Add actual serving cost to a model's calendar-month budget."""
        if amount < 0:
            raise ValueError("spend must be non-negative")
        with self.connection:
            self.connection.execute(
                "INSERT INTO priority_route_spend VALUES(?,?,?,?) ON CONFLICT(route_name,model,period) DO UPDATE SET spend=spend+excluded.spend",
                (route, model, at.strftime("%Y-%m"), amount),
            )

    def set_health(self, route: str, model: str, healthy: bool) -> None:
        """Set runtime health eligibility for a route target."""
        with self.connection:
            self.connection.execute(
                "INSERT INTO priority_route_health VALUES(?,?,?,?) ON CONFLICT(route_name,model) DO UPDATE SET healthy=excluded.healthy,updated_at=excluded.updated_at",
                (route, model, int(healthy), _now()),
            )

    def _excluded(
        self, route: str, target: dict[str, Any], at: datetime, capabilities: set[str]
    ) -> str | None:
        if not target["enabled"]:
            return "disabled"
        local = at.astimezone(ZoneInfo(target["timezone"]))
        if local.weekday() not in target["days"] or not _in_window(
            local.strftime("%H:%M"), target["start"], target["end"]
        ):
            return "outside_schedule"
        spend_row = self.connection.execute(
            "SELECT spend FROM priority_route_spend WHERE route_name=? AND model=? AND period=?",
            (route, target["model"], local.strftime("%Y-%m")),
        ).fetchone()
        if spend_row and float(spend_row[0]) >= float(target["monthly_budget"]):
            return "budget_exhausted"
        health = self.connection.execute(
            "SELECT healthy FROM priority_route_health WHERE route_name=? AND model=?",
            (route, target["model"]),
        ).fetchone()
        if health and not bool(health[0]):
            return "unhealthy"
        if not set(target["required_capabilities"]).issubset(capabilities):
            return "missing_capabilities"
        return None

    def _config(self, route_id: str, version: int) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT config_json FROM priority_route_versions WHERE route_id=? AND version=?",
            (route_id, version),
        ).fetchone()
        if row is None:
            raise KeyError((route_id, version))
        return json.loads(row[0])


def _validate(name: str, targets: list[dict[str, Any]]) -> dict[str, Any]:
    if not name.strip():
        raise ValueError("route name is required")
    if not targets:
        raise ValueError("at least one target is required")
    clean: list[dict[str, Any]] = []
    priorities: set[int] = set()
    for raw in targets:
        item = dict(raw)
        model = str(item.get("model", "")).strip()
        priority = int(item.get("priority", 0))
        if not model:
            raise ValueError("target model is required")
        if priority <= 0 or priority in priorities:
            raise ValueError("each target priority must be positive and unique")
        priorities.add(priority)
        timezone = str(item.get("timezone", "UTC"))
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown target timezone") from exc
        start, end = str(item.get("start", "00:00")), str(item.get("end", "23:59"))
        if not _valid_time(start) or not _valid_time(end):
            raise ValueError("target time must use valid HH:MM format")
        days = sorted({int(day) for day in item.get("days", range(7))})
        if not days or any(day < 0 or day > 6 for day in days):
            raise ValueError("target days must be between zero and six")
        statuses = sorted(
            {
                int(code)
                for code in item.get("fallback_statuses", [429, 500, 502, 503, 504])
            }
        )
        if not statuses or not set(statuses).issubset(SAFE_STATUSES):
            raise ValueError("fallback statuses must be retry-safe")
        budget = float(item.get("monthly_budget", 0))
        if budget <= 0:
            raise ValueError("target monthly budget must be positive")
        clean.append(
            {
                "id": str(item.get("id") or f"target_{secrets.token_hex(5)}"),
                "model": model,
                "priority": priority,
                "timezone": timezone,
                "days": days,
                "start": start,
                "end": end,
                "monthly_budget": budget,
                "enabled": bool(item.get("enabled", True)),
                "fallback_statuses": statuses,
                "required_capabilities": sorted(
                    {str(x) for x in item.get("required_capabilities", [])}
                ),
            }
        )
    clean.sort(key=lambda target: target["priority"])
    return {"name": name.strip(), "targets": clean}


def _valid_time(value: str) -> bool:
    try:
        hour, minute = map(int, value.split(":"))
    except (ValueError, TypeError):
        return False
    return 0 <= hour <= 23 and 0 <= minute <= 59 and len(value) == 5


def _in_window(value: str, start: str, end: str) -> bool:
    return start <= value < end if start < end else value >= start or value < end


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _now() -> str:
    return datetime.now(UTC).isoformat()
