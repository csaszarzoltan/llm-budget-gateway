"""Cost tracking: per-request token x price math and SQLite (WAL) ledger.

Placeholder stub for the TDD RED phase — behavioral methods raise
NotImplementedError until implemented (P0-2). Interface is normative per
analysis brief §4 P0-2.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from dataclasses import dataclass

from .budget_enforcement import BudgetScope

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS cost_records (
    request_id TEXT PRIMARY KEY,
    api_key TEXT NOT NULL,
    user_id TEXT,
    team TEXT,
    model TEXT NOT NULL,
    provider TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    input_cost REAL NOT NULL,
    output_cost REAL NOT NULL,
    reasoning_cost REAL NOT NULL DEFAULT 0.0,
    total_cost REAL NOT NULL,
    latency_ms INTEGER NOT NULL,
    status TEXT NOT NULL,
    status_code INTEGER,
    timestamp INTEGER NOT NULL,
    tool_name TEXT,
    project TEXT,
    route TEXT,
    client_id TEXT,
    client_profile TEXT,
    cache_hit INTEGER NOT NULL DEFAULT 0
)
"""


_CREATE_COOLDOWN_TABLE = """
CREATE TABLE IF NOT EXISTS model_cooldowns (
    route TEXT NOT NULL,
    model TEXT NOT NULL,
    until_ts INTEGER NOT NULL,
    reason TEXT,
    PRIMARY KEY (route, model)
)
"""


@dataclass
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    reasoning_tokens: int = 0


@dataclass
class ModelPrice:
    input_cost_per_million: float
    output_cost_per_million: float
    reasoning_cost_per_million: float = 0.0


@dataclass
class UsageRecord:
    request_id: str
    api_key: str
    user_id: str | None
    team: str | None
    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    input_cost: float
    output_cost: float
    total_cost: float
    latency_ms: int
    status: str  # "success" | "error" | "fallback"
    timestamp: int  # epoch seconds
    tool_name: str | None = None  # e.g. "server_id:tool_name" for MCP tool calls
    project: str | None = None  # project scope key when attribution is project-scoped
    route: str | None = None  # logical route name that served this request
    status_code: int | None = None  # HTTP status when status != "success"
    reasoning_tokens: int = 0
    reasoning_cost: float = 0.0
    client_id: str | None = None
    client_profile: str | None = None
    cache_hit: bool = False


def accumulate_usage(chunks: list[dict]) -> TokenUsage:
    """Aggregate partial usage chunk dicts into one TokenUsage record."""
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    reasoning_tokens = 0
    for chunk in chunks:
        prompt_tokens += int(chunk.get("prompt_tokens", 0) or 0)
        completion_tokens += int(chunk.get("completion_tokens", 0) or 0)
        total_tokens += int(chunk.get("total_tokens", 0) or 0)
        reasoning_tokens += int(chunk.get("reasoning_tokens", 0) or 0)
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        reasoning_tokens=reasoning_tokens,
    )


class PriceMap:
    """LiteLLM model_cost baseline + Settings.pricing_overrides. Overrides win."""

    def __init__(self, overrides: dict[str, ModelPrice] | None = None) -> None:
        self._overrides: dict[str, ModelPrice] = dict(overrides or {})

    def get_price(self, model: str) -> ModelPrice:
        """Return the effective price for ``model`` (override first, then
        litellm.model_cost; unknown models price at zero without raising).
        """
        if model in self._overrides:
            return self._overrides[model]
        try:
            import litellm

            info = litellm.model_cost.get(model)
        except Exception:
            info = None
        if not info:
            return ModelPrice(0.0, 0.0)
        input_per_token = info.get("input_cost_per_token")
        output_per_token = info.get("output_cost_per_token")
        if input_per_token is None or output_per_token is None:
            return ModelPrice(0.0, 0.0)
        return ModelPrice(
            input_cost_per_million=float(input_per_token) * 1e6,
            output_cost_per_million=float(output_per_token) * 1e6,
        )

    def add_override(self, model: str, price: ModelPrice) -> None:
        """Register a manual price that beats the litellm baseline."""
        self._overrides[model] = price


