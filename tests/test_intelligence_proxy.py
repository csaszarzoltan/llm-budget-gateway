"""Integrated intelligence: cache hit, PII redaction, cost-aware routing in the proxy path."""
from unittest.mock import MagicMock

import pytest

from llm_budget_gateway.budget_enforcement import BudgetEnforcer
from llm_budget_gateway.config import Settings
from llm_budget_gateway.cost_tracking import (
    CostCalculator,
    CostStore,
    CostTracker,
    PriceMap,
)
from llm_budget_gateway.gateway_proxy import GatewayProxy
from llm_budget_gateway.market_features import (
    CostAwareRouter,
    ExactResponseCache,
    PIIRedactor,
)
from llm_budget_gateway.model_fallback import FallbackManager


def _make_proxy(tmp_path, with_intel=True):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/gw.db")
    price_map = PriceMap(overrides={})
    tracker = CostTracker(store=CostStore(f"{tmp_path}/gw.db"), calculator=CostCalculator(price_map))
    enforcer = BudgetEnforcer(configs=[], cost_tracker=tracker, counter_store=MagicMock())
    manager = FallbackManager(configs=[], counter_store=MagicMock())
    proxy = GatewayProxy(settings=settings, cost_tracker=tracker, budget_enforcer=enforcer, fallback_manager=manager)
    if with_intel:
        proxy.attach_intelligence(
            cache=ExactResponseCache(f"{tmp_path}/intel.db"),
            redactor=PIIRedactor(),
            cost_router=CostAwareRouter(),
        )
    # A fake product console with one route so _handle_logical_route works.
    store = MagicMock()
    store.authenticate_application = MagicMock()  # no raise -> product route path
    store.published_route_by_name = MagicMock(return_value={
        "name": "r1",
        "targets": [
            {"model": "@google/gemini-3.6-flash", "priority": 10, "timezone": "UTC",
             "start": "00:00", "end": "23:59", "required_capabilities": [],
             "on_status_codes": [429, 500], "timeout_seconds": 60, "cooldown_seconds": 60},
        ],
    })
    store.record_request = MagicMock()
    proxy.attach_product_console(store)
    proxy._routing_now = lambda: __import__("datetime").datetime.now(__import__("datetime").UTC)
    return proxy


class _FakeDirect:
    """Direct client that echoes a canned response."""
    def __init__(self, body):
        self.body = body
        self.calls = 0
    def resolve(self, model):  # direct client knows every model
        return model
    async def forward(self, model, body, *, kind="chat", timeout=None, stream=False):
        self.calls += 1
        return 200, self.body, model


@pytest.mark.asyncio
async def test_cache_hit_skips_provider(tmp_path):
    proxy = _make_proxy(tmp_path)
    fake = _FakeDirect({"choices": [{"message": {"content": "cached"}}], "model": "x"})
    proxy.attach_direct_client(fake)
    body = {"model": "r1", "messages": [{"role": "user", "content": "hello"}]}
    headers = {"x-gateway-cache": "1"}
    r1 = await proxy.handle_chat_completion(body, "gw_test", headers)
    assert r1.status_code == 200
    assert fake.calls == 1
    r2 = await proxy.handle_chat_completion(body, "gw_test", headers)
    assert r2.status_code == 200
    assert r2.headers.get("X-Gateway-Cache-Hit") == "1"
    assert fake.calls == 1  # provider NOT called again


@pytest.mark.asyncio
async def test_pii_redaction_rewrites_user_message(tmp_path):
    proxy = _make_proxy(tmp_path)
    captured = {}
    fake = _FakeDirect({"choices": [{"message": {"content": "ok"}}], "model": "x"})

    class Wrapped(_FakeDirect):
        async def forward(self, model, body, **kw):
            captured["outbound"] = body
            return await fake.forward(model, body, **kw)

    proxy.attach_direct_client(Wrapped({"choices": [{"message": {"content": "ok"}}], "model": "x"}))
    body = {"model": "r1", "messages": [{"role": "user", "content": "Write to a@b.com please"}]}
    headers = {"x-gateway-redact-pii": "1"}
    r = await proxy.handle_chat_completion(body, "gw_test", headers)
    assert r.status_code == 200
    assert "[REDACTED_EMAIL]" in captured["outbound"]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_cost_aware_reroutes_to_cheapest(tmp_path):
    proxy = _make_proxy(tmp_path)
    store = MagicMock()
    store.authenticate_application = MagicMock()
    store.published_route_by_name = MagicMock(return_value={
        "name": "r1",
        "targets": [
            {"model": "expensive", "priority": 10, "timezone": "UTC", "start": "00:00",
             "end": "23:59", "required_capabilities": [], "on_status_codes": [429], "cooldown_seconds": 60},
            {"model": "cheap", "priority": 20, "timezone": "UTC", "start": "00:00",
             "end": "23:59", "required_capabilities": [], "on_status_codes": [429], "cooldown_seconds": 60},
        ],
    })
    store.record_request = MagicMock()
    proxy.attach_product_console(store)
    # price overrides: expensive = high, cheap = 0
    from llm_budget_gateway.cost_tracking import ModelPrice
    proxy._cost_tracker._calculator._price_map.add_override("expensive", ModelPrice(10.0, 10.0))
    proxy._cost_tracker._calculator._price_map.add_override("cheap", ModelPrice(0.0, 0.0))
    fake = _FakeDirect({"choices": [{"message": {"content": "hi"}}], "model": "cheap"})
    proxy.attach_direct_client(fake)
    body = {"model": "r1", "messages": [{"role": "user", "content": "hi"}], "metadata": {"cost_aware": "true"}}
    r = await proxy.handle_chat_completion(body, "gw_test", {})
    assert r.status_code == 200
    assert "cheap" in (fake.calls, r.headers.get("X-Gateway-Serving-Model", ""))
