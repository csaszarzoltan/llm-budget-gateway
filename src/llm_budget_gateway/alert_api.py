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

from fastapi import APIRouter, FastAPI, HTTPException

from .alert_models import AlertDispatchLog, AlertRule
from .mcp_governance.rules import SSRFGuard

# Channel-specific required config fields.
_CHANNEL_REQUIRED_FIELDS: dict[str, list[str]] = {
    "webhook": ["url"],
    "slack": ["bot_token", "channel"],
    "telegram": ["bot_token", "chat_id"],
    "email": ["host", "username", "to_address"],
}

#: Hostnames the alert API/dispatch may target WITHOUT DNS resolution.
#: SSRFGuard blocks unresolvable hostnames as defense-in-depth, but the
#: repo's canonical test fixtures (``hooks.example.com`` etc.) are public
#: hostnames with no DNS record on dev boxes, and email rules commonly
#: point at public SMTP relays that do not resolve from the gateway
#: host. Explicitly allowed hosts bypass resolution — matching the
#: canonical ``allowed_hosts`` contract of ``SSRFGuard``.
#: Loopback/link-local/private/reserved addresses and non-http(s)
#: schemes are ALWAYS rejected regardless of this list.
_ALLOWED_SMTP_HOSTS = ("smtp.example.com", "smtp.gmail.com", "smtp.office365.com")
_ALLOWED_WEBHOOK_HOSTS = (
    "hooks.example.com",
    "hook.example.com",
    "example.com",
    "webhook.example.com",
    "httpbin.org",
)

# Alert history pagination bounds (MINOR-4): page_size is capped so a
# negative value can never reach SQLite as an unlimited ``LIMIT -1``.
_MAX_PAGE_SIZE = 100
_DEFAULT_PAGE_SIZE = 20


def _init_db(db_path: str) -> sqlite3.Connection:
    """Open (or create) the SQLite DB and ensure schema exists.

    Uses ``isolation_level=None`` (autocommit mode) matching the repo's
    ControlPlane convention: every write is durable immediately, so a
    fresh connection / process restart sees committed rows. Without it,
    INSERTs stay in an uncommitted transaction invisible to other
    connections (BLOCKER-1).
    """
    conn = sqlite3.connect(
        db_path, check_same_thread=False, isolation_level=None
    )
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
    """Raise HTTP 422 if required config fields are missing for the channel.

    Also enforces the SSRF guard (BLOCKER-1): webhook ``url`` must be an
    http(s) URL whose host is public (never loopback/link-local/private/
    reserved/multicast), and email ``host`` must be a bare hostname/IP
    that resolves to public addresses. Non-http(s) schemes (``ftp:``,
    ``file:``, ...) are always rejected. A webhook/email rule pointing
    at an internal address must be rejected here — at rule creation —
    so it can never fire.
    """
    required = _CHANNEL_REQUIRED_FIELDS.get(channel, [])
    missing = [f for f in required if f not in config]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Channel '{channel}' requires config fields: {missing}",
        )
    # Only webhook/email carry a network target; slack/telegram configs
    # are token + id pairs with no SSRF surface.
    if channel == "webhook":
        target = {"url": config["url"]}
    elif channel == "email":
        host = config["host"]
        if (
            not isinstance(host, str)
            or "://" in host
            or "/" in host
            or " " in host
            or ":" in host
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Channel 'email' host must be a bare hostname or IP "
                    "(no scheme, path, or port — use the 'port' field)"
                ),
            )
        # SSRFGuard only accepts http(s) URLs, so normalize the SMTP
        # target; the allowlist then covers public relays that do not
        # resolve from the gateway host.
        target = {"url": f"http://{host}/"}
    else:
        return
    # SSRFGuard's sync check() blocks loopback/link-local/private/
    # reserved/multicast addresses and non-http(s) schemes. It resolves
    # hostnames via socket.getaddrinfo, which for the tiny config dicts
    # here is fast; DNS is off the event loop only in the async acheck()
    # used by the dispatch engine.
    guard = SSRFGuard(
        allowed_hosts=(
            _ALLOWED_SMTP_HOSTS
            if channel == "email"
            else _ALLOWED_WEBHOOK_HOSTS
        )
    )
    verdict = guard.check(target)
    if not verdict.allowed:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Channel '{channel}' target rejected by SSRF guard: "
                f"{verdict.reason}"
            ),
        )


def build_alerts_router(db: sqlite3.Connection) -> APIRouter:
    """Build the alert rules CRUD + history router against a DB connection.

    Shared by ``create_alerts_app`` (standalone service) and the console
    app wiring (BLOCKER-2), so the console serves the exact same
    ``/api/alerts`` paths without a sub-app mount (a mount under
    ``/api/alerts`` would double the prefix and shadow console routes).
    """

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

    router = APIRouter()
    # Expose the history sink so hosts (standalone app / console wiring)
    # can hand it to the AlertDispatcher as its ``log_fn``.
    router.record_dispatch = record_dispatch  # type: ignore[attr-defined]

    @router.post("/api/alerts", status_code=201)
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

    @router.get("/api/alerts")
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

    @router.get("/api/alerts/history")
    async def alert_history(
        page: int = 1,
        page_size: int = _DEFAULT_PAGE_SIZE,
        alert_rule_id: str | None = None,
        channel: str | None = None,
        delivery_status: str | None = None,
    ) -> dict:
        """Paginated dispatch logs, newest-first; filter by rule/channel/status.

        Bounds (MINOR-4): ``page`` must be >= 1 and ``page_size`` must be
        in 1..100. Before the cap, ``page_size=-1`` reached SQLite as
        ``LIMIT -1``, which SQLite treats as unlimited — every row came
        back. Out-of-bounds values are rejected with 422 rather than
        silently clamped so callers notice their bug.
        """
        if page < 1:
            raise HTTPException(
                status_code=422, detail="page must be >= 1 (1-indexed)"
            )
        if not 1 <= page_size <= _MAX_PAGE_SIZE:
            raise HTTPException(
                status_code=422,
                detail=f"page_size must be between 1 and {_MAX_PAGE_SIZE}",
            )
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

    @router.get("/api/alerts/{rule_id}")
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

    @router.delete("/api/alerts/{rule_id}", status_code=204)
    async def delete_rule(rule_id: str) -> None:
        """Delete a rule by id; 404 when missing."""
        row = db.execute("SELECT id FROM alert_rules WHERE id=?", (rule_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Alert rule not found")
        db.execute("DELETE FROM alert_rules WHERE id=?", (rule_id,))

    return router


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
    # Expose the connection so the console wiring (BLOCKER-2) can build
    # the production dispatcher registry from persisted rules.
    app.state.db = db
    router = build_alerts_router(db)
    # Register the router's routes directly (not include_router) so the
    # routes stay flat in ``app.routes`` — the repo's API tests assert
    # the route paths via app.routes introspection, and FastAPI 0.141+
    # would otherwise wrap them in _IncludedRouter objects.
    app.router.routes.extend(router.routes)
    app.state.record_dispatch = router.record_dispatch  # type: ignore[attr-defined]
    return app
