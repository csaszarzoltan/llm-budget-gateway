# Intelligence API
Run `uvicorn llm_budget_gateway.market_api:create_market_app --factory --port 8002`. All `/v1/intelligence/*` routes require `X-Tenant-Id`. Interactive OpenAPI is available at `/docs`.

Endpoints: `POST /redact`, `POST /cache`, `POST /cache/lookup`, `POST /webhooks/sign`, `POST /anomalies`, and `POST /route`. The dashboard is `GET /intelligence`.
