"""Acceptance contracts for guided multi-step console workflows."""
from __future__ import annotations

import httpx
import pytest

from llm_budget_gateway.console_api import create_console_app
from llm_budget_gateway.console_ui import render_console
from llm_budget_gateway.console_workflows import get_workflow


def test_workflow_lookup_returns_one_stable_definition():
    item = get_workflow("recover-quota")
    assert item["title"] == "Investigate 412 / 429 / 502"
    assert len(item["steps"]) == 3


def test_workflow_lookup_rejects_unknown_id():
    assert get_workflow("missing") is None


def test_console_contains_accessible_workflow_stepper():
    page = render_console()
    for marker in (
        "Guided workflow",
        "workflowProgress",
        "workflowSteps",
        "Next step",
        "Previous step",
        "aria-live='polite'",
        "startWorkflow",
        "openWorkflowStep",
    ):
        assert marker in page


@pytest.mark.asyncio
async def test_single_workflow_api_has_404_contract():
    app = create_console_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://console") as client:
        found = await client.get("/v1/console/workflows/recover-quota")
        missing = await client.get("/v1/console/workflows/missing")
    assert found.status_code == 200
    assert found.json()["workflow"]["id"] == "recover-quota"
    assert missing.status_code == 404
