"""ToolBudgetStore + ToolBudgetService + budget_window_seconds tests.

Normative per docs/architecture/mcp-governance.md §6.3 and §9.2. Interface
tests pass immediately; behavioral tests fail with NotImplementedError until
the implementer lands the store CRUD, the service checks and the extracted
budget_window_seconds() function. The ledger is faked per spec §11.3.
"""

import inspect

import pytest

from llm_budget_gateway.budget_enforcement import (
    BudgetExceededError,
    BudgetScope,
    budget_window_seconds,
)
from llm_budget_gateway.mcp_governance import (
    AuditEvent,
    BudgetNotFoundError,
    DuplicateBudgetError,
    ToolBudgetRequest,
    ToolBudgetService,
    ToolBudgetStore,
    open_mcp_db,
)

ALICE_SCOPES = [
    BudgetScope("user", "alice"),
    BudgetScope("team", "eng"),
    BudgetScope("project", "p1"),
    BudgetScope("global", "default"),
]


class FakeTracker:
    """Spec §11.3 fake ledger: controllable spend + recorded UsageRecords."""

    def __init__(self, spend: float = 0.0):
        self.spend = spend
        self.records = []

    async def spend_since(self, scope_key, since_epoch, tool_name=None) -> float:
        return self.spend

    async def record(self, usage) -> None:
        self.records.append(usage)


@pytest.fixture
def conn():
    c = open_mcp_db(":memory:")
    yield c
    c.close()


def budget_request(**overrides):
    base = dict(scope_kind="user", scope_key="alice", hard_limit=5.0)
    base.update(overrides)
    return ToolBudgetRequest(**base)


class TestToolBudgetStoreInterface:
    def test_constructor_accepts_conn(self, conn):
        store = ToolBudgetStore(conn)
        assert store is not None

    def test_constructor_creates_budgets_table(self, conn):
        ToolBudgetStore(conn)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mcp_budgets'"
        ).fetchall()
        assert len(rows) == 1

    @pytest.mark.parametrize("method", ["create_budget", "list_budgets", "get_budget", "delete_budget"])
    def test_has_method(self, method):
        assert hasattr(ToolBudgetStore, method)

    def test_list_budgets_keyword_only(self):
        sig = inspect.signature(ToolBudgetStore.list_budgets)
        assert sig.parameters["scope_kind"].kind is inspect.Parameter.KEYWORD_ONLY


class TestToolBudgetServiceInterface:
    def test_constructor_accepts_tracker_and_store(self, conn):
        svc = ToolBudgetService(FakeTracker(), ToolBudgetStore(conn))
        assert svc is not None

    def test_constructor_default_now_fn(self, conn):
        svc = ToolBudgetService(FakeTracker(), ToolBudgetStore(conn))
        assert svc is not None

    def test_has_methods(self, conn):
        svc = ToolBudgetService(FakeTracker(), ToolBudgetStore(conn))
        for name in ("applicable_budgets", "check", "soft_exceeded", "record_usage", "canonical_tool"):
            assert hasattr(svc, name)

    def test_check_is_async(self):
        assert inspect.iscoroutinefunction(ToolBudgetService.check)

    def test_soft_exceeded_is_async(self):
        assert inspect.iscoroutinefunction(ToolBudgetService.soft_exceeded)

    def test_record_usage_is_async(self):
        assert inspect.iscoroutinefunction(ToolBudgetService.record_usage)


class TestBudgetWindowSecondsInterface:
    def test_function_exists(self):
        assert callable(budget_window_seconds)

    def test_signature(self):
        sig = inspect.signature(budget_window_seconds)
        assert "window" in sig.parameters
        assert sig.parameters["now_fn"].default is None


