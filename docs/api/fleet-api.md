# Fleet Governance API

Set `GATEWAY_FLEET_API_KEY`, run `uvicorn llm_budget_gateway.fleet_api:create_fleet_app --factory --port 8011`, and open `/fleet` or `/docs`. Every `/v1/fleet/{capability}` request requires bearer authentication and `X-Tenant-Id`. Errors: 401, 404, 422, and fail-closed 503.
