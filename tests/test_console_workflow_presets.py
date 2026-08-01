"""TDD acceptance contracts for guided workflow input presets."""
from __future__ import annotations

from llm_budget_gateway.console_ui import render_console
from llm_budget_gateway.console_workflows import get_workflow, step_preset


def test_each_guided_step_has_help_and_object_preset():
    for workflow_id in (
        "first-request", "investigate-spend", "recover-quota",
        "rotate-key", "prepare-release", "review-security",
    ):
        workflow = get_workflow(workflow_id)
        assert workflow is not None
        for center, capability in workflow["steps"]:
            preset = step_preset(center, capability)
            assert preset is not None
            assert preset["description"]
            assert isinstance(preset["body"], dict)


def test_high_frequency_presets_are_actionable_and_safe():
    quota = step_preset("operations", "quota-diagnostics")
    assert quota["body"]["status_code"] == 429
    assert "example" in quota["notice"].lower()
    secret = step_preset("security", "secret-scanner")
    assert "secret" not in str(secret["body"]).lower()


def test_preset_lookup_rejects_unknown_step():
    assert step_preset("missing", "missing") is None


def test_console_applies_presets_and_labels_example_data():
    page = render_console()
    for marker in (
        "Example input loaded",
        "stepHelp",
        "applyStepPreset",
        "presetNotice",
        "Review and replace example values before sending",
    ):
        assert marker in page