class TestToolBudgetStoreBehavior:
    """RED-phase: every behavioral path raises NotImplementedError today."""

    def test_create_budget_returns_tool_budget(self, conn):
        store = ToolBudgetStore(conn)
        b = store.create_budget(budget_request())
        assert b.budget_id
        assert b.hard_limit == 5.0

    def test_create_budget_duplicate_4tuple_raises(self, conn):
        store = ToolBudgetStore(conn)
        store.create_budget(budget_request(server_id="srv1", tool_name="t1"))
        with pytest.raises(DuplicateBudgetError):
            store.create_budget(budget_request(server_id="srv1", tool_name="t1"))

    def test_list_budgets_returns_all(self, conn):
        store = ToolBudgetStore(conn)
        store.create_budget(budget_request())
        store.create_budget(budget_request(scope_kind="team", scope_key="eng"))
        assert len(store.list_budgets()) == 2

    def test_list_budgets_filters(self, conn):
        store = ToolBudgetStore(conn)
        store.create_budget(budget_request())
        store.create_budget(budget_request(scope_kind="team", scope_key="eng"))
        assert len(store.list_budgets(scope_kind="user")) == 1

    def test_get_budget_returns_budget(self, conn):
        store = ToolBudgetStore(conn)
        b = store.create_budget(budget_request())
        assert store.get_budget(b.budget_id).hard_limit == 5.0

    def test_get_budget_unknown_raises(self, conn):
        store = ToolBudgetStore(conn)
        with pytest.raises(BudgetNotFoundError):
            store.get_budget("nope")

    def test_delete_budget_removes(self, conn):
        store = ToolBudgetStore(conn)
        b = store.create_budget(budget_request())
        store.delete_budget(b.budget_id)
        with pytest.raises(BudgetNotFoundError):
            store.get_budget(b.budget_id)

    def test_delete_budget_unknown_raises(self, conn):
        store = ToolBudgetStore(conn)
        with pytest.raises(BudgetNotFoundError):
            store.delete_budget("nope")


class TestToolBudgetServiceBehavior:
    """RED-phase: every behavioral path raises NotImplementedError today."""

    def test_applicable_budgets_matching(self, conn):
        store = ToolBudgetStore(conn)
        store.create_budget(budget_request(server_id="srv1", tool_name="t1"))
        svc = ToolBudgetService(FakeTracker(), store)
        budgets = svc.applicable_budgets(ALICE_SCOPES, "srv1", "t1")
        assert len(budgets) == 1

    @pytest.mark.asyncio
    async def test_check_passes_under_limit(self, conn):
        store = ToolBudgetStore(conn)
        store.create_budget(budget_request(hard_limit=10.0))
        svc = ToolBudgetService(FakeTracker(spend=1.0), store)
        await svc.check(ALICE_SCOPES, "srv1", "t1")

    @pytest.mark.asyncio
    async def test_check_raises_budget_exceeded(self, conn):
        store = ToolBudgetStore(conn)
        store.create_budget(budget_request(hard_limit=5.0))
        svc = ToolBudgetService(FakeTracker(spend=9.0), store)
        with pytest.raises(BudgetExceededError):
            await svc.check(ALICE_SCOPES, "srv1", "t1")

    @pytest.mark.asyncio
    async def test_check_passes_with_no_applicable_budgets(self, conn):
        store = ToolBudgetStore(conn)
        svc = ToolBudgetService(FakeTracker(spend=99.0), store)
        await svc.check(ALICE_SCOPES, "srv1", "t1")

    @pytest.mark.asyncio
    async def test_soft_exceeded_returns_scopes(self, conn):
        store = ToolBudgetStore(conn)
        store.create_budget(budget_request(soft_limit=2.0))
        svc = ToolBudgetService(FakeTracker(spend=5.0), store)
        exceeded = await svc.soft_exceeded(ALICE_SCOPES, "srv1", "t1")
        assert any(s.kind == "user" and s.key == "alice" for s in exceeded)

    @pytest.mark.asyncio
    async def test_record_usage_writes_ledger(self, conn):
        store = ToolBudgetStore(conn)
        tracker = FakeTracker()
        svc = ToolBudgetService(tracker, store)
        event = AuditEvent(
            event_id="e1",
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
        await svc.record_usage(event=event)
        assert len(tracker.records) == 1
        rec = tracker.records[0]
        assert rec.tool_name == "srv1:t1"
        assert rec.total_cost == 0.0042

    def test_canonical_tool(self, conn):
        svc = ToolBudgetService(FakeTracker(), ToolBudgetStore(conn))
        assert svc.canonical_tool("srv1", "t1") == "srv1:t1"


class TestBudgetWindowSecondsBehavior:
    """RED-phase: the extracted function is a NotImplementedError stub today."""

    def test_30d_window(self):
        assert budget_window_seconds("30d") == 30 * 86400

    def test_30m_window(self):
        assert budget_window_seconds("30m") == 30 * 60

    def test_daily_window(self):
        assert budget_window_seconds("daily") == 86400

    def test_bad_window_raises_valueerror(self):
        with pytest.raises(ValueError):
            budget_window_seconds("1y")
