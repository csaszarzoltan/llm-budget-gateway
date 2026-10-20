"""Provider plan-limit windows parsed from stored cooldown reasons."""
from __future__ import annotations

import json
import sqlite3
import time

import httpx
import pytest

from llm_budget_gateway.console_api import create_console_app
from llm_budget_gateway.cost_tracking import CostStore
from llm_budget_gateway.quota_windows import parse_quota_reason, quota_windows
from llm_budget_gateway.service_manager import ServiceManager


def _opencode(limit: str, text: str, status: int = 429) -> str:
    body = {
        "type": "error",
        "error": {
            "type": "GoUsageLimitError",
            "message": f"{text} To continue using this model now, enable usage "
            "from your available balance: https://opencode.ai/workspace/wrk_X/go",
        },
        "metadata": {"workspace": "wrk_X", "limitName": limit},
    }
    return json.dumps({"type": "http", "status_code": status, "body": json.dumps(body)})


def test_monthly_limit_is_parsed_with_reset_hint():
    parsed = parse_quota_reason(_opencode("monthly", "Monthly usage limit reached. Resets in 13 days."))

    assert parsed["window"] == "monthly"
    assert parsed["reset_hint_seconds"] == 13 * 86400


def test_weekly_limit_with_hours_and_minutes():
    parsed = parse_quota_reason(_opencode("weekly", "Weekly usage limit reached. Resets in 1hr 18min."))

    assert parsed["window"] == "weekly"
    assert parsed["reset_hint_seconds"] == 3600 + 18 * 60


def test_message_hides_workspace_urls():
    parsed = parse_quota_reason(_opencode("weekly", "Weekly usage limit reached. Resets in 2 hours."))

    assert "http" not in parsed["message"]
    assert "wrk_" not in parsed["message"]


def test_window_falls_back_to_message_text_without_metadata():
    reason = json.dumps({
        "type": "http", "status_code": 429,
        "body": json.dumps({"error": {"message": "You have hit your 5-hour usage limit. Resets in 45 min."}}),
    })

    parsed = parse_quota_reason(reason)

    assert parsed["window"] == "5-hour"
    assert parsed["reset_hint_seconds"] == 45 * 60


@pytest.mark.parametrize("reason", [
    "", "not json", '{"type": "timeout", "seconds": 90}',
    '{"type":"error","error":"upstream provider error: openrouter (HTTP 404)","body":"{}"}',
])
def test_non_quota_reasons_return_none(reason):
    assert parse_quota_reason(reason) is None


def test_quota_windows_keeps_only_quota_cooldowns_and_uses_the_cooldown_end():
    now = int(time.time())
    cooldowns = [
        {"route": "r", "model": "@a/m1", "until": now + 500, "remaining_seconds": 500,
         "reason": _opencode("weekly", "Weekly usage limit reached. Resets in 1hr."), "strikes": 1},
        {"route": "r", "model": "@a/m2", "until": now + 90, "remaining_seconds": 90,
         "reason": '{"type": "timeout", "seconds": 90}', "strikes": 5},
    ]

    result = quota_windows(cooldowns)

    assert [e["model"] for e in result] == ["@a/m1"]
    assert result[0]["window"] == "weekly"
    assert result[0]["remaining_seconds"] == 500
    assert result[0]["routes"] == ["r"]


def test_quota_windows_dedupes_the_same_model_across_routes():
    now = int(time.time())
    reason = _opencode("monthly", "Monthly usage limit reached. Resets in 2 days.")
    cooldowns = [
        {"route": "a", "model": "@x/m", "until": now + 100, "remaining_seconds": 100, "reason": reason, "strikes": 0},
        {"route": "b", "model": "@x/m", "until": now + 300, "remaining_seconds": 300, "reason": reason, "strikes": 0},
    ]

    result = quota_windows(cooldowns)

    assert len(result) == 1
    assert result[0]["remaining_seconds"] == 300
    assert sorted(result[0]["routes"]) == ["a", "b"]


