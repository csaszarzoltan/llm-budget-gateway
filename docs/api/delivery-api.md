# Delivery API

Set `GATEWAY_DELIVERY_API_KEY` and start `llm_budget_gateway.delivery_api:create_delivery_app` with `--factory`.

Capabilities are `environment-readiness`, `configuration-drift`, `capacity-plan`, `dependency-health`, `rollout-plan`, `rollback-decision`, `observability-coverage`, `alert-routes`, `runbook-coverage`, and `release-manifest`.

Protected calls require bearer authentication and `X-Tenant-Id`. Errors are 401 for authentication or tenant failures, 404 for unknown capabilities, 422 for invalid input, and fail-closed 503 when the server key is absent. `GET /health` is the liveness endpoint.
