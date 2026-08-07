"""Cost attribution: per-customer spend tracking, aggregation, budget, and CSV export.

Implements the CostAttributionStore backed by SQLite — customers, customer_budgets
tables, and per-customer aggregation queries over the existing cost_records ledger.
"""

from __future__ import annotations

import calendar
import datetime
import secrets
import sqlite3
import time
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Dataclasses (brief §4.3)
# ---------------------------------------------------------------------------

@dataclass
class CustomerSpendSummary:
    customer_id: str
    customer_name: str
    mtd_cost_usd: float
    mtd_calls: int
    mtd_total_tokens: int
    mtd_prompt_tokens: int
    mtd_completion_tokens: int


@dataclass
class DailySpendPoint:
    date: str  # YYYY-MM-DD
    cost_usd: float
    calls: int
    total_tokens: int


@dataclass
class ModelSpend:
    model: str
    cost_usd: float
    calls: int
    total_tokens: int


@dataclass
class CustomerBudgetStatus:
    customer_id: str
    monthly_limit_usd: float
    mtd_spend_usd: float
    percent_used: float  # clamp 0..100
    remaining_usd: float
    reset_day: int  # 1 (calendar month)


@dataclass
class UsageLedgerRow:
    customer: str
    timestamp: str  # ISO-8601 UTC
    model: str
    tokens: int  # total_tokens
    cost: float  # total_cost


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------

_CREATE_CUSTOMERS = """
CREATE TABLE IF NOT EXISTS customers (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    tenant        TEXT NOT NULL DEFAULT 'local',
    created_at    INTEGER NOT NULL
)
"""

_CREATE_CUSTOMER_BUDGETS = """
CREATE TABLE IF NOT EXISTS customer_budgets (
    customer_id       TEXT PRIMARY KEY REFERENCES customers(id) ON DELETE CASCADE,
    monthly_limit_usd REAL NOT NULL CHECK (monthly_limit_usd > 0),
    updated_at        INTEGER NOT NULL
)
"""


def _ensure_tables(conn: sqlite3.Connection) -> None:
    """Create customers / customer_budgets tables if they don't exist."""
    conn.execute(_CREATE_CUSTOMERS)
    conn.execute(_CREATE_CUSTOMER_BUDGETS)
    conn.commit()


def _month_start_epoch(now_epoch: int) -> int:
    """Return the UTC epoch of the first second of the current month for *now_epoch*."""
    dt = datetime.datetime.fromtimestamp(now_epoch, tz=datetime.UTC)
    return int(calendar.timegm(dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).utctimetuple()))


def _clamp_days(days: int) -> int:
    """Clamp *days* to the range 1..90."""
    return max(1, min(90, days))


def _mtd_since_epoch(now_epoch: int | None = None) -> int:
    """Return the epoch of start-of-month for *now_epoch* (or current time if None)."""
    ts = now_epoch if now_epoch is not None else int(time.time())
    return _month_start_epoch(ts)


# ---------------------------------------------------------------------------
# Store (brief §4.4)
# ---------------------------------------------------------------------------

