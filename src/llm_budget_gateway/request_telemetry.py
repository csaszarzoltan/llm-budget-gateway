"""LLM request telemetry — observability for LLM usage patterns.

Captures provider/model/tokens/cost/latency/trace_id for every routed
request and persists to a SQLite telemetry table, exposed via the
``/v1/observability/requests`` endpoint.

This module is the observability surface for roadmap #1 ("llm observability").
It is a lightweight middleware: thin enough to never block the proxy path,
and backed by the same SQLite database as the cost ledger so operational
deployments do not need an extra store.

MVP signals (confirmed by operator decision):
  - trace_id      (the gateway request_id, stable per-request)
  - provider      (litellm | direct | unknown)
  - model          (the serving model name)
  - tokens         (prompt / completion / total / reasoning)
  - cost           (input / output / reasoning / total USD)
  - latency_ms     (provider round-trip, wall-clock)
  - status         (success | error | timeout)
  - status_code    (HTTP status from upstream or gateway)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .cost_tracking import TokenUsage

logger = logging.getLogger(__name__)

_TELEMETRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry_requests (
    request_id  TEXT PRIMARY KEY,
    trace_id    TEXT NOT NULL,
    provider    TEXT NOT NULL,
    model       TEXT NOT NULL,
    api_key     TEXT,
    user_id     TEXT,
    team        TEXT,
    customer_id TEXT,
    route       TEXT,
    prompt_tokens  INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    input_cost   REAL NOT NULL DEFAULT 0.0,
    output_cost   REAL NOT NULL DEFAULT 0.0,
    reasoning_cost REAL NOT NULL DEFAULT 0.0,
    total_cost    REAL NOT NULL DEFAULT 0.0,
    latency_ms    INTEGER NOT NULL,
    status        TEXT NOT NULL,
    status_code   INTEGER,
    conversation_id TEXT,
    metadata_json  TEXT,
    recorded_at    INTEGER NOT NULL
)
"""


@dataclass
class TelemetryEntry:
    """A single LLM request observability record.

    Derived from a ``ProviderResponse`` plus gateway context (trace_id,
    provider, model).  ``trace_id`` is the gateway's internal request_id.
    """

    trace_id: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    input_cost: float = 0.0
    output_cost: float = 0.0
    reasoning_cost: float = 0.0
    total_cost: float = 0.0
    latency_ms: int = 0
    status: str = "success"
    status_code: int | None = None
    api_key: str | None = None
    user_id: str | None = None
    team: str | None = None
    customer_id: str | None = None
    route: str | None = None
    conversation_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    recorded_at: int | None = None

    def to_record(self, db_path: str | None = None) -> dict[str, Any]:
        """Serialise to a plain dict suitable for JSON / DB insertion."""
        return {
            "trace_id": self.trace_id,
            "provider": self.provider,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "input_cost": round(self.input_cost, 8),
            "output_cost": round(self.output_cost, 8),
            "reasoning_cost": round(self.reasoning_cost, 8),
            "total_cost": round(self.total_cost, 8),
            "latency_ms": self.latency_ms,
            "status": self.status,
            "status_code": self.status_code,
            "api_key": self.api_key,
            "user_id": self.user_id,
            "team": self.team,
            "customer_id": self.customer_id,
            "route": self.route,
            "conversation_id": self.conversation_id,
            "metadata": self.metadata,
            "recorded_at": self.recorded_at or int(time.time()),
        }


