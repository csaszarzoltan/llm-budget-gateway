# Route Studio

Route Studio is the task-first editor for stable route aliases. An application calls `@route/<name>` while operators evolve an isolated draft and publish only after validation.

## Inventory

The Routes page supports search, status filtering, archived-route filtering, strategy and version summaries, health warnings, and contextual actions. Frequent actions are separated from destructive lifecycle controls.

## Visual editor

The editor uses a deterministic vertical flow:

1. **Start** accepts the OpenAI-compatible request.
2. **Model targets** are evaluated in priority order.
3. Red fallback edges explain the next attempt.
4. A green success edge returns the response.
5. **End** terminates every valid path.

The inspector configures model, retry count, timeout, schedule, timezone, capabilities, fallback HTTP status codes, condition metadata, weighted split and per-request cost ceilings. Pointer dragging is not required: Move up and Move down provide a keyboard-accessible equivalent.

## Lifecycle

- `draft`: editable and isolated from live traffic.
- `active`: the current draft has been published.
- `archived`: unavailable for new traffic but recoverable.
- permanent deletion: only accepted for an archived route after exact route-name confirmation.

Applications using a route are blocking dependencies. They must be reassigned before archival.

## API

- `GET /v1/product/routes`
- `POST /v1/product/routes`
- `PUT /v1/product/routes/{route_id}`
- `POST /v1/product/routes/{route_id}/publish`
- `POST /v1/product/routes/{route_id}/validate`
- `POST /v1/product/routes/{route_id}/simulate`
- `GET /v1/product/routes/{route_id}/versions`
- `GET /v1/product/routes/{route_id}/dependencies`
- `POST /v1/product/routes/{route_id}/duplicate`
- `POST /v1/product/routes/{route_id}/archive`
- `POST /v1/product/routes/{route_id}/restore`
- `DELETE /v1/product/routes/{route_id}`

Simulation never calls a provider. It returns the selected model, eligibility path and `provider_call_made: false`.
