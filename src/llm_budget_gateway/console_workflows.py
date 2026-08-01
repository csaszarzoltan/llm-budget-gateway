"""Task-oriented workflow catalog for frequent console jobs.

The catalog intentionally composes existing capabilities. It does not duplicate
business logic or persist credentials, prompts, request bodies, or results.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Workflow:
    id: str
    title: str
    summary: str
    roles: tuple[str, ...]
    keywords: tuple[str, ...]
    steps: tuple[tuple[str, str], ...]


WORKFLOWS = (
    Workflow(
        "first-request",
        "Send the first protected request",
        "Check readiness, estimate cost, then open the gateway request runner.",
        ("Platform engineer", "Developer"),
        ("setup", "activate", "first request", "onboarding", "provider"),
        (("activation", "configuration-doctor"), ("gateway", "cost-estimates"), ("gateway", "chat-completions")),
    ),
    Workflow(
        "investigate-spend",
        "Investigate spend and budget risk",
        "Estimate cost, explain an anomaly, and forecast period-end spend.",
        ("FinOps", "Platform engineer"),
        ("spend", "cost", "budget", "over budget", "anomaly", "forecast"),
        (("gateway", "cost-estimates"), ("intelligence", "anomalies"), ("optimization", "budget-forecast")),
    ),
    Workflow(
        "recover-quota",
        "Investigate 412 / 429 / 502",
        "Classify the failure before choosing a bounded retry, fallback, or budget action.",
        ("SRE", "Platform engineer", "Support"),
        ("412", "429", "502", "quota", "rate limit", "timeout", "provider slow", "recover"),
        (("operations", "quota-diagnostics"), ("operations", "retry-decisions"), ("resilience", "dependency-health")),
    ),
    Workflow(
        "rotate-key",
        "Review and rotate a key",
        "Assess key age and usage, then use the Control Center for a safe rotation.",
        ("Administrator", "Security"),
        ("key", "credential", "credential rotation", "rotate", "revoke", "expiry"),
        (("collaboration", "key-lifecycle"), ("control", "keys")),
    ),
    Workflow(
        "prepare-release",
        "Prepare a safe release",
        "Evaluate quality, validate rollout stages, and check rollback guardrails.",
        ("Release manager", "Quality engineer"),
        ("release", "deploy", "quality gate", "canary", "rollback"),
        (("quality", "release-gates"), ("delivery", "rollout-plan"), ("delivery", "rollback-decision")),
    ),
    Workflow(
        "review-security",
        "Review security posture",
        "Scan for secrets, check provider compliance, and review posture remediation.",
        ("Security", "Compliance"),
        ("security", "secret", "compliance", "posture", "risk"),
        (("security", "secret-scanner"), ("security", "provider-compliance"), ("security", "security-posture")),
    ),
)


def workflow_catalog() -> list[dict[str, object]]:
    """Return the immutable workflow definitions as JSON-safe dictionaries."""
    return [asdict(item) for item in WORKFLOWS]


def search_workflows(query: str) -> list[dict[str, object]]:
    """Search workflows by title, summary, role, symptom, or error code."""
    needle = query.strip().casefold()
    items = WORKFLOWS
    if needle:
        items = tuple(
            item
            for item in WORKFLOWS
            if needle
            in " ".join((item.id, item.title, item.summary, *item.roles, *item.keywords)).casefold()
        )
    return [asdict(item) for item in items]


def get_workflow(workflow_id: str) -> dict[str, object] | None:
    """Return one workflow by stable identifier, or ``None`` when unknown."""
    for item in WORKFLOWS:
        if item.id == workflow_id:
            return asdict(item)
    return None

# Safe, non-secret starter payloads for guided workflow steps. These are
# examples only and are deliberately never submitted automatically.
_STEP_PRESETS: dict[tuple[str, str], dict[str, object]] = {
    ("activation", "configuration-doctor"): {"description": "Check common configuration risks before activation.", "body": {"environment": "development", "configured_names": ["GATEWAY_VIRTUAL_KEYS"]}},
    ("gateway", "cost-estimates"): {"description": "Estimate an upper-bound request cost without contacting a provider.", "body": {"model": "gpt-4o", "messages": [{"role": "user", "content": "Replace with a representative request"}], "max_completion_tokens": 500}},
    ("gateway", "chat-completions"): {"description": "Send a protected OpenAI-compatible chat request.", "body": {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello from the guided console"}]}},
    ("intelligence", "anomalies"): {"description": "Compare a current spend period with non-negative historical periods.", "body": {"history": [12.0, 12.5, 13.0, 11.8], "current": 20.0}},
    ("optimization", "budget-forecast"): {"description": "Project period-end spend from elapsed days and current spend.", "body": {"current_spend": 250.0, "elapsed_days": 10, "period_days": 30, "budget": 1000.0}},
    ("operations", "quota-diagnostics"): {"description": "Classify an ambiguous provider or gateway failure.", "body": {"status_code": 429, "provider_code": "rate_limit", "message": "request rate exceeded"}},
    ("operations", "retry-decisions"): {"description": "Evaluate whether a retry is safe within bounded ceilings.", "body": {"status_code": 429, "attempt": 1, "max_attempts": 3, "elapsed_seconds": 1.0, "max_elapsed_seconds": 10.0, "base_delay_seconds": 0.5, "max_delay_seconds": 2.0}},
    ("resilience", "dependency-health"): {"description": "Separate required dependency outages from optional degradation.", "body": {"dependencies": [{"name": "primary-provider", "required": True, "healthy": False}]}},
    ("collaboration", "key-lifecycle"): {"description": "Assess rotation guidance from key age and inactivity without entering a key value.", "body": {"age_days": 91, "idle_days": 7, "max_age_days": 90, "max_idle_days": 30}},
    ("control", "keys"): {"description": "Open the Control Center key workflow. No credential value is included.", "body": {}},
    ("quality", "release-gates"): {"description": "Evaluate release quality against a minimum score and regression tolerance.", "body": {"scores": [0.94, 0.95, 0.93], "minimum_score": 0.9, "baseline_score": 0.94, "max_regression": 0.03}},
    ("delivery", "rollout-plan"): {"description": "Validate increasing canary stages ending at 100 percent.", "body": {"stages": [5, 25, 50, 100]}},
    ("delivery", "rollback-decision"): {"description": "Check quality, error-rate, and latency rollback guardrails.", "body": {"quality_score": 0.92, "minimum_quality": 0.9, "error_rate": 0.01, "maximum_error_rate": 0.02, "latency_ms": 800, "maximum_latency_ms": 1000}},
    ("security", "secret-scanner"): {"description": "Test local detection using benign text. Do not paste production credentials into examples.", "body": {"text": "This example contains no sensitive value."}},
    ("security", "provider-compliance"): {"description": "Evaluate provider evidence against required certifications and regions.", "body": {"required_certifications": ["soc2"], "provider_certifications": ["soc2"], "allowed_regions": ["eu"], "provider_region": "eu"}},
    ("security", "security-posture"): {"description": "Calculate a deterministic posture summary from control states.", "body": {"controls": {"authentication": True, "tenant_isolation": True, "secret_scanning": True, "replay_protection": False, "provider_compliance": True, "audit_logging": True}}},
}

_DEFAULT_PRESET_NOTICE = "Example input loaded. Review and replace example values before sending."


def step_preset(center: str, capability: str) -> dict[str, object] | None:
    """Return a defensive copy of a safe example payload for one workflow step."""
    preset = _STEP_PRESETS.get((center, capability))
    if preset is None:
        return None
    return {
        "description": str(preset["description"]),
        "body": dict(preset["body"]),
        "notice": _DEFAULT_PRESET_NOTICE,
    }