class RequestTelemetryStore:
    """SQLite-backed store for LLM request telemetry.

    Shares the connection with the cost ledger in production (via
    ``create_app``), but can open its own ``gateway_telemetry.db`` when
    used standalone or in tests.
    """

    def __init__(self, db_path: str, connection: sqlite3.Connection | None = None) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = connection or sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute(_TELEMETRY_SCHEMA)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_telemetry_trace_id "
                "ON telemetry_requests(trace_id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_telemetry_recorded_at "
                "ON telemetry_requests(recorded_at)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_telemetry_model "
                "ON telemetry_requests(model, recorded_at)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_telemetry_status "
                "ON telemetry_requests(status, recorded_at)"
            )
            self._conn.commit()

    def record(self, entry: TelemetryEntry) -> str:
        """Persist a telemetry entry.  Returns the trace_id.

        Best-effort by design: a DB failure is logged, never raised into
        the proxy request path.
        """
        if entry.recorded_at is None:
            entry.recorded_at = int(time.time())
        d = entry.to_record()
        try:
            with self._lock:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO telemetry_requests (
                        request_id, trace_id, provider, model, api_key,
                        user_id, team, customer_id, route,
                        prompt_tokens, completion_tokens, total_tokens,
                        reasoning_tokens,
                        input_cost, output_cost, reasoning_cost, total_cost,
                        latency_ms, status, status_code,
                        conversation_id, metadata_json, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        d["trace_id"],        # request_id == trace_id (upsert key)
                        d["trace_id"],
                        d["provider"],
                        d["model"],
                        d["api_key"],
                        d["user_id"],
                        d["team"],
                        d["customer_id"],
                        d["route"],
                        d["prompt_tokens"],
                        d["completion_tokens"],
                        d["total_tokens"],
                        d["reasoning_tokens"],
                        d["input_cost"],
                        d["output_cost"],
                        d["reasoning_cost"],
                        d["total_cost"],
                        d["latency_ms"],
                        d["status"],
                        d["status_code"],
                        d["conversation_id"],
                        json.dumps(d["metadata"]) if d["metadata"] else None,
                        d["recorded_at"],
                    ),
                )
                self._conn.commit()
        except Exception:
            logger.exception(
                "telemetry record failed trace_id=%s", entry.trace_id
            )
        return entry.trace_id

    def query(
        self,
        *,
        model: str | None = None,
        status: str | None = None,
        provider: str | None = None,
        since_epoch: int | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return telemetry entries matching the filter, newest first.

        ``since_epoch`` is an epoch-second lower bound on ``recorded_at``.
        """
        sql = "SELECT * FROM telemetry_requests WHERE 1=1"
        params: list[Any] = []
        if model is not None:
            sql += " AND model = ?"
            params.append(model)
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        if provider is not None:
            sql += " AND provider = ?"
            params.append(provider)
        if since_epoch is not None:
            sql += " AND recorded_at >= ?"
            params.append(since_epoch)
        sql += f" ORDER BY recorded_at DESC LIMIT {int(limit)} OFFSET {int(offset)}"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_dict(r) for r in rows]

    def lookup(self, trace_id: str) -> dict[str, Any] | None:
        """Return a single telemetry entry by trace_id, or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM telemetry_requests WHERE trace_id = ?",
                (trace_id,),
            ).fetchone()
        return _row_to_dict(row) if row else None

    def summary(
        self, *, since_epoch: int | None = None, model: str | None = None
    ) -> dict[str, Any]:
        """Aggregate counts and costs over the window (default: all-time)."""
        sql = (
            "SELECT COUNT(*), COALESCE(SUM(prompt_tokens), 0), "
            "COALESCE(SUM(completion_tokens), 0), COALESCE(SUM(total_tokens), 0), "
            "COALESCE(SUM(total_cost), 0.0) FROM telemetry_requests WHERE 1=1"
        )
        params: list[Any] = []
        if since_epoch is not None:
            sql += " AND recorded_at >= ?"
            params.append(since_epoch)
        if model is not None:
            sql += " AND model = ?"
            params.append(model)
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return {
            "requests": int(row[0]),
            "prompt_tokens": int(row[1]),
            "completion_tokens": int(row[2]),
            "total_tokens": int(row[3]),
            "total_cost": round(float(row[4]), 8),
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a sqlite Row into a dict, parsing metadata_json."""
    d = dict(row)
    raw = d.get("metadata_json")
    d["metadata"] = json.loads(raw) if raw else {}
    d.pop("metadata_json", None)
    return d


class RequestTelemetryLogger:
    """Thin logger that converts ProviderResponse → TelemetryEntry.

    The logger itself is a no-op without a store attached; this keeps the
    proxy path safe even when observability is not configured.  The gateway
    attaches a ``RequestTelemetryStore`` when the SQLite DB is available.

    Usage:
        logger = RequestTelemetryLogger(store)
        entry = logger.from_response(
            trace_id=request_id, provider="litellm",
            response=provider_response, scope=scope, customer_id=cid,
            route=route_name,
        )
    """

    def __init__(self, store: RequestTelemetryStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> RequestTelemetryStore | None:
        return self._store

    def attach_store(self, store: RequestTelemetryStore) -> None:
        """Attach a telemetry store at runtime (called by create_app)."""
        self._store = store

    def _cost_from_usage(
        self, usage: TokenUsage | None
    ) -> tuple[int, int, int, int]:
        """Extract (prompt, completion, total, reasoning) token counts."""
        if usage is None:
            return 0, 0, 0, 0
        return (
            int(getattr(usage, "prompt_tokens", 0) or 0),
            int(getattr(usage, "completion_tokens", 0) or 0),
            int(getattr(usage, "total_tokens", 0) or 0),
            int(getattr(usage, "reasoning_tokens", 0) or 0),
        )

    def from_response(
        self,
        *,
        trace_id: str,
        provider: str,
        response: Any,
        scope: Any = None,
        customer_id: str | None = None,
        route: str | None = None,
        conversation_id: str | None = None,
        cost_calc: tuple[float, float, float, float] | None = None,
    ) -> TelemetryEntry:
        """Build a TelemetryEntry from a ProviderResponse-like object.

        ``cost_calc`` is ``(input_cost, output_cost, reasoning_cost, total_cost)``
        — when provided, it overrides zero-cost estimation so the telemetry
        reflects the real charge computed by CostCalculator.
        """
        model = getattr(response, "model", "") or ""
        latency_ms = int(getattr(response, "latency_ms", 0) or 0)
        status_code = int(getattr(response, "status_code", 0) or 0)
        usage = getattr(response, "usage", None)

        prompt_tokens, completion_tokens, total_tokens, reasoning_tokens = (
            self._cost_from_usage(usage)
        )

        if cost_calc is not None:
            input_cost, output_cost, reasoning_cost, total_cost = cost_calc
        else:
            input_cost = output_cost = reasoning_cost = total_cost = 0.0

        # Infer status text from the response status code and body.
        body_str = str(getattr(response, "body", "")).lower()
        if status_code >= 200 and status_code < 300:
            status = "success"
        elif status_code == 429:
            status = "rate_limited"
        elif status_code == 408 or "timed out" in body_str:
            status = "timeout"
        elif status_code >= 400:
            status = "error"
        else:
            status = "success"

        api_key = None
        user_id = None
        team = None
        if scope is not None:
            api_key = getattr(scope, "key", None) if getattr(scope, "kind", "") == "key" else None
            user_id = getattr(scope, "key", None) if getattr(scope, "kind", "") == "user" else None
            team = getattr(scope, "key", None) if getattr(scope, "kind", "") == "team" else None

        return TelemetryEntry(
            trace_id=trace_id,
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            reasoning_tokens=reasoning_tokens,
            input_cost=float(input_cost),
            output_cost=float(output_cost),
            reasoning_cost=float(reasoning_cost),
            total_cost=float(total_cost),
            latency_ms=latency_ms,
            status=status,
            status_code=status_code or None,
            api_key=api_key,
            user_id=user_id,
            team=team,
            customer_id=customer_id,
            route=route,
            conversation_id=conversation_id,
        )

    def emit(self, entry: TelemetryEntry) -> None:
        """Persist the entry — silently no-op if no store is attached."""
        if self._store is None:
            # Log to stderr as a fallback so telemetry isn't silently lost
            # when a store is not yet attached (e.g. tests).
            logger.info(
                "telemetry trace_id=%s provider=%s model=%s "
                "tokens=%d latency=%dms status=%s cost=$%.6f",
                entry.trace_id,
                entry.provider,
                entry.model,
                entry.total_tokens,
                entry.latency_ms,
                entry.status,
                entry.total_cost,
            )
            return
        try:
            self._store.record(entry)
        except Exception:
            # Best-effort by design: a store/record failure is logged,
            # never raised into the proxy request path.
            logger.exception(
                "telemetry emit failed trace_id=%s provider=%s model=%s",
                entry.trace_id,
                entry.provider,
                entry.model,
            )
