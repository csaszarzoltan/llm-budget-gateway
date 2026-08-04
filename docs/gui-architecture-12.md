# GUI architecture 12.0

## Product principle

The interface exposes user jobs before platform internals. A user should identify gateway status, application connectivity, active routes, current serving behavior, and required attention within ten seconds.

## Primary navigation

- Home: gateway status, endpoint, activation, attention, KPIs, routes, decisions.
- Applications: stable route assignment and one-time gateway keys.
- Routes: templates, model chains, draft versions, testing, publication.
- Providers: credentials, health, region, governed models and capabilities.
- Activity: compact explainable decisions; traces are secondary evidence.
- Usage: requests, latency, success and cost by route and serving model.
- Advanced: services, traces, policies, security, supply chain and API explorer.

## Role views

Developer emphasizes integration, Operator attention, FinOps usage, and Security policy. The underlying object model and navigation remain shared.

## API

- `GET /v1/product/home?role=developer|operator|finops|security`
- `GET|POST /v1/product/applications`
- `GET|POST /v1/product/providers`
- `GET|POST /v1/product/routes`
- `PUT /v1/product/routes/{route_id}`
- `POST /v1/product/routes/{route_id}/test`
- `POST /v1/product/routes/{route_id}/publish`
- `GET /v1/product/templates`
- `GET /v1/product/activity`
- `GET /v1/product/usage`
