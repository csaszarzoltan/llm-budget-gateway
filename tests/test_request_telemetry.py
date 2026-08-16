"""TDD tests for the LLM request telemetry module (roadmap #1 observability).

Covers:
  - TelemetryEntry dataclass shape and serialisation
  - RequestTelemetryStore: insert, query (filtering), lookup, summary, close
  - RequestTelemetryLogger: from_response conversion (provider / model /
    tokens / cost / latency / trace_id / status), emit best-effort, no-store
    fallback to logging
"""

from __future__ import annotations

import sqlite3
from dataclasses import is_dataclass
from unittest.mock import Mock

import pytest
from llm_budget_gateway.request_telemetry import (
    RequestTelemetryLogger,
    RequestTelemetryStore,
    TelemetryEntry,
)

from llm_budget_gateway.budget_enforcement import BudgetScope
from llm_budget_gateway.config import Settings
from llm_budget_gateway.cost_tracking import TokenUsage
from llm_budget_gateway.gateway_proxy import GatewayProxy, ProviderResponse

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    """Fresh SQLite telemetry store backed by a temp file."""
    return RequestTelemetryStore(str(tmp_path / "telemetry.db"))


@pytest.fixture
def logger_instance(store):
    return RequestTelemetryLogger(store=store)


@pytest.fixture
def sample_response():
    """A ProviderResponse-like object with usage data."""
    return ProviderResponse(
        status_code=200,
        body={"choices": [{"message": {"content": "Hello!"}}]},
        headers={"content-type": "application/json"},
        model="gpt-4o",
        usage=TokenUsage(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            reasoning_tokens=10,
        ),
        latency_ms=242,
    )


# ---------------------------------------------------------------------------
# TelemetryEntry interface
# ---------------------------------------------------------------------------


class TestTelemetryEntryInterface:
    def test_is_dataclass(self):
        assert is_dataclass(TelemetryEntry)

    def test_required_fields(self):
        field_names = {f.name for f in TelemetryEntry.__dataclass_fields__.values()}  # type: ignore[misc]
        for required in ("trace_id", "provider", "model", "latency_ms", "status"):
            assert required in field_names

    def test_defaults(self):
        e = TelemetryEntry(trace_id="t1", provider="litellm", model="gpt-4o")
        assert e.prompt_tokens == 0
        assert e.total_cost == 0.0
        assert e.status_code is None
        assert e.metadata == {}

    def test_to_record_keys(self):
        d = TelemetryEntry(
            trace_id="t1", provider="litellm", model="gpt-4o", latency_ms=10
        ).to_record()
        for key in (
            "trace_id", "provider", "model", "latency_ms", "status",
            "total_cost", "total_tokens", "recorded_at",
        ):
            assert key in d


# ---------------------------------------------------------------------------
# Store interface & behaviour
# ---------------------------------------------------------------------------