class CostCalculator:
    """Pure function: tokens x price / 1e6."""

    def __init__(self, price_map: PriceMap) -> None:
        self._price_map = price_map

    def calculate(
        self, model: str, prompt_tokens: int, completion_tokens: int,
        reasoning_tokens: int = 0,
    ) -> tuple[float, float, float, float]:
        """Return ``(input_cost, output_cost, reasoning_cost, total_cost)`` for the usage."""
        price = self._price_map.get_price(model)
        input_cost = prompt_tokens * price.input_cost_per_million / 1e6
        output_cost = completion_tokens * price.output_cost_per_million / 1e6
        reasoning_cost = reasoning_tokens * price.reasoning_cost_per_million / 1e6
        return input_cost, output_cost, reasoning_cost, input_cost + output_cost + reasoning_cost


class CostStore:
    """SQLite ledger, WAL mode. Table cost_records with indexes on
    (timestamp), (api_key, timestamp).

    The connection is created with ``check_same_thread=False`` and every
    operation is serialized under ``self._lock`` so the store can safely be
    driven from worker threads (see ``CostTracker`` which offloads sync I/O
    off the event loop).
    """

    def __init__(
        self, db_path: str | None = None, connection: sqlite3.Connection | None = None
    ) -> None:
        self._db_path = db_path or ""
        self._lock = threading.Lock()
        self._conn = connection or sqlite3.connect(
            db_path or ":memory:", check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(_CREATE_TABLE)
            self._conn.execute(_CREATE_COOLDOWN_TABLE)
            self._migrate_legacy_schema()
            # Additive migrations for pre-existing databases.
            try:
                self._conn.execute(
                    "ALTER TABLE cost_records ADD COLUMN status_code INTEGER"
                )
            except sqlite3.OperationalError:
                pass  # column already exists
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cost_records_timestamp "
                "ON cost_records (timestamp)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cost_records_api_key_timestamp "
                "ON cost_records (api_key, timestamp)"
            )
            self._conn.commit()

    def _migrate_legacy_schema(self) -> None:
        """Add columns introduced after the original table was created.

        ``CREATE TABLE IF NOT EXISTS`` never alters an existing table, so a
        DB created by an older build is missing ``tool_name``/``project``.
        Adding the column is idempotent and safe: the insert statement is
        column-explicit, so old rows keep NULL defaults.
        """
        existing = {
            row[1] for row in self._conn.execute("PRAGMA table_info(cost_records)")
        }
        for column, definition in (
            ("tool_name", "TEXT"),
            ("project", "TEXT"),
            ("route", "TEXT"),
            ("reasoning_tokens", "INTEGER NOT NULL DEFAULT 0"),
            ("reasoning_cost", "REAL NOT NULL DEFAULT 0.0"),
            ("client_id", "TEXT"),
            ("client_profile", "TEXT"),
            ("cache_hit", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if column not in existing:
                self._conn.execute(
                    f"ALTER TABLE cost_records ADD COLUMN {column} {definition}"
                )

    def insert(self, record: UsageRecord) -> None:
        """Persist one usage record (upsert on request_id)."""
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO cost_records (
                    request_id, api_key, user_id, team, model, provider,
                    prompt_tokens, completion_tokens, total_tokens,
                    reasoning_tokens,
                    input_cost, output_cost, reasoning_cost, total_cost,
                    latency_ms, status, status_code, timestamp,
                    tool_name, project, route,
                    client_id, client_profile, cache_hit
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.request_id,
                    record.api_key,
                    record.user_id,
                    record.team,
                    record.model,
                    record.provider,
                    record.prompt_tokens,
                    record.completion_tokens,
                    record.total_tokens,
                    record.reasoning_tokens,
                    record.input_cost,
                    record.output_cost,
                    record.reasoning_cost,
                    record.total_cost,
                    record.latency_ms,
                    record.status,
                    record.status_code,
                    record.timestamp,
                    record.tool_name,
                    record.project,
                    record.route,
                    record.client_id,
                    record.client_profile,
                    1 if record.cache_hit else 0,
                ),
            )
            self._conn.commit()

    def spend_since(
        self, scope_key: str, since_epoch: int, tool_name: str | None = None
    ) -> float:
        """Sum total_cost for records matching ``scope_key`` with
        timestamp >= since_epoch. When ``tool_name`` is given, only records
        attributed to that exact tool (e.g. ``"server_id:tool_name"``) count.
        """
        kind, _, key = scope_key.partition(":")
        with self._lock:
            if kind == "global":
                sql = (
                    "SELECT COALESCE(SUM(total_cost), 0) FROM cost_records "
                    "WHERE timestamp >= ?"
                )
                params: tuple[object, ...] = (since_epoch,)
                if tool_name is not None:
                    sql += " AND tool_name = ?"
                    params += (tool_name,)
                row = self._conn.execute(sql, params).fetchone()
                return float(row[0])
            column = {
                "key": "api_key",
                "user": "user_id",
                "team": "team",
                "project": "project",
            }.get(kind)
            if column is None:
                raise ValueError(f"unknown scope kind: {kind!r}")
            sql = (
                f"SELECT COALESCE(SUM(total_cost), 0) FROM cost_records "
                f"WHERE {column} = ? AND timestamp >= ?"
            )
            params = (key, since_epoch)
            if tool_name is not None:
                sql += " AND tool_name = ?"
                params += (tool_name,)
            row = self._conn.execute(sql, params).fetchone()
            return float(row[0])

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            self._conn.close()

    def daily_usage(
        self,
        days: int = 14,
        route: str | None = None,
    ) -> dict[str, object]:
        """Aggregate token usage per day per serving model, plus the raw
        request list for the same window (OpenRouter-style usage page).

        Returns:
            {
              "days": [{"date": "YYYY-MM-DD", "models": [
                  {"model": str, "prompt_tokens": int, "completion_tokens": int,
                   "total_tokens": int, "requests": int, "cost_usd": float}, ...
              ]}],
              "calls": [{"request_id", "model", "route", "prompt_tokens",
                         "completion_tokens", "total_tokens", "total_cost",
                         "status", "timestamp", "latency_ms"}],
            }
        """
        since = int(time.time()) - days * 86400
        with self._lock:
            day_rows = self._conn.execute(
                """
                SELECT date(timestamp, 'unixepoch') AS day, model,
                       SUM(prompt_tokens), SUM(completion_tokens),
                       SUM(total_tokens), COUNT(*), SUM(total_cost)
                FROM cost_records
                WHERE timestamp >= ?
                GROUP BY day, model
                ORDER BY day ASC
                """,
                (since,),
            ).fetchall()
            call_rows = self._conn.execute(
                """
                SELECT request_id, model, route, prompt_tokens, completion_tokens,
                       total_tokens, total_cost, status, timestamp, latency_ms,
                       status_code, reasoning_tokens, client_id, client_profile, cache_hit
                FROM cost_records
                WHERE timestamp >= ?
                ORDER BY timestamp DESC
                LIMIT 200
                """,
                (since,),
            ).fetchall()
        by_day: dict[str, list[dict[str, object]]] = {}
        for day, model, pt, ct, tt, reqs, cost in day_rows:
            by_day.setdefault(day, []).append(
                {
                    "model": model,
                    "prompt_tokens": int(pt or 0),
                    "completion_tokens": int(ct or 0),
                    "total_tokens": int(tt or 0),
                    "requests": int(reqs or 0),
                    "cost_usd": round(float(cost or 0.0), 6),
                }
            )
        days_out = [
            {"date": day, "models": by_day[day]} for day in sorted(by_day)
        ]
        if route:
            calls = [c for c in call_rows if c[2] == route]
        else:
            calls = list(call_rows)
        return {
            "days": days_out,
            "calls": [
                {
                    "request_id": r[0],
                    "model": r[1],
                    "route": r[2],
                    "prompt_tokens": int(r[3] or 0),
                    "completion_tokens": int(r[4] or 0),
                    "total_tokens": int(r[5] or 0),
                    "total_cost": round(float(r[6] or 0.0), 6),
                    "status": r[7],
                    "timestamp": int(r[8] or 0),
                    "latency_ms": int(r[9] or 0),
                    "status_code": int(r[10]) if r[10] is not None else None,
                    "reasoning_tokens": int(r[11] or 0),
                    "client_id": r[12],
                    "client_profile": r[13],
                    "cache_hit": bool(r[14]),
                }
                for r in calls
            ],
        }

    def set_model_cooldown(
        self,
        route: str,
        model: str,
        seconds: int = 3600,
        reason: str = "",
    ) -> None:
        """Mark ``model`` as unavailable inside ``route`` for ``seconds``.

        The route-scoped key means one route failing over from a model does
        not penalise another route that relies on the same model. Used by the
        proxy after a fallback-triggering response (429/5xx) so the whole
        fallback chain is not walked again for every request while a model's
        daily quota is exhausted.
        """
        until = int(time.time()) + max(1, int(seconds))
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO model_cooldowns "
                "(route, model, until_ts, reason) VALUES (?, ?, ?, ?)",
                (route, model, until, reason),
            )
            self._conn.commit()

    def model_in_cooldown(self, route: str, model: str) -> int:
        """Return remaining cooldown seconds for (route, model); 0 = live."""
        now = int(time.time())
        with self._lock:
            row = self._conn.execute(
                "SELECT until_ts FROM model_cooldowns "
                "WHERE route = ? AND model = ?",
                (route, model),
            ).fetchone()
        if row is None:
            return 0
        remaining = int(row[0]) - now
        if remaining <= 0:
            with self._lock:
                self._conn.execute(
                    "DELETE FROM model_cooldowns WHERE route = ? AND model = ?",
                    (route, model),
                )
                self._conn.commit()
            return 0
        return remaining

    def active_cooldowns(self) -> list[dict[str, object]]:
        """List not-yet-expired cooldowns (for the UI Routes tab)."""
        now = int(time.time())
        with self._lock:
            rows = self._conn.execute(
                "SELECT route, model, until_ts, reason FROM model_cooldowns "
                "WHERE until_ts > ? ORDER BY until_ts ASC",
                (now,),
            ).fetchall()
        return [
            {
                "route": r[0],
                "model": r[1],
                "until": int(r[2]),
                "remaining_seconds": int(r[2]) - now,
                "reason": r[3],
            }
            for r in rows
        ]

    def clear_cooldown(self, route: str, model: str) -> None:
        """Manually reset a route-scoped model cooldown."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM model_cooldowns WHERE route = ? AND model = ?",
                (route, model),
            )
            self._conn.commit()

    def usage_by_period(
        self,
        period: str = "day",
        days: int = 14,
        route: str | None = None,
    ) -> dict[str, object]:
        """Aggregate token usage per bucket per serving model.

        ``period`` selects the SQLite strftime bucket: ``hour`` (last
        ``days`` hours), ``day`` (last ``days`` days) or ``month`` (last
        ``days`` months of calendar buckets). Mirrors the daily_usage
        output shape so the UI can switch views with one renderer.
        """
        if period == "hour":
            fmt = "%Y-%m-%d %H:00"
            since = int(time.time()) - days * 3600
        elif period == "month":
            fmt = "%Y-%m"
            since = int(time.time()) - days * 30 * 86400
        else:
            fmt = "%Y-%m-%d"
            since = int(time.time()) - days * 86400
        with self._lock:
            bucket_rows = self._conn.execute(
                f"""
                SELECT strftime('{fmt}', timestamp, 'unixepoch') AS bucket, model,
                       route, SUM(prompt_tokens), SUM(completion_tokens),
                       SUM(total_tokens), COUNT(*), SUM(total_cost)
                FROM cost_records
                WHERE timestamp >= ?
                GROUP BY bucket, model, route
                ORDER BY bucket ASC
                """,
                (since,),
            ).fetchall()
            call_rows = self._conn.execute(
                """
                SELECT request_id, model, route, prompt_tokens, completion_tokens,
                       total_tokens, total_cost, status, timestamp, latency_ms,
                       status_code, reasoning_tokens, client_id, client_profile, cache_hit
                FROM cost_records
                WHERE timestamp >= ?
                ORDER BY timestamp DESC
                LIMIT 200
                """,
                (since,),
            ).fetchall()
        by_bucket: dict[str, list[dict[str, object]]] = {}
        by_bucket_route: dict[str, list[dict[str, object]]] = {}
        for bucket, model, rte, pt, ct, tt, reqs, cost in bucket_rows:
            by_bucket.setdefault(bucket, []).append(
                {
                    "model": model,
                    "prompt_tokens": int(pt or 0),
                    "completion_tokens": int(ct or 0),
                    "total_tokens": int(tt or 0),
                    "requests": int(reqs or 0),
                    "cost_usd": round(float(cost or 0.0), 6),
                }
            )
            by_bucket_route.setdefault(bucket, []).append(
                {
                    "route": rte or "",
                    "total_tokens": int(tt or 0),
                    "requests": int(reqs or 0),
                    "cost_usd": round(float(cost or 0.0), 6),
                }
            )
        buckets_out = [
            {
                "date": bucket,
                "models": by_bucket[bucket],
                "routes": by_bucket_route[bucket],
            }
            for bucket in sorted(by_bucket)
        ]
        if route:
            calls = [c for c in call_rows if c[2] == route]
        else:
            calls = list(call_rows)
        return {
            "days": buckets_out,
            "calls": [
                {
                    "request_id": r[0],
                    "model": r[1],
                    "route": r[2],
                    "prompt_tokens": int(r[3] or 0),
                    "completion_tokens": int(r[4] or 0),
                    "total_tokens": int(r[5] or 0),
                    "total_cost": round(float(r[6] or 0.0), 6),
                    "status": r[7],
                    "timestamp": int(r[8] or 0),
                    "latency_ms": int(r[9] or 0),
                    "status_code": int(r[10]) if r[10] is not None else None,
                    "reasoning_tokens": int(r[11] or 0),
                    "client_id": r[12],
                    "client_profile": r[13],
                    "cache_hit": bool(r[14]),
                }
                for r in calls
            ],
        }

    def route_status(
        self, route: str, models: list[str]
    ) -> dict[str, object]:
        """Per-target last-call + cooldown status for a route's model list.

        Returns ``{"models": {model: {...}}, "last_served": {...}}`` where
        each model entry carries ``last_called_at`` (epoch), ``last_status``
        and ``cooldown_remaining`` (seconds; 0 = live) plus the cooldown
        reason while active.
        """
        now = int(time.time())
        per_model: dict[str, object] = {}
        with self._lock:
            for model in models:
                last = self._conn.execute(
                    "SELECT timestamp, status FROM cost_records "
                    "WHERE route = ? AND model = ? "
                    "ORDER BY timestamp DESC LIMIT 1",
                    (route, model),
                ).fetchone()
                cd = self._conn.execute(
                    "SELECT until_ts, reason FROM model_cooldowns "
                    "WHERE route = ? AND model = ?",
                    (route, model),
                ).fetchone()
                remaining = (
                    max(0, int(cd[0]) - now) if cd and int(cd[0]) > now else 0
                )
                per_model[model] = {
                    "last_called_at": int(last[0]) if last else None,
                    "last_status": last[1] if last else None,
                    "cooldown_remaining": remaining,
                    "cooldown_reason": (
                        cd[1] if cd and int(cd[0]) > now else None
                    ),
                }
            served = self._conn.execute(
                "SELECT model, timestamp FROM cost_records "
                "WHERE route = ? ORDER BY timestamp DESC LIMIT 1",
                (route,),
            ).fetchone()
        return {
            "models": per_model,
            "last_served": (
                {"model": served[0], "at": int(served[1])} if served else None
            ),
        }


class CostTracker:
    """Async facade over CostStore + CostCalculator."""

    def __init__(self, store: CostStore, calculator: CostCalculator) -> None:
        self._store = store
        self._calculator = calculator

    async def record(self, usage: UsageRecord) -> None:
        """Persist a usage record (off the event loop)."""
        await asyncio.to_thread(self._store.insert, usage)

    async def spend_since(
        self, scope_key: str, since_epoch: int, tool_name: str | None = None
    ) -> float:
        """Return total spend for ``scope_key`` in the window (off the event
        loop); optionally filtered to one tool (``tool_name``).
        """
        return await asyncio.to_thread(
            self._store.spend_since, scope_key, since_epoch, tool_name
        )

    def build_record(
        self,
        *,
        request_id: str,
        scope: BudgetScope,
        model: str,
        provider: str,
        usage: TokenUsage | None,
        latency_ms: int,
        status: str,
        route: str | None = None,
        status_code: int | None = None,
    ) -> UsageRecord:
        """Assemble a UsageRecord, computing costs from usage (zero when None)."""
        if usage is not None:
            input_cost, output_cost, reasoning_cost, total_cost = self._calculator.calculate(
                model, usage.prompt_tokens, usage.completion_tokens,
                usage.reasoning_tokens,
            )
            prompt_tokens = usage.prompt_tokens
            completion_tokens = usage.completion_tokens
            total_tokens = usage.total_tokens
            reasoning_tokens = usage.reasoning_tokens
        else:
            input_cost = output_cost = reasoning_cost = total_cost = 0.0
            prompt_tokens = completion_tokens = total_tokens = reasoning_tokens = 0
        return UsageRecord(
            request_id=request_id,
            api_key=scope.key if scope.kind == "key" else "",
            user_id=scope.key if scope.kind == "user" else None,
            team=scope.key if scope.kind == "team" else None,
            model=model,
            provider=provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            input_cost=input_cost,
            output_cost=output_cost,
            reasoning_cost=reasoning_cost,
            total_cost=total_cost,
            latency_ms=latency_ms,
            status=status,
            status_code=status_code,
            timestamp=int(time.time()),
            route=route,
            reasoning_tokens=reasoning_tokens,
        )

    def estimate_cost(
        self, model: str, input_tokens: int, max_output_tokens: int,
        reasoning_tokens: int = 0,
    ) -> tuple[float, float, float]:
        """Upper-bound cost estimate (input, output, total) for a model plus a
        request size. Used by the proxy for per-target budget gates."""
        input_c, output_c, reasoning_c, total = self._calculator.calculate(
            model, input_tokens, max_output_tokens, reasoning_tokens,
        )
        return input_c, output_c, total

    def daily_usage(
        self,
        days: int = 14,
        route: str | None = None,
    ) -> dict[str, object]:
        """Aggregate token usage per day per serving model, plus the raw
        request list for the same window (OpenRouter-style usage page).
        """
        return self._store.daily_usage(days=days, route=route)

    def usage_by_period(
        self,
        period: str = "day",
        days: int = 14,
        route: str | None = None,
    ) -> dict[str, object]:
        """Aggregate usage bucketed by hour / day / month."""
        return self._store.usage_by_period(period=period, days=days, route=route)

    def clear_cooldown(self, route: str, model: str) -> None:
        """Manually reset a route-scoped model cooldown."""
        self._store.clear_cooldown(route, model)

    def route_status(
        self, route: str, models: list[str]
    ) -> dict[str, object]:
        """Per-target last-call + cooldown status for a route's model list."""
        return self._store.route_status(route, models)

    def set_model_cooldown(
        self, route: str, model: str, seconds: int = 3600, reason: str = ""
    ) -> None:
        """Mark ``model`` unavailable inside ``route`` (route-scoped)."""
        self._store.set_model_cooldown(route, model, seconds, reason)

    def model_in_cooldown(self, route: str, model: str) -> int:
        """Remaining cooldown seconds for (route, model); 0 = live."""
        return self._store.model_in_cooldown(route, model)

    def active_cooldowns(self) -> list[dict[str, object]]:
        """Not-yet-expired cooldowns for the UI."""
        return self._store.active_cooldowns()