class CostAttributionStore:
    """Per-customer cost attribution store backed by SQLite."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an existing SQLite connection (typically from CostStore)."""
        self._conn = connection
        self._conn.row_factory = sqlite3.Row

    # -- Customers CRUD ----------------------------------------------------

    def create_customer(self, name: str, tenant: str = "local") -> dict:
        """Persist a customer; raises ValueError on duplicate name."""
        _ensure_tables(self._conn)
        # Check for duplicate
        existing = self._conn.execute(
            "SELECT id FROM customers WHERE name = ?", (name,)
        ).fetchone()
        if existing is not None:
            raise ValueError(f"customer name already exists: {name!r}")

        customer_id = f"cus_{secrets.token_hex(8)}"
        now = int(time.time())
        self._conn.execute(
            "INSERT INTO customers (id, name, tenant, created_at) VALUES (?, ?, ?, ?)",
            (customer_id, name, tenant, now),
        )
        self._conn.commit()
        return {"id": customer_id, "name": name, "tenant": tenant, "created_at": now}

    def list_customers(self) -> list[dict]:
        """Return all customers with MTD summary per customer."""
        _ensure_tables(self._conn)
        since = _mtd_since_epoch()
        rows = self._conn.execute(
            """
            SELECT c.id, c.name, c.tenant, c.created_at,
                   COALESCE(SUM(cr.total_cost), 0) AS mtd_cost_usd,
                   COUNT(cr.request_id) AS mtd_calls,
                   COALESCE(SUM(cr.total_tokens), 0) AS mtd_total_tokens,
                   COALESCE(SUM(cr.prompt_tokens), 0) AS mtd_prompt_tokens,
                   COALESCE(SUM(cr.completion_tokens), 0) AS mtd_completion_tokens
            FROM customers c
            LEFT JOIN cost_records cr ON cr.customer_id = c.id
                                     AND cr.timestamp >= ?
            GROUP BY c.id
            ORDER BY c.created_at ASC
            """,
            (since,),
        ).fetchall()
        result = []
        for row in rows:
            result.append({
                "id": row["id"],
                "name": row["name"],
                "tenant": row["tenant"],
                "created_at": row["created_at"],
                "mtd_cost_usd": float(row["mtd_cost_usd"]),
                "mtd_calls": int(row["mtd_calls"]),
                "mtd_total_tokens": int(row["mtd_total_tokens"]),
                "mtd_prompt_tokens": int(row["mtd_prompt_tokens"]),
                "mtd_completion_tokens": int(row["mtd_completion_tokens"]),
            })
        return result

    def get_customer(self, customer_id: str) -> dict | None:
        """Return customer dict or None if not found."""
        _ensure_tables(self._conn)
        row = self._conn.execute(
            "SELECT id, name, tenant, created_at FROM customers WHERE id = ?",
            (customer_id,),
        ).fetchone()
        if row is None:
            return None
        return {"id": row["id"], "name": row["name"], "tenant": row["tenant"], "created_at": row["created_at"]}

    # -- Budget ------------------------------------------------------------

    def set_monthly_budget(self, customer_id: str, limit_usd: float) -> dict:
        """Upsert monthly budget; returns budget status dict."""
        _ensure_tables(self._conn)
        now = int(time.time())
        self._conn.execute(
            """
            INSERT INTO customer_budgets (customer_id, monthly_limit_usd, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(customer_id) DO UPDATE SET
                monthly_limit_usd = excluded.monthly_limit_usd,
                updated_at = excluded.updated_at
            """,
            (customer_id, limit_usd, now),
        )
        self._conn.commit()
        budget = self.get_budget(customer_id)
        return budget or {"customer_id": customer_id, "monthly_limit_usd": limit_usd,
                          "mtd_spend_usd": 0.0, "percent_used": 0.0,
                          "remaining_usd": limit_usd, "reset_day": 1}

    def get_budget(self, customer_id: str) -> dict | None:
        """Return budget status dict or None if no budget set."""
        _ensure_tables(self._conn)
        row = self._conn.execute(
            "SELECT monthly_limit_usd FROM customer_budgets WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()
        if row is None:
            return None
        limit = float(row["monthly_limit_usd"])
        # Compute MTD spend from ledger (not a stale counter)
        since = _mtd_since_epoch()
        spend_row = self._conn.execute(
            "SELECT COALESCE(SUM(total_cost), 0) FROM cost_records "
            "WHERE customer_id = ? AND timestamp >= ?",
            (customer_id, since),
        ).fetchone()
        mtd_spend = float(spend_row[0]) if spend_row else 0.0
        percent = min(100.0, mtd_spend / limit * 100.0) if limit > 0 else 0.0
        remaining = max(0.0, limit - mtd_spend)
        return {
            "customer_id": customer_id,
            "monthly_limit_usd": limit,
            "mtd_spend_usd": mtd_spend,
            "percent_used": percent,
            "remaining_usd": remaining,
            "reset_day": 1,
        }

    # -- Aggregations (all customer-scoped, UTC) ---------------------------

    def mtd_summary(self, customer_id: str, now_epoch: int | None = None) -> CustomerSpendSummary:
        """Month-to-date spend summary for a customer."""
        since = _mtd_since_epoch(now_epoch)
        row = self._conn.execute(
            """
            SELECT COUNT(*) AS calls,
                   COALESCE(SUM(total_cost), 0) AS cost,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens
            FROM cost_records
            WHERE customer_id = ? AND timestamp >= ?
            """,
            (customer_id, since),
        ).fetchone()
        # Fetch customer name
        cust_row = self._conn.execute(
            "SELECT name FROM customers WHERE id = ?", (customer_id,)
        ).fetchone()
        name = cust_row["name"] if cust_row else ""
        return CustomerSpendSummary(
            customer_id=customer_id,
            customer_name=name,
            mtd_cost_usd=float(row["cost"]),
            mtd_calls=int(row["calls"]),
            mtd_total_tokens=int(row["total_tokens"]),
            mtd_prompt_tokens=int(row["prompt_tokens"]),
            mtd_completion_tokens=int(row["completion_tokens"]),
        )

    def daily_spend(self, customer_id: str, days: int = 31, granularity: str = "day") -> list[DailySpendPoint]:
        """Daily/weekly/monthly spend points for a customer."""
        days = _clamp_days(days)
        now = int(time.time())
        since = now - days * 86400

        if granularity == "week":
            date_expr = "strftime('%Y-W%W', timestamp, 'unixepoch')"
        elif granularity == "month":
            date_expr = "strftime('%Y-%m', timestamp, 'unixepoch')"
        else:  # day (default)
            date_expr = "date(timestamp, 'unixepoch')"

        rows = self._conn.execute(
            f"""
            SELECT {date_expr} AS day,
                   COALESCE(SUM(total_cost), 0) AS cost_usd,
                   COUNT(*) AS calls,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens
            FROM cost_records
            WHERE customer_id = ? AND timestamp >= ?
            GROUP BY day
            ORDER BY day ASC
            """,
            (customer_id, since),
        ).fetchall()

        return [
            DailySpendPoint(
                date=row["day"],
                cost_usd=float(row["cost_usd"]),
                calls=int(row["calls"]),
                total_tokens=int(row["total_tokens"]),
            )
            for row in rows
        ]

    def spend_by_model(self, customer_id: str, since_epoch: int | None = None) -> list[ModelSpend]:
        """Breakdown by model, sorted by cost desc."""
        since = _mtd_since_epoch(since_epoch) if since_epoch is None else since_epoch
        rows = self._conn.execute(
            """
            SELECT model,
                   COALESCE(SUM(total_cost), 0) AS cost_usd,
                   COUNT(*) AS calls,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens
            FROM cost_records
            WHERE customer_id = ? AND timestamp >= ?
            GROUP BY model
            ORDER BY SUM(total_cost) DESC
            """,
            (customer_id, since),
        ).fetchall()
        return [
            ModelSpend(
                model=row["model"],
                cost_usd=float(row["cost_usd"]),
                calls=int(row["calls"]),
                total_tokens=int(row["total_tokens"]),
            )
            for row in rows
        ]

    def ledger_rows(self, customer_id: str, limit: int = 10000) -> list[UsageLedgerRow]:
        """CSV-ready ledger rows, newest first."""
        rows = self._conn.execute(
            """
            SELECT c.name AS customer,
                   strftime('%Y-%m-%dT%H:%M:%SZ', cr.timestamp, 'unixepoch') AS timestamp,
                   cr.model,
                   cr.total_tokens AS tokens,
                   cr.total_cost AS cost
            FROM cost_records cr
            JOIN customers c ON c.id = cr.customer_id
            WHERE cr.customer_id = ?
            ORDER BY cr.timestamp DESC
            LIMIT ?
            """,
            (customer_id, limit),
        ).fetchall()
        return [
            UsageLedgerRow(
                customer=row["customer"],
                timestamp=row["timestamp"],
                model=row["model"],
                tokens=int(row["tokens"]),
                cost=float(row["cost"]),
            )
            for row in rows
        ]
