# Assurance API
Set `GATEWAY_ASSURANCE_API_KEY`, run `uvicorn llm_budget_gateway.assurance_api:create_assurance_app --factory --port 8012`, and open `/assurance` or `/docs`. Every capability requires bearer authentication and `X-Tenant-Id`. Errors are 401, 404, 422, and fail-closed 503.
