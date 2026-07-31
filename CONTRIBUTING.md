# Contributing
Use Python 3.11+, feature branches, Conventional Commits, type annotations, public API docstrings, and tests with every change. Run `python -m pytest`, `python -m pytest --cov=llm_budget_gateway`, and `python -m ruff check src tests examples` before opening a merge request. Never commit secrets or production data.


Security changes require a feature branch, fail-closed tests, and no committed API keys.

Resilience changes require failure-path tests and dedicated branches.

Optimization changes require quality guardrails, edge-case tests, and dedicated branches.

Collaboration changes require privilege-boundary tests and dedicated branches.


Platform capabilities require a dedicated branch, public docstrings, unit and API contract tests, fail-closed validation, and full regression before merge.

AgentOps changes require dedicated branches, public docstrings, fail-closed validation, unit/API/UI tests, and full regression.

Assurance features require branches, docstrings, fail-closed validation, tests, coverage, and regression.
