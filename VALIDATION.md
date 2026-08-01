# Validation record

Release: 9.4.0  
Date: 2026-08-01

- `uv run --extra dev ruff check src tests examples`: passed
- `uv run --extra dev pytest -q`: 410 passed
- Generated Unified Console JavaScript parsed with `node --check`: passed
- Targeted console acceptance and API regression: 24 passed

See `docs/implementation-report-9.4.md` for product analysis, requirements, implementation decisions, test coverage, remaining gaps, and run instructions.
