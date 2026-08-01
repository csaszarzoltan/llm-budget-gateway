"""Acceptance contracts for resumable, completion-aware guided workflows."""
from llm_budget_gateway.console_ui import render_console


def test_guided_workflows_expose_completion_and_resume_controls():
    page = render_console()
    for marker in (
        "gateway-console-workflow-progress",
        "Mark step complete",
        "Reset progress",
        "restoreWorkflowProgress",
        "saveWorkflowProgress",
        "resumeWorkflow",
        ".workflow-step.completed",
        "aria-current",
    ):
        assert marker in page


def test_workflow_progress_is_privacy_safe_and_does_not_store_runner_content():
    page = render_console()
    assert "completedSteps" in page
    assert "currentIndex" in page
    assert "workflow_started" in page
    assert "workflow_step_completed" in page
    # Progress persistence must never include secret or request/response content.
    progress_source = page[page.index("function saveWorkflowProgress"):page.index("function applyStepPreset")]
    for forbidden in ("runnerKey", "runnerBody", "result", "Authorization", "tenant"):
        assert forbidden not in progress_source


def test_request_feedback_prevents_duplicate_submission_and_explains_failures():
    page = render_console()
    for marker in (
        "aria-busy",
        "sendRequest.disabled=true",
        "sendRequest.disabled=false",
        "actionableFailure",
        "Request timed out or the service is unreachable",
    ):
        assert marker in page
