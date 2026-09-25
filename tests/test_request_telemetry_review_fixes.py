"""The four request_telemetry.py defects the Claude Code review found.

1. SECURITY: a request authenticated with an API key had its FULL SECRET
   persisted in telemetry_requests.api_key, and both
   GET /v1/observability/requests and the trace lookup return these rows
   verbatim. Confirmed live before the fix — the gateway's own key came back
   in the observability JSON response, so any Cockpit reader harvested
   working credentials.
2. A failed insert still returned the trace_id, so the caller believed the
   row existed; the gap was undetectable and never retried.
3. `row_factory` is PER-CONNECTION state: setting it on the CostStore-shared
   handle permanently changed that store's read contract (its tuple rows
   became sqlite3.Row) and the write itself ran outside the shared mutex.
4. `?limit=abc` made int() raise and 500 the observability endpoint.
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading

import pytest

from llm_budget_gateway import request_telemetry as rt


def _entry(**kw) -> rt.TelemetryEntry:
    base = dict(
        trace_id="t1",
        provider="direct",
        model="m",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        reasoning_tokens=0,
        input_cost=0.0,
        output_cost=0.0,
        reasoning_cost=0.0,
        total_cost=0.0,
        latency_ms=5,
        status="success",
        status_code=200,
    )
    base.update(kw)
    return rt.TelemetryEntry(**base)


@pytest.fixture()
def store(tmp_path) -> rt.RequestTelemetryStore:
    return rt.RequestTelemetryStore(str(tmp_path / "tel.db"))


# --- 1. the secret leak ------------------------------------------------------

def test_key_fingerprint_is_not_the_key() -> None:
    secret = "gw_supersecret_abc123"
    fp = rt._key_fingerprint(secret)
    assert fp is not None
    assert secret not in fp
    assert fp != secret
    # stable for the same key, different for another
    assert rt._key_fingerprint(secret) == fp
    assert rt._key_fingerprint("other") != fp
    # empty stays empty
    assert rt._key_fingerprint(None) is None
    assert rt._key_fingerprint("") is None


def _logger() -> "rt.RequestTelemetryLogger":
    """from_response is an instance method; it touches no store state."""
    return object.__new__(rt.RequestTelemetryLogger)


class _FakeResponse:
    """Minimal stand-in for ProviderResponse (from_response uses getattr)."""

    def __init__(self, *, model="m", latency_ms=5, status_code=200, usage=None):
        self.model = model
        self.latency_ms = latency_ms
        self.status_code = status_code
        self.usage = usage
        self.body = {"choices": [{"message": {"content": "hi"}}]}


def test_from_response_never_copies_the_raw_key() -> None:
    from llm_budget_gateway.budget_enforcement import BudgetScope

    secret = "gw_dktUxnLbj6dPI0xklC_Cq77I0l1mbw"
    entry = _logger().from_response(
        trace_id="t1",
        provider="direct",
        response=_FakeResponse(),
        scope=BudgetScope(kind="key", key=secret),
    )
    assert secret not in (entry.api_key or ""), entry.api_key
    assert entry.api_key and entry.api_key.startswith("key:")


def test_no_api_key_comes_back_out_of_the_store(store) -> None:
    """End to end: record with a secret scope, then read it back."""
    from llm_budget_gateway.budget_enforcement import BudgetScope

    secret = "gw_livekey_TOP_SECRET"
    entry = _logger().from_response(
        trace_id="t1",
        provider="direct",
        response=_FakeResponse(),
        scope=BudgetScope(kind="key", key=secret),
    )
    store.record(entry)

    rows = store.query()
    assert rows, "the row must still be recorded"
    blob = repr(rows)
    assert secret not in blob, blob[:400]

    looked_up = store.lookup(entry.trace_id)
    assert looked_up is not None
    assert secret not in repr(looked_up), repr(looked_up)[:400]


# --- 2. a failed insert must be visible -------------------------------------

def test_failed_insert_returns_none(tmp_path) -> None:
    """A write failure must not be reported as a successful trace_id."""
    store = rt.RequestTelemetryStore(str(tmp_path / "tel.db"))
    # break the insert deliberately
    store._conn.close()
    result = store.record(_entry())
    assert result is None, f"a failed insert returned {result!r}"


def test_successful_insert_returns_the_trace_id(store) -> None:
    assert store.record(_entry()) == "t1"


# --- 3. the shared connection's row_factory ---------------------------------

def test_shared_connection_keeps_its_row_factory(tmp_path) -> None:
    """Tuning one store's row_factory must not change the other's contract."""
    conn = sqlite3.connect(str(tmp_path / "shared.db"), check_same_thread=False)
    before = conn.row_factory
    lock = threading.Lock()

    _ = rt.RequestTelemetryStore(str(tmp_path / "shared.db"), connection=conn, lock=lock)
    assert conn.row_factory is before, (
        "RequestTelemetryStore mutated a connection it does not own"
    )

    # and the shared handle still yields positional rows for the other store
    cur = conn.execute("SELECT 1 AS a")
    row = cur.fetchone()
    assert row == (1,), type(row)
    conn.close()


def test_self_opened_connection_still_gets_row_access(tmp_path) -> None:
    """A store that owns its connection may configure it."""
    store = rt.RequestTelemetryStore(str(tmp_path / "own.db"))
    assert store._conn.row_factory is sqlite3.Row
    store.record(_entry())
    assert store.query(), "row access by name must still work"


# --- 4. limit/offset validation ----------------------------------------------

@pytest.mark.parametrize("bad", ["abc", None, "", "1; DROP TABLE x", -5, 10**9])
def test_query_survives_a_bad_limit(store, bad) -> None:
    store.record(_entry())
    rows = store.query(limit=bad)  # type: ignore[arg-type]
    assert isinstance(rows, list)


def test_query_limit_is_clamped(store) -> None:
    store.record(_entry())
    assert len(store.query(limit=10**9)) <= 1000  # type: ignore[arg-type]
    assert len(store.query(limit=0)) == 1  # clamped up to 1, not empty
    assert store.query(offset=-100) is not None  # type: ignore[arg-type]
