# Platform API

Set `GATEWAY_PLATFORM_API_KEY`, run `uvicorn llm_budget_gateway.platform_api:create_platform_app --factory --port 8009`, and open `/platform` or `/docs`. Every `/v1/platform/{capability}` call requires `Authorization: Bearer <key>` and `X-Tenant-Id`. Missing configuration returns 503, bad authentication 401, unknown capabilities 404, and invalid payloads 422.
