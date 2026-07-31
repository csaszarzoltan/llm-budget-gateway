# Security API

Set `GATEWAY_SECURITY_API_KEY`, run `uvicorn llm_budget_gateway.security_api:create_security_app --factory --port 8005`, open `/security`, and use `/docs` for OpenAPI. Every `/v1/security/*` route needs a bearer key and `X-Tenant-Id`.
