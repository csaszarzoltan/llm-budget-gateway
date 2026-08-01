"""TDD acceptance contracts for the task-oriented console experience."""
from __future__ import annotations

import httpx
import pytest

from llm_budget_gateway.console_api import create_console_app
from llm_budget_gateway.console_ui import render_console
from llm_budget_gateway.console_workflows import search_workflows, workflow_catalog


def test_workflow_catalog_targets_repeated_user_jobs():
    items = workflow_catalog()
    assert {item["id"] for item in items} == {
        "first-request",
        "investigate-spend",
        "recover-quota",
        "rotate-key",
        "prepare-release",
        "review-security",
    }
    assert all(item["steps"] and item["roles"] and item["keywords"] for item in items)


def test_workflow_search_accepts_symptoms_and_error_codes():
    assert search_workflows("429")[0]["id"] == "recover-quota"
    assert search_workflows("over budget")[0]["id"] == "investigate-spend"
    assert search_workflows("credential rotation")[0]["id"] == "rotate-key"
    assert search_workflows("does not exist") == []


def test_console_exposes_safe_repeat_use_and_validation_controls():
    page = render_console()
    for marker in (
        "Daily workflows",
        "Investigate 412 / 429 / 502",
        "Recent tasks",
        "Favorite task",
        "gateway-console-context",
        "aria-describedby='runnerBodyError'",
        "role='alert' id='runnerBodyError'",
        "validateRequestBody",
        "runnerTenant.addEventListener",
        "restoreRecentTasks",
    ):
        assert marker in page


@pytest.mark.asyncio
async def test_workflows_are_available_as_machine_readable_catalog():
    app = create_console_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://console") as client:
        response = await client.get("/v1/console/workflows?q=429")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["workflows"][0]["id"] == "recover-quota"
