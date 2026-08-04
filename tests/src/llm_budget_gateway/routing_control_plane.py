"""Application-facing logical routes with versioned, explainable model selection."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_SAFE_FALLBACK_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}
_TIERS = {"fast", "balanced", "smart", "reasoning"}


class RoutingControlPlane:
    """Persist applications and safely version logical LLM routes."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS gateway_applications(id TEXT PRIMARY KEY,name TEXT NOT NULL,default_route TEXT NOT NULL,api_key_hash TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS logical_routes(id TEXT PRIMARY KEY,name TEXT UNIQUE NOT NULL,draft_version INTEGER NOT NULL,published_version INTEGER,status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS route_versions(route_id TEXT NOT NULL,version INTEGER NOT NULL,config_json TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(route_id,version));
        CREATE TABLE IF NOT EXISTS route_activity(decision_id TEXT PRIMARY KEY,route_id TEXT NOT NULL,version INTEGER NOT NULL,selected_model TEXT NOT NULL,fallback_reason TEXT,decision_json TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS route_model_spend(route_name TEXT NOT NULL,model TEXT NOT NULL,period TEXT NOT NULL,spend REAL NOT NULL,PRIMARY KEY(route_name,model,period));
        CREATE TABLE IF NOT EXISTS route_model_health(route_name TEXT NOT NULL,model TEXT NOT NULL,healthy INTEGER NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(route_name,model));
        """)
        connection.commit()

    def create_application(self, name: str, default_route: str) -> dict[str, Any]:
        """Create an application and return its API key exactly once."""
        if not name.strip() or not default_route.strip():
            raise ValueError("application name and default route are required")
        app_id = f"app_{secrets.token_hex(6)}"
        api_key = f"gw_{secrets.token_urlsafe(24)}"
        self.connection.execute(
            "INSERT INTO gateway_applications VALUES(?,?,?,?,?)",
            (
                app_id,
                name.strip(),
                default_route.strip(),
                hashlib.sha256(api_key.encode()).hexdigest(),
                _utcnow(),
            ),
        )
        self.connection.commit()
        return {
            "id": app_id,
            "name": name.strip(),
            "default_route": default_route.strip(),
            "api_key": api_key,
            "status": "active",
        }

    def authenticate_application(self, api_key: str) -> dict[str, Any]:
        """Authenticate a gateway application key without storing plaintext."""
        digest = hashlib.sha256(api_key.encode()).hexdigest()
        row = self.connection.execute(
            "SELECT id,name,default_route FROM gateway_applications WHERE api_key_hash=?",
            (digest,),
        ).fetchone()
        if row is None:
            raise PermissionError("invalid application key")
        return {"id": row[0], "name": row[1], "default_route": row[2]}

    def has_published_route(self, name: str) -> bool:
        """Return whether a logical alias has an active published version."""
        return (
            self.connection.execute(
                "SELECT 1 FROM logical_routes WHERE name=? AND published_version IS NOT NULL",
                (name,),
            ).fetchone()
            is not None
        )

    def list_applications(self) -> list[dict[str, Any]]:
        """List applications without exposing API-key material."""
        rows = self.connection.execute(
            "SELECT id,name,default_route,created_at FROM gateway_applications ORDER BY created_at,id"
        ).fetchall()
        return [
            {
                "id": row[0],
                "name": row[1],
                "default_route": row[2],
                "created_at": row[3],
                "status": "active",
            }
            for row in rows
        ]

    def create_route(self, config: dict[str, Any]) -> dict[str, Any]:
        """Create version one as a validated draft logical route."""
        clean = _validate_config(config)
        route_id = f"route_{secrets.token_hex(6)}"
        now = _utcnow()
        try:
            with self.connection:
                self.connection.execute(
                    "INSERT INTO logical_routes VALUES(?,?,1,NULL,'draft')",
                    (route_id, clean["name"]),
                )
                self.connection.execute(
                    "INSERT INTO route_versions VALUES(?,?,?,?)",
                    (route_id, 1, _canonical(clean), now),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("route name already exists") from exc
        return self.get_route(route_id)

    def update_route(self, route_id: str, config: dict[str, Any]) -> dict[str, Any]:
        """Create a new immutable draft version without changing production."""
        current = self.get_route(route_id)
        clean = _validate_config(config)
        version = int(current["draft_version"]) + 1
        with self.connection:
            self.connection.execute(
                "UPDATE logical_routes SET name=?,draft_version=?,status='draft' WHERE id=?",
                (clean["name"], version, route_id),
            )
            self.connection.execute(
                "INSERT INTO route_versions VALUES(?,?,?,?)",
                (route_id, version, _canonical(clean), _utcnow()),
            )
        return self.get_route(route_id)

    def get_route(self, route_id: str) -> dict[str, Any]:
        """Return one route and both draft and published configurations."""
        row = self.connection.execute(
            "SELECT id,name,draft_version,published_version,status FROM logical_routes WHERE id=?",
            (route_id,),
        ).fetchone()
        if row is None:
            raise KeyError(route_id)
        draft = self._config(route_id, int(row[2]))
        published = self._config(route_id, int(row[3])) if row[3] is not None else None
        return {
            "id": row[0],
            "name": row[1],
            "draft_version": row[2],
            "published_version": row[3],
            "status": row[4],
            "draft": draft,
            "published": published,
        }

    def list_routes(self) -> list[dict[str, Any]]:
        """List logical routes with compact product-facing summaries."""
        rows = self.connection.execute(
            "SELECT id FROM logical_routes ORDER BY name"
        ).fetchall()
        return [self.get_route(row[0]) for row in rows]

    def publish_route(self, route_id: str) -> dict[str, Any]:
        """Atomically make the current draft the active production version."""
        self.get_route(route_id)
        with self.connection:
            self.connection.execute(
                "UPDATE logical_routes SET published_version=draft_version,status='active' WHERE id=?",
                (route_id,),
            )
        return self.get_route(route_id)

    def rollback_route(self, route_id: str) -> dict[str, Any]:
        """Roll back to the version immediately before the active version."""
        route = self.get_route(route_id)
        published = route["published_version"]
        if published is None or int(published) <= 1:
            raise ValueError("no previous published version exists")
        with self.connection:
            self.connection.execute(
                "UPDATE logical_routes SET published_version=?,status='active' WHERE id=?",
                (int(published) - 1, route_id),
            )
        return self.get_route(route_id)

    def simulate(
        self,
        route_id: str,
        *,
        now: datetime,
        quality_tier: str,
        estimated_cost: float,
        spend_by_model: dict[str, float],
        health: dict[str, bool],
        region: str,
        capabilities: list[str],
    ) -> dict[str, Any]:
        """Select a model and return a complete human-readable decision path."""
        route = self.get_route(route_id)
        version = route["published_version"] or route["draft_version"]
        config = self._config(route_id, int(version))
        if quality_tier not in _TIERS:
            raise ValueError("quality tier must be fast, balanced, smart, or reasoning")
        if estimated_cost < 0 or estimated_cost > float(config["max_cost_per_request"]):
            raise ValueError("estimated cost exceeds the route request limit")
        if region != config["required_region"]:
            raise ValueError("request region does not satisfy route residency")
        required = set(config["required_capabilities"])
        if not required.issubset(capabilities):
            raise ValueError("request capabilities do not satisfy route capabilities")
        local = now.astimezone(ZoneInfo(config["timezone"]))
        schedule = config["schedule"]
        in_schedule = (
            local.weekday() in schedule["weekdays"]
            and schedule["start"] <= local.strftime("%H:%M") < schedule["end"]
        )
        path = [
            {
                "gate": "application",
                "passed": True,
                "detail": f"Route {config['name']} is available",
            },
            {
                "gate": "schedule",
                "passed": True,
                "detail": f"{'Inside' if in_schedule else 'Outside'} premium schedule in {config['timezone']}",
            },
        ]
        tier_model = config["quality_models"].get(quality_tier)
        primary = tier_model or (
            schedule["scheduled_model"] if in_schedule else config["default_model"]
        )
        candidates = [primary, *config["fallback_models"]]
        selected = None
        fallback_reason = None
        for index, model in enumerate(dict.fromkeys(candidates)):
            exhausted = spend_by_model.get(model, 0.0) >= float(
                config["monthly_budget"]
            )
            healthy = health.get(model, True)
            if exhausted:
                path.append(
                    {
                        "gate": "budget",
                        "passed": False,
                        "detail": f"{model} budget exhausted",
                    }
                )
                fallback_reason = fallback_reason or "budget"
                continue
            if not healthy:
                path.append(
                    {
                        "gate": "health",
                        "passed": False,
                        "detail": f"{model} is unhealthy",
                    }
                )
                fallback_reason = fallback_reason or "health"
                continue
            selected = model
            path.append(
                {
                    "gate": "model",
                    "passed": True,
                    "detail": f"Selected {model} for {quality_tier} quality",
                }
            )
            if index and fallback_reason is None:
                fallback_reason = "fallback"
            break
        if selected is None:
            raise RuntimeError("no eligible healthy model with remaining budget")
        decision_id = f"dec_{secrets.token_hex(8)}"
        result = {
            "decision_id": decision_id,
            "route": config["name"],
            "route_version": version,
            "selected_model": selected,
            "fallback_reason": fallback_reason,
            "candidate_models": [
                candidate
                for candidate in dict.fromkeys(candidates)
                if spend_by_model.get(candidate, 0.0) < float(config["monthly_budget"])
                and health.get(candidate, True)
            ],
            "fallback_statuses": config["fallback_statuses"],
            "decision_path": path,
            "response_headers": {
                "X-Gateway-Route": config["name"],
                "X-Gateway-Route-Version": str(version),
                "X-Gateway-Serving-Model": selected,
                "X-Gateway-Decision-Id": decision_id,
                "X-Gateway-Fallback": fallback_reason or "none",
            },
        }
        with self.connection:
            self.connection.execute(
                "INSERT INTO route_activity VALUES(?,?,?,?,?,?,?)",
                (
                    decision_id,
                    route_id,
                    version,
                    selected,
                    fallback_reason,
                    _canonical(result),
                    _utcnow(),
                ),
            )
        return result

    def resolve_alias(
        self,
        name: str,
        *,
        now: datetime,
        quality_tier: str,
        estimated_cost: float,
        region: str,
        capabilities: list[str],
    ) -> dict[str, Any]:
        """Resolve a published logical alias using persisted spend and health."""
        row = self.connection.execute(
            "SELECT id,published_version FROM logical_routes WHERE name=? AND published_version IS NOT NULL",
            (name,),
        ).fetchone()
        if row is None:
            raise KeyError(name)
        config = self._config(str(row[0]), int(row[1]))
        models = set(config["fallback_models"]) | set(config["quality_models"].values())
        models |= {config["default_model"], config["schedule"]["scheduled_model"]}
        spend = {model: self.model_spend(name, model, at=now) for model in models}
        health = {model: self.model_health(name, model) for model in models}
        return self.simulate(
            str(row[0]),
            now=now,
            quality_tier=quality_tier,
            estimated_cost=estimated_cost,
            spend_by_model=spend,
            health=health,
            region=region,
            capabilities=capabilities,
        )

    def record_model_spend(
        self, route_name: str, model: str, amount: float, *, at: datetime
    ) -> None:
        """Attribute successful serving cost to a model's route-month ledger."""
        if amount < 0:
            raise ValueError("spend amount must be non-negative")
        period = at.strftime("%Y-%m")
        with self.connection:
            self.connection.execute(
                "INSERT INTO route_model_spend VALUES(?,?,?,?) ON CONFLICT(route_name,model,period) DO UPDATE SET spend=spend+excluded.spend",
                (route_name, model, period, amount),
            )

    def model_spend(self, route_name: str, model: str, *, at: datetime) -> float:
        """Return attributed spend for one route, model and calendar month."""
        row = self.connection.execute(
            "SELECT spend FROM route_model_spend WHERE route_name=? AND model=? AND period=?",
            (route_name, model, at.strftime("%Y-%m")),
        ).fetchone()
        return float(row[0]) if row else 0.0

    def route_usage(self, route_id: str, *, at: datetime) -> dict[str, Any]:
        """Return current-month spend and budget headroom by configured model."""
        route = self.get_route(route_id)
        config = route["published"] or route["draft"]
        models = set(config["fallback_models"]) | set(config["quality_models"].values())
        models |= {config["default_model"], config["schedule"]["scheduled_model"]}
        budget = float(config["monthly_budget"])
        rows = []
        for model in sorted(models):
            spent = self.model_spend(route["name"], model, at=at)
            rows.append(
                {
                    "model": model,
                    "spent": spent,
                    "budget": budget,
                    "remaining": max(0.0, budget - spent),
                    "percent_used": min(100.0, spent / budget * 100.0),
                }
            )
        rows.sort(key=lambda item: (-item["spent"], item["model"]))
        return {
            "route_id": route_id,
            "route": route["name"],
            "period": at.strftime("%Y-%m"),
            "total_spend": sum(item["spent"] for item in rows),
            "models": rows,
        }

    def set_model_health(self, route_name: str, model: str, *, healthy: bool) -> None:
        """Set the latest provider-health eligibility for a route model."""
        with self.connection:
            self.connection.execute(
                "INSERT INTO route_model_health VALUES(?,?,?,?) ON CONFLICT(route_name,model) DO UPDATE SET healthy=excluded.healthy,updated_at=excluded.updated_at",
                (route_name, model, int(healthy), _utcnow()),
            )

    def model_health(self, route_name: str, model: str) -> bool:
        """Return current health, defaulting new models to healthy."""
        row = self.connection.execute(
            "SELECT healthy FROM route_model_health WHERE route_name=? AND model=?",
            (route_name, model),
        ).fetchone()
        return bool(row[0]) if row else True

    def route_activity(self, route_id: str) -> list[dict[str, Any]]:
        """Return newest-first explainable route decisions."""
        rows = self.connection.execute(
            "SELECT decision_json,created_at FROM route_activity WHERE route_id=? ORDER BY created_at DESC,decision_id DESC",
            (route_id,),
        ).fetchall()
        return [{**json.loads(row[0]), "created_at": row[1]} for row in rows]

    def _config(self, route_id: str, version: int) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT config_json FROM route_versions WHERE route_id=? AND version=?",
            (route_id, version),
        ).fetchone()
        if row is None:
            raise KeyError((route_id, version))
        return json.loads(row[0])


