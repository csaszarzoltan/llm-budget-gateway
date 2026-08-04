# Priority Features API 9.5

The Unified Console hosts the researched P0 controls. Run it with:

```bash
uv run uvicorn llm_budget_gateway.console_api:create_console_app --factory --host 127.0.0.1 --port 8013
```

## Routes

### `POST /v1/console/cockpit/summary`

Accepts `spend`, `quality`, `operations`, and `governance` objects. Returns a normalized platform status, metric cards, and severity-ranked actions. Invalid, non-finite, negative, or out-of-range values return HTTP 422.

### `POST /v1/console/runaway/evaluate`

Accepts `state` and `limits`. State fields are `run_id`, `cost_usd`, `tokens`, `tool_calls`, `depth`, `elapsed_seconds`, `retries`, and optional `emergency_stop`. Limit fields use the corresponding `max_` prefix. The response contains `allowed`, stable `code`, `explanation`, and `next_action`.

The decision is fail-closed at the exact boundary: consumption equal to a ceiling is blocked before the next step.

### `POST /v1/console/forms/generate`

Accepts `form_id` and a JSON Schema with an object root. Returns presentation metadata for text, number, select, checkbox, list, and JSON controls. Secret-like field names are marked sensitive and explicitly non-persistent. Unsupported roots return HTTP 422.

### `GET /cockpit`

Serves the production Vite/React cockpit after `npm run build` in `ui/`. The page provides a complete guided firewall evaluation flow and links back to the expert console.

## Trace and outcome routes 9.6

### `POST /v1/console/traces`

Appends one span with `span_id`, `run_id`, `tenant_id`, optional `parent_span_id`, `kind`, `name`, start/end milliseconds, cost, status, and bounded metadata. Parents must already exist in the same tenant and run. Prompt, response, authorization, secret, and API-key metadata keys are removed before SQLite persistence. Success returns HTTP 201.

### `GET /v1/console/traces/{run_id}`

Requires `X-Tenant-Id` and returns the run as nested `children` arrays with duration and cost. Missing tenant headers return 422. Unknown or cross-tenant runs return 404.

### `POST /v1/console/outcomes/summary`

Accepts outcome records carrying feature, project, model, tool, non-negative cost, quality from zero to one, and success. Returns total cost, successful outcomes, cost per success, quality-weighted cost, and grouped breakdowns.

## Supply-chain routes 9.7

### `GET /v1/console/supply-chain/sbom`

Reads the repository's exactly pinned `pyproject.toml` and optional npm lockfile, then returns a deterministic CycloneDX-compatible inventory. Unpinned Python runtime dependencies fail closed with HTTP 422.

### `POST /v1/console/supply-chain/upgrade-risk`

Accepts `current`, `proposed`, and optional `security_advisories` package maps. Changes are labeled `added`, `removed`, `unpinned`, `major`, `minor`, `patch`, or `security-major`. High-risk changes require approval and return a blocking recommendation.

### Library provenance surface

`ProvenanceService.create()` emits an in-toto statement with an SLSA provenance predicate. `verify()` compares the local artifact against the recorded SHA-256 digest and returns false after tampering or when the artifact is absent.
