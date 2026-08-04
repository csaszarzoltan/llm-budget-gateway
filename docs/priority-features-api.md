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