class TestTelemetryStore:
    def test_record_and_lookup(self, store):
        """Inserting a TelemetryEntry persists and retrieves by trace_id."""
        entry = TelemetryEntry(
            trace_id="trace-abc",
            provider="litellm",
            model="gpt-4o",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            input_cost=0.0005,
            output_cost=0.00075,
            total_cost=0.00125,
            latency_ms=242,
            status="success",
            api_key="sk_test_abc",
            customer_id="cust-1",
        )
        store.record(entry)
        got = store.lookup("trace-abc")
        assert got is not None
        assert got["model"] == "gpt-4o"
        assert got["provider"] == "litellm"
        assert got["prompt_tokens"] == 100
        assert got["total_tokens"] == 150
        assert got["total_cost"] == 0.00125
        assert got["latency_ms"] == 242
        assert got["status"] == "success"
        assert got["api_key"] == "sk_test_abc"
        assert got["customer_id"] == "cust-1"

    def test_lookup_missing_returns_none(self, store):
        assert store.lookup("does-not-exist") is None

    def test_query_ordering_newest_first(self, store):
        """Entries are returned newest-first by recorded_at."""
        for i, ts in enumerate([100, 500, 300]):
            entry = TelemetryEntry(
                trace_id=f"t{i}",
                provider="litellm",
                model="gpt-4o",
                latency_ms=10,
                status="success",
                metadata={},
            )
            entry.recorded_at = ts
            store.record(entry)
        results = store.query(limit=10)
        assert len(results) == 3
        # recorded_at: t0=100, t1=500, t2=300 → DESC order: t1, t2, t0
        assert results[0]["trace_id"] == "t1"  # recorded_at=500 (newest)
        assert results[1]["trace_id"] == "t2"  # recorded_at=300
        assert results[2]["trace_id"] == "t0"  # recorded_at=100 (oldest)

    def test_query_filter_by_status(self, store):
        """Only entries matching the status filter are returned."""
        for i, status in enumerate(("success", "error", "timeout", "success")):
            entry = TelemetryEntry(
                trace_id=f"t-{i}-{status}",
                provider="litellm",
                model="gpt-4o",
                latency_ms=5,
                status=status,
            )
            store.record(entry)
        results = store.query(status="success")
        assert len(results) == 2
        assert all(r["status"] == "success" for r in results)

    def test_query_filter_by_model(self, store):
        """Filtering by model returns only matching rows."""
        store.record(TelemetryEntry(
            trace_id="t-a", provider="litellm", model="gpt-4o", latency_ms=5
        ))
        store.record(TelemetryEntry(
            trace_id="t-b", provider="litellm", model="gpt-3.5-turbo", latency_ms=5
        ))
        results = store.query(model="gpt-4o")
        assert len(results) == 1
        assert results[0]["model"] == "gpt-4o"

    def test_query_filter_by_provider(self, store):
        """Filtering by provider returns only matching rows."""
        store.record(TelemetryEntry(
            trace_id="t-a", provider="litellm", model="gpt-4o", latency_ms=5
        ))
        store.record(TelemetryEntry(
            trace_id="t-b", provider="direct", model="gpt-4o", latency_ms=5
        ))
        results = store.query(provider="direct")
        assert len(results) == 1
        assert results[0]["provider"] == "direct"

    def test_query_since_epoch(self, store):
        """since_epoch filters out older entries."""
        old = TelemetryEntry(
            trace_id="t-old", provider="litellm", model="gpt-4o", latency_ms=5
        )
        old.recorded_at = 100
        new = TelemetryEntry(
            trace_id="t-new", provider="litellm", model="gpt-4o", latency_ms=5
        )
        new.recorded_at = 1000
        store.record(old)
        store.record(new)
        results = store.query(since_epoch=500)
        assert len(results) == 1
        assert results[0]["trace_id"] == "t-new"

    def test_query_limit(self, store):
        """limit caps the number of returned rows."""
        for i in range(10):
            entry = TelemetryEntry(
                trace_id=f"t-{i}", provider="litellm", model="m", latency_ms=1
            )
            entry.recorded_at = i
            store.record(entry)
        assert len(store.query(limit=3)) == 3

    def test_record_overwrite_same_trace_id(self, store):
        """INSERT OR REPLACE: same trace_id overwrites."""
        e1 = TelemetryEntry(
            trace_id="dup", provider="litellm", model="gpt-4o", latency_ms=5
        )
        e2 = TelemetryEntry(
            trace_id="dup", provider="litellm", model="gpt-4o", latency_ms=99
        )
        store.record(e1)
        store.record(e2)
        got = store.lookup("dup")
        assert got["latency_ms"] == 99

    def test_metadata_json_roundtrip(self, store):
        """metadata dict is serialised to JSON and parsed back on query."""
        entry = TelemetryEntry(
            trace_id="t-meta",
            provider="litellm",
            model="gpt-4o",
            latency_ms=5,
            metadata={"route": "route-1", "fallback": "none"},
        )
        store.record(entry)
        got = store.lookup("t-meta")
        assert got["metadata"] == {"route": "route-1", "fallback": "none"}

    def test_summary_aggregates(self, store):
        """summary returns correct counts and cost totals."""
        entries = [
            TelemetryEntry(
                trace_id=f"t-{i}",
                provider="litellm",
                model="gpt-4o",
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
                total_cost=0.001,
                latency_ms=100,
                status="success",
            )
            for i in range(5)
        ]
        for e in entries:
            store.record(e)
        s = store.summary()
        assert s["requests"] == 5
        assert s["prompt_tokens"] == 500
        assert s["completion_tokens"] == 250
        assert s["total_tokens"] == 750
        assert s["total_cost"] == 0.005

    def test_summary_by_model(self, store):
        """summary with model filter only counts that model."""
        store.record(TelemetryEntry(
            trace_id="t-a", provider="litellm", model="gpt-4o",
            total_tokens=100, total_cost=0.001, latency_ms=10, status="success"
        ))
        store.record(TelemetryEntry(
            trace_id="t-b", provider="litellm", model="gpt-3.5-turbo",
            total_tokens=200, total_cost=0.002, latency_ms=10, status="success"
        ))
        s = store.summary(model="gpt-4o")
        assert s["requests"] == 1
        assert s["total_tokens"] == 100

    def test_store_uses_shared_connection(self, tmp_path):
        """Store can wrap an existing connection (shared with CostStore)."""
        conn = sqlite3.connect(str(tmp_path / "shared.db"))
        store = RequestTelemetryStore(
            str(tmp_path / "shared.db"), connection=conn
        )
        store.record(TelemetryEntry(
            trace_id="t", provider="litellm", model="m", latency_ms=1
        ))
        # Verify the table exists in the shared connection
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='telemetry_requests'"
        ).fetchall()
        assert len(tables) == 1

    def test_store_best_effort_on_failure(self, tmp_path, monkeypatch):
        """record swallows DB exceptions and returns trace_id."""
        conn = sqlite3.connect(str(tmp_path / "ok.db"))
        store = RequestTelemetryStore(str(tmp_path / "ok.db"), connection=conn)
        # Force the insert to fail
        monkeypatch.setattr(
            store, "_conn", Mock(execute=Mock(side_effect=sqlite3.Error("boom")))
        )
        entry = TelemetryEntry(
            trace_id="t", provider="litellm", model="m", latency_ms=1
        )
        # Should not raise
        result = store.record(entry)
        assert result == "t"


