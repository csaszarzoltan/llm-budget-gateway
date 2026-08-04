# Logical Routing API 11.0

## Applications

- `POST /v1/admin/applications` accepts `name` and `default_route`; HTTP 201 returns the gateway key once.
- `GET /v1/admin/applications` lists applications without API-key material.

## Routes

- `POST /v1/admin/routes` creates validated draft version 1.
- `GET /v1/admin/routes` lists routes and draft/published state.
- `GET /v1/admin/routes/{route_id}` returns one route.
- `PUT /v1/admin/routes/{route_id}` creates a new immutable draft version.
- `POST /v1/admin/routes/{route_id}/simulate` evaluates a timestamp, quality tier, estimated cost, model spend, health, region, and capabilities without a provider call.
- `POST /v1/admin/routes/{route_id}/publish` atomically activates the current draft.
- `POST /v1/admin/routes/{route_id}/rollback` restores the immediately previous published version.
- `GET /v1/admin/routes/{route_id}/activity` returns newest-first explainable decisions.

## Route configuration

Required fields are `name`, `default_model`, ordered `fallback_models`, positive `monthly_budget`, IANA `timezone`, weekday/time `schedule`, `quality_models`, retry-safe `fallback_statuses`, positive `max_cost_per_request`, `required_region`, and `required_capabilities`.

Fallback statuses intentionally exclude permanent client errors such as HTTP 400. Supported transient statuses are 408, 409, 425, 429, 500, 502, 503, and 504.

## Decision response

Simulation returns the selected model, fallback reason, ordered decision path, route version, decision ID, and the exact response-header contract expected from the data plane.

## Live data-plane execution 11.1

The gateway and console share `GATEWAY_ROUTING_DATABASE_URL`. Application keys created through the admin API authenticate directly on `/v1/chat/completions`, `/v1/completions`, and `/v1/embeddings`. A published logical route in the request `model` field is resolved before provider forwarding.

Optional request `metadata` supports `quality_tier`, `region`, and `max_cost_usd`. Tool and structured-output capabilities are inferred from standard OpenAI request fields. Runtime fallback proceeds only for the route's retry-safe status list.

Live responses include:

- `X-Gateway-Route`
- `X-Gateway-Route-Version`
- `X-Gateway-Serving-Model`
- `X-Gateway-Decision-Id`
- `X-Gateway-Fallback`

### `GET /v1/admin/routes/{route_id}/usage`

Returns the current calendar month's actual serving spend, budget, remaining headroom, and percentage used for every model configured in the route.
