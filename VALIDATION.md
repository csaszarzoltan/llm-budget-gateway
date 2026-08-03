# Validation record

Release: 9.4.0  \
Date: 2026-08-01

- `uv run --extra dev ruff check src tests examples`: passed
- `uv run --extra dev pytest -q`: 410 passed
- Generated Unified Console JavaScript parsed with `node --check`: passed
- Targeted console acceptance and API regression: 24 passed

See `docs/implementation-report-9.4.md` for product analysis, requirements, implementation decisions, test coverage, remaining gaps, and run instructions.

## MCP governance validation (2026-08-02)

- `.venv/bin/python -m ruff check src tests examples`: clean (0 errors)
- `.venv/bin/python -m pytest -q`: 800 passed, 0 failed, 0 skipped
  (includes 306 `mcp_governance` tests across 10 test modules)
- `tdd-gate-v3.sh`: PASSED with junitxml counters (716/0/0 at validation
  time, no facades, 11 integration test files)
- `doc-sync-check.sh`: PASSED — documented MCP governance endpoints,
  features, README, and CHANGELOG match the implementation
- MCP governance REST surface verified live over `httpx.ASGITransport` in
  `examples/mcp_governance.py` (registry, policies, budgets, audit,
  approvals, report, fail-closed auth)