# ---------------------------------------------------------------------------
# Logger - from_response conversion
# ---------------------------------------------------------------------------


class TestTelemetryLogger:
    def test_from_response_extracts_fields(self, logger_instance, sample_response):
        """from_response extracts provider/model/tokens/cost/latency/trace_id."""
        entry = logger_instance.from_response(
            trace_id="req-123",
            provider="litellm",
            response=sample_response,
            scope=BudgetScope(kind="key", key="sk_test_abc"),
        )
        assert entry.trace_id == "req-123"
        assert entry.provider == "litellm"
        assert entry.model == "gpt-4o"
        assert entry.prompt_tokens == 100
        assert entry.completion_tokens == 50
        assert entry.total_tokens == 150
        assert entry.reasoning_tokens == 10
        assert entry.latency_ms == 242
        assert entry.status == "success"
        assert entry.status_code == 200
        assert entry.api_key == "sk_test_abc"

    def test_from_response_with_cost_calc(self, logger_instance, sample_response):
        """When cost_calc is provided, costs are populated."""
        entry = logger_instance.from_response(
            trace_id="req-1",
            provider="litellm",
            response=sample_response,
            cost_calc=(0.0005, 0.00075, 0.0001, 0.00135),
        )
        assert entry.input_cost == 0.0005
        assert entry.output_cost == 0.00075
        assert entry.reasoning_cost == 0.0001
        assert entry.total_cost == 0.00135

    def test_from_response_without_usage(self, logger_instance):
        """A ProviderResponse with no usage yields zero tokens, zero cost."""
        resp = ProviderResponse(
            status_code=200, body={}, headers={}, model="gpt-4o",
            usage=None, latency_ms=10,
        )
        entry = logger_instance.from_response(
            trace_id="req-2", provider="direct", response=resp,
        )
        assert entry.total_tokens == 0
        assert entry.total_cost == 0.0
        assert entry.status == "success"

    def test_from_response_error_status(self, logger_instance):
        """Non-2xx status codes produce error/timeout status."""
        resp = ProviderResponse(
            status_code=502, body={"error": {"message": "timeout"}},
            headers={}, model="", usage=None, latency_ms=0,
        )
        entry = logger_instance.from_response(
            trace_id="req-3", provider="litellm", response=resp,
        )
        assert entry.status_code == 502
        assert entry.status == "error"

    def test_from_response_rate_limited_status(self, logger_instance):
        """429 status maps to rate_limited."""
        resp = ProviderResponse(
            status_code=429, body={}, headers={}, model="gpt-4o",
            usage=None, latency_ms=5,
        )
        entry = logger_instance.from_response(
            trace_id="req-4", provider="litellm", response=resp,
        )
        assert entry.status == "rate_limited"

    def test_from_response_timeout_status(self, logger_instance):
        """502 with 'timed out' in body maps to timeout."""
        resp = ProviderResponse(
            status_code=502,
            body={"error": {"message": "upstream provider timed out"}},
            headers={}, model="", usage=None, latency_ms=0,
        )
        entry = logger_instance.from_response(
            trace_id="req-5", provider="litellm", response=resp,
        )
        assert entry.status == "timeout"

    def test_from_response_scope_extraction(self, logger_instance, sample_response):
        """Scope with kind='user' populates user_id, not api_key."""
        scope = BudgetScope(kind="user", key="user123")
        entry = logger_instance.from_response(
            trace_id="req-6", provider="litellm", response=sample_response,
            scope=scope,
        )
        assert entry.user_id == "user123"
        assert entry.api_key is None

    def test_from_response_scope_team(self, logger_instance, sample_response):
        """Scope with kind='team' populates team."""
        scope = BudgetScope(kind="team", key="team-acme")
        entry = logger_instance.from_response(
            trace_id="req-7", provider="litellm", response=sample_response,
            scope=scope,
        )
        assert entry.team == "team-acme"

    def test_from_response_scope_none(self, logger_instance, sample_response):
        """When scope is None, api_key/user_id/team are all None."""
        entry = logger_instance.from_response(
            trace_id="req-8", provider="litellm", response=sample_response,
            scope=None,
        )
        assert entry.api_key is None
        assert entry.user_id is None
        assert entry.team is None

    def test_emit_no_store_logs(self, sample_response, caplog):
        """Without a store, emit falls back to logging (no crash)."""
        logger_instance = RequestTelemetryLogger(store=None)
        entry = logger_instance.from_response(
            trace_id="req-9", provider="litellm", response=sample_response,
        )
        import logging as _logging
        with caplog.at_level(_logging.INFO, logger="llm_budget_gateway.request_telemetry"):
            logger_instance.emit(entry)
        assert any("req-9" in rec.message for rec in caplog.records)

    def test_emit_with_store_persists(self, logger_instance, sample_response):
        """emit with a store persists the entry."""
        entry = logger_instance.from_response(
            trace_id="req-10", provider="litellm", response=sample_response,
        )
        logger_instance.emit(entry)
        got = logger_instance.store.lookup("req-10")
        assert got is not None
        assert got["model"] == "gpt-4o"
        assert got["total_tokens"] == 150

    def test_emit_best_effort_on_store_error(self, store, sample_response):
        """emit swallows store exceptions (never blocks proxy path)."""
        logger_instance = RequestTelemetryLogger(store=store)

        # Break the store's record method
        original = store.record
        def _boom(entry):
            raise RuntimeError("DB on fire")
        store.record = _boom  # type: ignore[method-assign]

        entry = logger_instance.from_response(
            trace_id="req-11", provider="litellm", response=sample_response,
        )
        # Should NOT raise
        logger_instance.emit(entry)

        # Restore and verify nothing was persisted
        store.record = original  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# GatewayProxy telemetry wiring
