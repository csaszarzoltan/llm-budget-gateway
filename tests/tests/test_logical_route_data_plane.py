"""TDD tests for executing logical routes on the OpenAI-compatible data plane."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import pytest

from llm_budget_gateway.config import Settings
from llm_budget_gateway.gateway_proxy import GatewayProxy, ProviderResponse
from llm_budget_gateway.routing_control_plane import RoutingControlPlane


def config() -> dict[str, object]:
    return {
        "name": "support-balanced",
        "default_model": "gpt-mini",
        "fallback_models": ["gemini-flash"],
        "monthly_budget": 1.0,
        "timezone": "Europe/Zurich",
        "schedule": {
            "weekdays": [0, 1, 2, 3, 4],
            "start": "08:00",
            "end": "18:00",
            "scheduled_model": "gpt-premium",
        },
        "quality_models": {
            "fast": "gemini-flash",
            "balanced": "gpt-mini",
            "smart": "claude-sonnet",
            "reasoning": "o3",
        },
        "fallback_statuses": [429, 500, 502, 503, 504],
        "max_cost_per_request": 0.20,
        "required_region": "eu",
        "required_capabilities": [],
    }


def seeded() -> tuple[RoutingControlPlane, str]:
    plane = RoutingControlPlane(sqlite3.connect(":memory:"))
    app = plane.create_application("Support", "support-balanced")
    route = plane.create_route(config())
    plane.publish_route(route["id"])
    return plane, app["api_key"]


def test_application_key_authentication_and_published_alias_lookup() -> None:
    plane, key = seeded()
    identity = plane.authenticate_application(key)
    assert identity["name"] == "Support"
    assert plane.has_published_route("support-balanced") is True
    assert plane.has_published_route("missing") is False
    with pytest.raises(PermissionError):
        plane.authenticate_application("bad")


def test_resolve_alias_uses_real_monthly_spend_and_health() -> None:
    plane, _ = seeded()
    first = plane.resolve_alias(
        "support-balanced",
        now=datetime(2026, 8, 4, 21, 30, tzinfo=ZoneInfo("Europe/Zurich")),
        quality_tier="balanced",
        estimated_cost=0.05,
        region="eu",
        capabilities=[],
    )
    assert first["selected_model"] == "gpt-mini"
    plane.record_model_spend(
        "support-balanced",
        "gpt-mini",
        1.0,
        at=datetime(2026, 8, 4, 21, 40, tzinfo=ZoneInfo("Europe/Zurich")),
    )
    fallback = plane.resolve_alias(
        "support-balanced",
        now=datetime(2026, 8, 4, 21, 45, tzinfo=ZoneInfo("Europe/Zurich")),
        quality_tier="balanced",
        estimated_cost=0.05,
        region="eu",
        capabilities=[],
    )
    assert fallback["selected_model"] == "gemini-flash"
    assert fallback["fallback_reason"] == "budget"
    plane.set_model_health("support-balanced", "gemini-flash", healthy=False)
    with pytest.raises(RuntimeError, match="eligible"):
        plane.resolve_alias(
            "support-balanced",
            now=datetime(2026, 8, 4, 22, 0, tzinfo=ZoneInfo("Europe/Zurich")),
            quality_tier="balanced",
            estimated_cost=0.05,
            region="eu",
            capabilities=[],
        )


@pytest.mark.asyncio
async def test_proxy_executes_logical_alias_and_adds_decision_headers() -> None:
    plane, key = seeded()
    enforcer = Mock()
    enforcer.check_sync.return_value = None
    enforcer.check_hard = AsyncMock(return_value=None)
    tracker = Mock()
    tracker.build_record.return_value = SimpleNamespace(total_cost=0.12)
    tracker.record = AsyncMock(return_value=None)
    proxy = GatewayProxy(Settings(virtual_keys={}), tracker, enforcer, Mock())
    proxy.attach_routing_control_plane(
        plane,
        now=lambda: datetime(2026, 8, 4, 21, 30, tzinfo=ZoneInfo("Europe/Zurich")),
    )
    proxy.forward = AsyncMock(
        return_value=ProviderResponse(200, {"choices": []}, {}, "gpt-mini", None, 12)
    )
    result = await proxy.handle_chat_completion(
        {
            "model": "support-balanced",
            "messages": [{"role": "user", "content": "hello"}],
            "metadata": {"quality_tier": "balanced", "region": "eu"},
        },
        key,
        {},
    )
    assert result.status_code == 200
    proxy.forward.assert_awaited_once()
    assert proxy.forward.await_args.args[0] == "gpt-mini"
    assert result.headers["X-Gateway-Route"] == "support-balanced"
    assert result.headers["X-Gateway-Serving-Model"] == "gpt-mini"
    assert plane.model_spend(
        "support-balanced",
        "gpt-mini",
        at=datetime(2026, 8, 4, 23, 0, tzinfo=ZoneInfo("Europe/Zurich")),
    ) == pytest.approx(0.12)


@pytest.mark.asyncio
async def test_proxy_rejects_unknown_alias_for_application_key() -> None:
    plane, key = seeded()
    enforcer = Mock()
    enforcer.check_sync.return_value = None
    enforcer.check_hard = AsyncMock(return_value=None)
    proxy = GatewayProxy(Settings(virtual_keys={}), Mock(), enforcer, Mock())
    proxy.attach_routing_control_plane(plane)
    result = await proxy.handle_chat_completion(
        {"model": "missing", "messages": []}, key, {}
    )
    assert result.status_code == 404


@pytest.mark.asyncio
async def test_proxy_fails_over_route_chain_on_transient_status() -> None:
    plane, key = seeded()
    enforcer = Mock()
    enforcer.check_sync.return_value = None
    enforcer.check_hard = AsyncMock(return_value=None)
    tracker = Mock()
    tracker.build_record.return_value = SimpleNamespace(total_cost=0.02)
    tracker.record = AsyncMock(return_value=None)
    proxy = GatewayProxy(Settings(virtual_keys={}), tracker, enforcer, Mock())
    proxy.attach_routing_control_plane(
        plane,
        now=lambda: datetime(2026, 8, 4, 21, 30, tzinfo=ZoneInfo("Europe/Zurich")),
    )
    proxy.forward = AsyncMock(
        side_effect=[
            ProviderResponse(
                429, {"error": {"message": "rate limited"}}, {}, "gpt-mini", None, 5
            ),
            ProviderResponse(200, {"choices": []}, {}, "gemini-flash", None, 9),
        ]
    )
    result = await proxy.handle_chat_completion(
        {"model": "support-balanced", "messages": [], "metadata": {"region": "eu"}},
        key,
        {},
    )
    assert result.status_code == 200
    assert [call.args[0] for call in proxy.forward.await_args_list] == [
        "gpt-mini",
        "gemini-flash",
    ]
    assert result.headers["X-Gateway-Serving-Model"] == "gemini-flash"
    assert result.headers["X-Gateway-Fallback"] == "provider_status_429"


def test_route_usage_reports_model_budget_headroom() -> None:
    plane, _ = seeded()
    plane.record_model_spend(
        "support-balanced",
        "gpt-mini",
        0.25,
        at=datetime(2026, 8, 4, 12, 0, tzinfo=ZoneInfo("Europe/Zurich")),
    )
    route_id = plane.list_routes()[0]["id"]
    usage = plane.route_usage(
        route_id, at=datetime(2026, 8, 4, 13, 0, tzinfo=ZoneInfo("Europe/Zurich"))
    )
    assert usage["total_spend"] == pytest.approx(0.25)
    assert usage["models"][0]["model"] == "gpt-mini"
    assert usage["models"][0]["remaining"] == pytest.approx(0.75)


def test_model_window_skips_outside_service_hours() -> None:
    plane, _ = seeded()
    plane.update_route(
        plane.list_routes()[0]["id"],
        {
            **config(),
            "default_model": "gpt-mini",
            "fallback_models": ["gemini-flash", "claude-sonnet"],
            "model_windows": {
                # gemini-flash only 09:00-17:00 Zurich weekdays
                "gemini-flash": {"weekdays": [0, 1, 2, 3, 4], "start": "09:00", "end": "17:00"},
            },
        },
    )
    route_id = plane.list_routes()[0]["id"]
    plane.publish_route(route_id)

    # gpt-mini budget exhausted -> gemini-flash is OUTSIDE window -> claude-sonnet
    plane.record_model_spend(
        "support-balanced", "gpt-mini", 1.0,
        at=datetime(2026, 8, 4, 20, 0, tzinfo=ZoneInfo("Europe/Zurich")),
    )
    decision = plane.resolve_alias(
        "support-balanced",
        now=datetime(2026, 8, 4, 20, 30, tzinfo=ZoneInfo("Europe/Zurich")),
        quality_tier="balanced",
        estimated_cost=0.05,
        region="eu",
        capabilities=[],
    )
    assert decision["selected_model"] == "claude-sonnet"
    # the first failed gate (gpt-mini budget) sets the top-level reason; the
    # window gate is visible in the decision path
    assert decision["fallback_reason"] == "budget"
    assert any(gate["gate"] == "window" and not gate["passed"] for gate in decision["decision_path"])

    # inside the window -> gemini-flash is selected
    inside = plane.resolve_alias(
        "support-balanced",
        now=datetime(2026, 8, 4, 12, 0, tzinfo=ZoneInfo("Europe/Zurich")),
        quality_tier="balanced",
        estimated_cost=0.05,
        region="eu",
        capabilities=[],
    )
    assert inside["selected_model"] == "gemini-flash"


def test_model_window_weekend_gate() -> None:
    plane, _ = seeded()
    plane.update_route(
        plane.list_routes()[0]["id"],
        {
            **config(),
            "default_model": "gpt-mini",
            "fallback_models": ["gemini-flash"],
            "model_windows": {
                # only weekdays 0-4, Saturday (5) excluded
                "gemini-flash": {"weekdays": [0, 1, 2, 3, 4], "start": "00:00", "end": "23:59"},
            },
        },
    )
    route_id = plane.list_routes()[0]["id"]
    plane.publish_route(route_id)
    plane.record_model_spend(
        "support-balanced", "gpt-mini", 1.0,
        at=datetime(2026, 8, 8, 12, 0, tzinfo=ZoneInfo("Europe/Zurich")),
    )
    with pytest.raises(RuntimeError, match="eligible"):
        plane.resolve_alias(
            "support-balanced",
            now=datetime(2026, 8, 8, 12, 0, tzinfo=ZoneInfo("Europe/Zurich")),  # Saturday
            quality_tier="balanced",
            estimated_cost=0.05,
            region="eu",
            capabilities=[],
        )


def test_model_windows_validation_rejects_bad_shape() -> None:
    plane, _ = seeded()
    route_id = plane.list_routes()[0]["id"]
    with pytest.raises(ValueError, match="model_windows"):
        plane.update_route(
            route_id,
            {
                **config(),
                "model_windows": {
                    "gemini-flash": {"start": "09:00"},  # missing weekdays/end
                },
            },
        )
    with pytest.raises(ValueError, match="weekdays"):
        plane.update_route(
            route_id,
            {
                **config(),
                "model_windows": {
                    "gemini-flash": {"weekdays": [7], "start": "09:00", "end": "17:00"},
                },
            },
        )
    with pytest.raises(ValueError, match="HH:MM"):
        plane.update_route(
            route_id,
            {
                **config(),
                "model_windows": {
                    "gemini-flash": {"weekdays": [0], "start": "9am", "end": "17:00"},
                },
            },
        )
