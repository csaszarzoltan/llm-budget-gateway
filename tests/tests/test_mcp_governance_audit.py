"""AuditStore interface + behavioral (RED) tests.

Normative per docs/architecture/mcp-governance.md §6.4. Interface tests pass
immediately; behavioral tests fail with NotImplementedError until the
implementer lands append/query.
"""

import inspect

import pytest

from llm_budget_gateway.mcp_governance import AuditEvent, AuditStore, open_mcp_db


@pytest.fixture
def conn():
    c = open_mcp_db(":memory:")
    yield c
    c.close()


def event(**overrides):
    base = dict(
        event_id="",
        server_id="srv1",
        tool_name="t1",
        caller="alice",
        scope_kind="user",
        scope_key="alice",
        decision="allowed",
        status="completed",
        cost=0.0042,
        latency_ms=10,
        timestamp=100,
    )
    base.update(overrides)
    return AuditEvent(**base)


class TestAuditStoreInterface:
    def test_constructor_accepts_conn(self, conn):
        store = AuditStore(conn)
        assert store is not None

    def test_constructor_creates_audit_table(self, conn):
        AuditStore(conn)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mcp_audit_events'"
        ).fetchall()
        assert len(rows) == 1

    def test_constructor_creates_indexes(self, conn):
        AuditStore(conn)
        idxs = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_mcp_audit_%'"
            )
        }
        assert "idx_mcp_audit_caller" in idxs
        assert "idx_mcp_audit_tool" in idxs
        assert "idx_mcp_audit_ts" in idxs

    @pytest.mark.parametrize("method", ["append", "query"])
    def test_has_method(self, method):
        assert hasattr(AuditStore, method)

    def test_append_signature(self):
        sig = inspect.signature(AuditStore.append)
        assert "event" in sig.parameters

    def test_query_keyword_only(self):
        sig = inspect.signature(AuditStore.query)
        for name in ("caller", "server_id", "tool_name", "decision", "status", "since", "until"):
            assert sig.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert sig.parameters["limit"].default == 50
        assert sig.parameters["offset"].default == 0


class TestAuditStoreBehavior:
    """RED-phase: every behavioral path raises NotImplementedError today."""

    def test_append_generates_event_id(self, conn):
        store = AuditStore(conn)
        stored = store.append(event())
        assert stored.event_id
        assert stored.event_id != ""

    def test_append_returns_stored_event(self, conn):
        store = AuditStore(conn)
        stored = store.append(event(event_id="e1"))
        assert stored.event_id == "e1"

    def test_append_replace_same_id(self, conn):
        store = AuditStore(conn)
        store.append(event(event_id="e1", cost=1.0))
        store.append(event(event_id="e1", cost=2.0))
        page = store.query()
        assert page.total == 1
        assert page.data[0].cost == 2.0

    def test_query_filters_by_caller(self, conn):
        store = AuditStore(conn)
        store.append(event(event_id="e1", caller="alice"))
        store.append(event(event_id="e2", caller="bob"))
        page = store.query(caller="alice")
        assert page.total == 1
        assert page.data[0].caller == "alice"

    def test_query_filters_by_decision(self, conn):
        store = AuditStore(conn)
        store.append(event(event_id="e1", decision="allowed"))
        store.append(event(event_id="e2", decision="denied"))
        page = store.query(decision="denied")
        assert page.total == 1

    def test_query_filters_by_timestamp_range(self, conn):
        store = AuditStore(conn)
        store.append(event(event_id="e1", timestamp=100))
        store.append(event(event_id="e2", timestamp=200))
        page = store.query(since=150, until=250)
        assert page.total == 1
        assert page.data[0].event_id == "e2"

    def test_query_orders_timestamp_desc(self, conn):
        store = AuditStore(conn)
        store.append(event(event_id="e1", timestamp=100))
        store.append(event(event_id="e2", timestamp=200))
        page = store.query()
        assert [e.event_id for e in page.data] == ["e2", "e1"]

    def test_query_pagination_and_total(self, conn):
        store = AuditStore(conn)
        for i in range(5):
            store.append(event(event_id=f"e{i}", timestamp=i))
        page = store.query(limit=2, offset=1)
        assert len(page.data) == 2
        assert page.limit == 2
        assert page.offset == 1
        assert page.total == 5

    def test_query_clamps_limit(self, conn):
        store = AuditStore(conn)
        page = store.query(limit=10_000)
        assert page.limit <= 500

    def test_query_invalid_decision_raises_valueerror(self, conn):
        store = AuditStore(conn)
        with pytest.raises(ValueError):
            store.query(decision="nope")

    def test_query_invalid_status_raises_valueerror(self, conn):
        store = AuditStore(conn)
        with pytest.raises(ValueError):
            store.query(status="nope")