@pytest.mark.asyncio
async def test_quota_endpoint_lists_active_plan_limits(tmp_path):
    def conn(name: str) -> sqlite3.Connection:
        return sqlite3.connect(tmp_path / name, check_same_thread=False)

    store = CostStore(connection=conn("cost.db"))
    store.set_model_cooldown(
        "r", "@a/m1", seconds=600,
        reason=_opencode("weekly", "Weekly usage limit reached. Resets in 1hr."),
    )
    app = create_console_app(
        manager=ServiceManager(workdir=tmp_path), project_root=tmp_path,
        product_connection=conn("product.db"), provider_connection=conn("providers.db"),
        routing_connection=conn("routing.db"), cost_connection=conn("cost.db"),
        credential_key_path=tmp_path / "k.key", alerts_db_path=tmp_path / "a.db",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://console") as client:
        body = (await client.get("/v1/product/quota")).json()

    assert body["limits"][0]["model"] == "@a/m1"
    assert body["limits"][0]["window"] == "weekly"
    assert body["limits"][0]["remaining_seconds"] > 0


def test_out_of_credits_is_labelled_credits_and_message_has_no_dangling_preposition():
    reason = json.dumps({
        "type": "error", "error": "upstream provider error: openai (HTTP 429)",
        "body": json.dumps({"error": {
            "message": "You have no credits remaining. Add credits to continue using the API at "
            "https://platform.openai.com/settings/organization/billing/.",
            "type": "insufficient_quota",
        }}),
    })

    parsed = parse_quota_reason(reason)

    assert parsed["window"] == "credits"
    assert parsed["message"].endswith("continue using the API")


def test_quota_entries_carry_the_provider_account():
    now = int(time.time())
    reason = _opencode("monthly", "Monthly usage limit reached. Resets in 2 days.")
    result = quota_windows([
        {"route": "r", "model": "@opencode-go2/m1", "until": now + 10, "remaining_seconds": 10,
         "reason": reason, "strikes": 0},
    ])

    assert result[0]["provider"] == "opencode-go2"


def test_quota_accounts_group_parked_models_per_provider_account():
    from llm_budget_gateway.quota_windows import quota_accounts

    now = int(time.time())
    reason = _opencode("weekly", "Weekly usage limit reached. Resets in 1hr.")
    limits = quota_windows([
        {"route": "r", "model": "@opencode-go/a", "until": now + 100, "remaining_seconds": 100, "reason": reason, "strikes": 0},
        {"route": "r", "model": "@opencode-go/b", "until": now + 400, "remaining_seconds": 400, "reason": reason, "strikes": 0},
        {"route": "r", "model": "@opencode-go2/a", "until": now + 50, "remaining_seconds": 50, "reason": reason, "strikes": 0},
    ])

    accounts = quota_accounts(limits)

    by_provider = {a["provider"]: a for a in accounts}
    assert by_provider["opencode-go"]["models"] == 2
    assert by_provider["opencode-go"]["remaining_seconds"] == 400
    assert by_provider["opencode-go2"]["models"] == 1
    assert [a["provider"] for a in accounts] == ["opencode-go2", "opencode-go"]  # soonest back first


def test_models_without_provider_prefix_group_under_unknown():
    from llm_budget_gateway.quota_windows import quota_accounts

    now = int(time.time())
    reason = _opencode("weekly", "Weekly usage limit reached. Resets in 1hr.")
    limits = quota_windows([
        {"route": "r", "model": "plain-model", "until": now + 5, "remaining_seconds": 5, "reason": reason, "strikes": 0},
    ])

    assert quota_accounts(limits)[0]["provider"] == "unknown"


def test_oversized_provider_message_is_parsed_in_bounded_time():
    import time as _time

    huge = "Weekly usage limit reached." + " " * 20000 + "x"
    reason = json.dumps({"type": "http", "status_code": 429, "body": json.dumps(
        {"error": {"type": "GoUsageLimitError", "message": huge}, "metadata": {"limitName": "weekly"}})})

    started = _time.perf_counter()
    parsed = parse_quota_reason(reason)

    assert _time.perf_counter() - started < 0.5
    assert parsed["window"] == "weekly"
    assert len(parsed["message"]) <= 500
