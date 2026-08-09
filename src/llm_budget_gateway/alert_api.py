"""Alert Rules REST API and Alert History endpoints.

Pre-development stub: the app factory and route registration exist so
interface tests pass (imports, ``create_alerts_app`` callable, route
paths). Every handler raises ``NotImplementedError`` — behavioral tests
stay RED until the developer implements the CRUD + history logic.

Contract (planning spec acceptance criteria):
- ``create_alerts_app(db_path) -> FastAPI`` — repo convention for
  standalone API apps (control_api, security_api, evaluation_api).
- ``POST   /api/alerts``         create rule → 201 ``{id, ...}``; 422 on
  missing channel-specific config (webhook needs ``url``, slack needs
  ``bot_token`` + ``channel``, telegram needs ``bot_token`` + ``chat_id``,
  email needs ``host``, ``username``, ``to_address``).
- ``GET    /api/alerts``         list rules → ``{"items": [...]}``.
- ``GET    /api/alerts/{id}``    single rule; 404 on missing.
- ``DELETE /api/alerts/{id}``    → 204; 404 on missing.
- ``GET    /api/alerts/history`` → ``{"items": [...], "total": N}``,
  paginated (page, page_size), filterable by alert_rule_id, channel,
  delivery_status; newest-first.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from .alert_models import AlertRule


def create_alerts_app(db_path: str | Path = "alerts.db") -> FastAPI:
    """Create the FastAPI app serving the alert rules CRUD + history API."""
    db = Path(db_path)
    app = FastAPI(title="LLM Budget Gateway Alert API", version="1.0")
    app.state.db_path = str(db)

    @app.post("/api/alerts", status_code=201)
    async def create_rule(rule: AlertRule) -> dict:
        """Persist a new alert rule and return it with its generated id."""
        raise NotImplementedError

    @app.get("/api/alerts")
    async def list_rules() -> dict:
        """Return all alert rules: {"items": [...]}."""
        raise NotImplementedError

    @app.get("/api/alerts/{rule_id}")
    async def get_rule(rule_id: str) -> dict:
        """Return one rule by id; 404 when missing."""
        raise NotImplementedError

    @app.delete("/api/alerts/{rule_id}", status_code=204)
    async def delete_rule(rule_id: str) -> None:
        """Delete a rule by id; 404 when missing."""
        raise NotImplementedError

    @app.get("/api/alerts/history")
    async def alert_history(
        page: int = 1,
        page_size: int = 20,
        alert_rule_id: str | None = None,
        channel: str | None = None,
        delivery_status: str | None = None,
    ) -> dict:
        """Paginated dispatch logs, newest-first; filter by rule/channel/status."""
        raise NotImplementedError

    return app
