# Operations API

Set `GATEWAY_OPERATIONS_API_KEY` to a strong secret, run `uvicorn llm_budget_gateway.operations_api:create_operations_app --factory --port 8003`, and open `/operations`. All `/v1/operations/*` requests require `Authorization: Bearer <key>` and `X-Tenant-Id`. Swagger UI is available at `/docs`.

Endpoints:
- `POST /v1/operations/prompts`
- `GET /v1/operations/prompts/{name}`
- `POST /v1/operations/prompts/{name}/assign`
- `POST /v1/operations/retry-decisions`
- `POST /v1/operations/quota-diagnostics`
- `POST /v1/operations/model-catalog/normalize`
- `POST /v1/operations/slo`
