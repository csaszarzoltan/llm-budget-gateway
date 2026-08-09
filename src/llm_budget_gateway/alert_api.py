"""Alert Rules REST API and Alert History endpoints.

Provides the ``create_alerts_app`` factory that returns a FastAPI app
serving:

- ``POST   /api/alerts``         create rule → 201 with id
- ``GET    /api/alerts``         list rules → ``{"items": [...]}``
- ``GET    /api/alerts/{id}``    single rule; 404 on missing
- ``DELETE /api/alerts/{id}``    → 204; 404 on missing
- ``GET    /api/alerts/history`` paginated dispatch logs with filters

Channel config validation on POST:
- webhook needs ``url``
- slack needs ``bot_token`` + ``channel``
- telegram needs ``bot_token`` + ``chat_id``
- email needs ``host``, ``username``, ``to_address``

Storage: SQLite (one DB file per app instance), matching the repo convention.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException

from .alert_models import AlertDispatchLog, AlertRule

# Channel-specific required config fields.
_CHANNEL_REQUIRED_FIELDS: dict[str, list[str]] = {
    "webhook": ["url"],
    "slack": ["bot_token", "channel"],
    "telegram": ["bot_token", "chat_id"],
    "email": ["host", "username", "to_address"],
}


def _init_db(db_path: str) -> sqlite3.Connection:
    """Open (or create) the SQLite DB and ensure schema exists."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS alert_rules (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            threshold REAL NOT NULL,
            channel TEXT NOT NULL,
            config TEXT NOT NULL DEFAULT '{}',
            cooldown_seconds INTEGER NOT NULL DEFAULT 300,
            enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS alert_dispatch_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_rule_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            delivery_status TEXT NOT NULL,
            response_code INTEGER,
            error_message TEXT,
            dispatched_at REAL NOT NULL
        );
    """)
    return conn


def _validate_channel_config(channel: str, config: dict) -> None:
    """Raise HTTP 422 if required config fields are missing for the channel."""
    required = _CHANNEL_REQUIRED_FIELDS.get(channel, [])
    missing = [f for f in required if f not in config]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Channel '{channel}' requires config fields: {missing}",
        )


def create_alerts_app(db_path: str | Path = "alerts.db") -> FastAPI:
    """Create the FastAPI app serving the alert rules CRUD + history API.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        Configured FastAPI application instance.
    """
    db = _init_db(str(db_path))
    app = FastAPI(title="LLM Budget Gateway Alert API", version="1.0")
    app.state.db_path = str(db)

    def record_dispatch(log_entry: AlertDispatchLog) -> None:
        """Persist a dispatch attempt to the history store (spec item 7).

        Called by the dispatch engine's ``log_fn`` hook on every attempt.
        Never raises — history logging must not break alert dispatch.
        """
        try:
            db.execute(
                "INSERT INTO alert_dispatch_log"
                "(alert_rule_id, channel, delivery_status, response_code, error_message, dispatched_at) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                (
                    log_entry.alert_rule_id,
                    log_entry.channel,
                    log_entry.delivery_status,
                    log_entry.response_code,
                    log_entry.error_message,
                    log_entry.dispatched_at,
                ),
            )
        except sqlite3.Error:  # pragma: no cover - defensive; DB is local
            pass

    app.state.record_dispatch = record_dispatch

    @app.post("/api/alerts", status_code=201)
    async def create_rule(rule: AlertRule) -> dict:
        """Persist a new alert rule and return it with its generated id.

        Validates channel-specific config requirements before saving.
        """
        _validate_channel_config(rule.channel.value if hasattr(rule.channel, 'value') else str(rule.channel), rule.config)
        rule_id = secrets.token_hex(8)
        channel_val = rule.channel.value if hasattr(rule.channel, 'value') else str(rule.channel)
        db.execute(
            "INSERT INTO alert_rules(id, name, threshold, channel, config, cooldown_seconds, enabled) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            (
                rule_id,
                rule.name,
                rule.threshold,
                channel_val,
                json.dumps(rule.config),
                rule.cooldown_seconds,
                1 if rule.enabled else 0,
            ),
        )
        return {
            "id": rule_id,
            "name": rule.name,
            "threshold": rule.threshold,
            "channel": channel_val,
            "config": rule.config,
            "cooldown_seconds": rule.cooldown_seconds,
            "enabled": rule.enabled,
        }

    @app.get("/api/alerts")
    async def list_rules() -> dict:
        """Return all alert rules: ``{"items": [...]}``."""
        rows = db.execute("SELECT * FROM alert_rules ORDER BY rowid").fetchall()
        items = []
        for r in rows:
            items.append({
                "id": r["id"],
                "name": r["name"],
                "threshold": r["threshold"],
                "channel": r["channel"],
                "config": json.loads(r["config"]),
                "cooldown_seconds": r["cooldown_seconds"],
                "enabled": bool(r["enabled"]),
            })
        return {"items": items}

    @app.get("/api/alerts/history")
    async def alert_history(
        page: int = 1,
        page_size: int = 20,
        alert_rule_id: str | None = None,
        channel: str | None = None,
        delivery_status: str | None = None,
    ) -> dict:
        """Paginated dispatch logs, newest-first; filter by rule/channel/status."""
        where_clauses: list[str] = []
        params: list = []
        if alert_rule_id is not None:
            where_clauses.append("alert_rule_id = ?")
            params.append(alert_rule_id)
        if channel is not None:
            where_clauses.append("channel = ?")
            params.append(channel)
        if delivery_status is not None:
            where_clauses.append("delivery_status = ?")
            params.append(delivery_status)

        where = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        total = db.execute(f"SELECT count(*) as cnt FROM alert_dispatch_log{where}", params).fetchone()["cnt"]
        offset = (page - 1) * page_size
        rows = db.execute(
            f"SELECT * FROM alert_dispatch_log{where} ORDER BY dispatched_at DESC LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()
        items = [dict(r) for r in rows]
        return {"items": items, "total": total}

    @app.get("/api/alerts/{rule_id}")
    async def get_rule(rule_id: str) -> dict:
        """Return one rule by id; 404 when missing."""
        row = db.execute("SELECT * FROM alert_rules WHERE id=?", (rule_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Alert rule not found")
        return {
            "id": row["id"],
            "name": row["name"],
            "threshold": row["threshold"],
            "channel": row["channel"],
            "config": json.loads(row["config"]),
            "cooldown_seconds": row["cooldown_seconds"],
            "enabled": bool(row["enabled"]),
        }

    @app.delete("/api/alerts/{rule_id}", status_code=204)
    async def delete_rule(rule_id: str) -> None:
        """Delete a rule by id; 404 when missing."""
        row = db.execute("SELECT id FROM alert_rules WHERE id=?", (rule_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Alert rule not found")
        db.execute("DELETE FROM alert_rules WHERE id=?", (rule_id,))

    return app
