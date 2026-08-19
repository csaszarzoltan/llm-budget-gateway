# Contributing to LLM Budget Gateway

Welcome — and thanks for your interest in making LLM Budget Gateway better.

This project is a local-first, OpenAI-compatible AI gateway with budget enforcement, logical routing, provider discovery, trace analytics, governance, and a React product cockpit. Contributions are welcome as issues, discussions, or pull requests.

## Prerequisites

- **Python 3.11+**
- **uv** — package manager (replaces pip/poetry)
- **Node.js 20+ and npm** — for cockpit UI changes

## Quick Start

### Clone and install

```bash
git clone https://github.com/csaszarzoltan/llm-budget-gateway.git
cd llm-budget-gateway
uv sync --extra dev --frozen
```

### Run tests

```bash
uv run pytest -q
```

### Run linter

```bash
uv run ruff check src tests examples
```

### Rebuild cockpit (UI contributors only)

```bash
cd ui
npm ci
npm test
npm run build
```

## Development Workflow

### 1. Fork and branch

Create a feature branch from `main`:

```bash
git checkout -b feature/your-change
```

Use descriptive branch names: `feature/budget-alerts`, `fix/proxy-timeout`, `docs/api-reference`.

### 2. Make changes

Keep changes focused — one logical change per PR. Source lives under `src/llm_budget_gateway/`.

### 3. Write tests

Every new behaviour gets a test first (TDD). Tests live in `tests/test_<module>.py` and mirror the source module name.

### 4. Run quality gates

Before submitting, all three gates must pass clean:

```bash
uv run ruff check src tests examples       # linter
uv run pytest -q                           # tests
cd ui && npm test && npm run build && cd .. # cockpit (if UI changed)
```

### 5. Submit PR

Push your branch and open a pull request against `main`. Use the PR template below.

## Code Conventions

### Python style

The project uses [Ruff](https://docs.astral.sh/ruff/) for linting and formatting. Configuration from `pyproject.toml`:

```toml
[tool.ruff]
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "UP", "B", "SIM", "I"]
ignore = ["E501", "SIM105"]
```

Formatting is Black-compatible. Run `uv run ruff format src tests` to auto-format.

### Docstrings

Follow the agent-friendly docstring conventions in `docs/engineering-standards.md` (METH-COD-001 through 008):
- Every non-trivial function includes a rationale docstring (purpose + approach + rejected alternatives).
- Module-level summaries describe role, entry points, and dependencies.
- Document edge cases and error returns in the docstring.

### Testing conventions

- **Framework:** pytest with `asyncio_mode = "strict"` (declared in `pyproject.toml`)
- **File naming:** `tests/test_<module>.py` — mirrors the source module
- **Markers:** `@pytest.mark.unit`, `@pytest.mark.integration`, `@pytest.mark.slow`
- **Fixtures:** prefer `pytest.fixture` and `pytest-mock` over manual setup
- **Async tests:** use `@pytest.mark.asyncio` — the strict mode requires explicit marking
- **Coverage:** `uv run pytest --cov=llm_budget_gateway` for local coverage reports

### Commit messages

Use imperative mood, scoped format:

```
<type>(<scope>): <short summary>

<body — why, not just what>
```

Types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`. Scope is the affected module (e.g. `cost-tracking`, `gateway-proxy`, `ui`).

### PR description template

```markdown
## What

<1-2 sentence summary of the change>

## Why

<Context — link to issue or explain the motivation>

## How

<Key implementation decisions, if non-obvious>

## Checklist

- [ ] Tests pass (`uv run pytest -q`)
- [ ] Linter clean (`uv run ruff check src tests examples`)
- [ ] No regressions in existing tests
- [ ] New behaviour has test coverage
- [ ] Docstrings follow project conventions
- [ ] Cockpit builds clean (if UI changed: `cd ui && npm test && npm run build`)
```

## Project Structure

Top modules under `src/llm_budget_gateway/`:

| Module | Purpose |
|--------|---------|
| `main.py` | Gateway proxy FastAPI app — OpenAI-compatible endpoint routing |
| `console_api.py` | Product console API — ~129 cockpit/product endpoints |
| `control_api.py` | Admin control API — dashboard, keys, budgets, CSV export |
| `gateway_proxy.py` | Core proxy logic — auth, scope resolution, forwarding, streaming |
| `config.py` | Pydantic Settings — all `GATEWAY_*` env var configuration |
| `control_plane.py` | Key/budget/alert/policy management, SQLite persistence |
| `routing_control_plane.py` | Application routes, versions, publish/rollback, activity |
| `budget_enforcement.py` | TPM/RPM rate limiting, soft/hard budget enforcement |
| `cost_tracking.py` | Per-request cost recording, SQLite ledger, spend queries |
| `cost_estimation.py` | Preflight cost estimation before sending requests |
| `provider_connections.py` | Encrypted API key storage, provider model discovery |
| `dispatch_engine.py` | Request dispatching to upstream LLM providers via LiteLLM |
| `fallback_manager` | Fallback chains, cooldowns, error classification |
| `evidence_plane.py` | OpenTelemetry spans, OTLP export, trace recording |
| `system_launcher.py` | Full-system launcher — starts proxy + cockpit + services |

For the complete architecture, see `docs/ARCHITECTURE.md` and `docs/index.md`.

## Quality Gates

### ruff check

```bash
uv run ruff check src tests examples
```

Must exit 0 with no warnings. Ruff targets Python 3.11+ and enforces the rules listed above.

### pytest

```bash
uv run pytest -q
```

Must exit 0. The suite uses `asyncio_mode = "strict"` — async tests must be explicitly marked.

### npm test + build (UI changes only)

```bash
cd ui && npm test && npm run build
```

Both the test suite and production build must pass. The release packaging step (`scripts/build_release.py`) requires `ui/dist` to exist.

## Getting Help

- **Issues** — report bugs or request features on GitHub Issues
- **Discussions** — architectural questions, ideas, and RFCs in GitHub Discussions
- **Docs** — `docs/` covers routing, budgets, alerts, providers, and more
- **Engineering standards** — read `docs/engineering-standards.md` before your first PR