def _validate_config(config: dict[str, Any]) -> dict[str, Any]:
    clean = dict(config)
    required = {
        "name",
        "default_model",
        "fallback_models",
        "monthly_budget",
        "timezone",
        "schedule",
        "quality_models",
        "fallback_statuses",
        "max_cost_per_request",
        "required_region",
        "required_capabilities",
    }
    if not required.issubset(clean):
        raise ValueError(
            f"missing route fields: {', '.join(sorted(required - set(clean)))}"
        )
    if not str(clean["name"]).strip() or not str(clean["default_model"]).strip():
        raise ValueError("route name and default model are required")
    try:
        ZoneInfo(str(clean["timezone"]))
    except ZoneInfoNotFoundError as exc:
        raise ValueError("unknown timezone") from exc
    statuses = {int(value) for value in clean["fallback_statuses"]}
    if not statuses or not statuses.issubset(_SAFE_FALLBACK_STATUSES):
        raise ValueError("fallback statuses must be retry-safe transient status codes")
    if float(clean["monthly_budget"]) <= 0 or float(clean["max_cost_per_request"]) <= 0:
        raise ValueError("budgets must be positive")
    schedule = clean["schedule"]
    if not all(
        key in schedule for key in ("weekdays", "start", "end", "scheduled_model")
    ):
        raise ValueError("schedule is incomplete")
    if not set(clean["quality_models"]).issubset(_TIERS):
        raise ValueError("unknown quality tier")
    clean["fallback_statuses"] = sorted(statuses)
    clean["fallback_models"] = list(dict.fromkeys(clean["fallback_models"]))
    clean["required_capabilities"] = sorted(set(clean["required_capabilities"]))
    return clean


def _canonical(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _utcnow() -> str:
    return datetime.now(tz=ZoneInfo("UTC")).isoformat()