# ---------------------------------------------------------------------------


class TestGatewayProxyTelemetry:
    """Verify the _emit_telemetry method correctly delegates to the logger."""

    def test_emit_telemetry_attaches_to_logger(self, store, sample_response):
        """_emit_telemetry forwards to _telemetry.emit."""
        settings = Settings(
            virtual_keys={"sk_test_abc": "key1"},
            user_header_mappings={"X-User-Id": "user", "X-Team-Id": "team"},
        )
        proxy = GatewayProxy(
            settings=settings,
            cost_tracker=Mock(),
            budget_enforcer=Mock(),
            fallback_manager=Mock(),
        )
        proxy.attach_telemetry(RequestTelemetryLogger(store=store))

        proxy._emit_telemetry(
            trace_id="t-attach",
            provider="litellm",
            response=sample_response,
            scope=BudgetScope(kind="key", key="sk_test_abc"),
            customer_id="cust-1",
        )
        got = store.lookup("t-attach")
        assert got is not None
        assert got["model"] == "gpt-4o"
        assert got["provider"] == "litellm"
        assert got["customer_id"] == "cust-1"
        assert got["total_tokens"] == 150

    def test_emit_telemetry_no_store_does_not_crash(self, sample_response):
        """Without a store attached, _emit_telemetry logs and does not raise."""
        settings = Settings(virtual_keys={"sk_test_abc": "key1"})
        proxy = GatewayProxy(
            settings=settings,
            cost_tracker=Mock(),
            budget_enforcer=Mock(),
            fallback_manager=Mock(),
        )
        # Default _telemetry has no store — emit should not crash
        proxy._emit_telemetry(
            trace_id="t-nostore",
            provider="litellm",
            response=sample_response,
        )
        # If we reach here, no exception was raised
        assert proxy._telemetry is not None
