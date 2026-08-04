# Resilience API
Set `GATEWAY_RESILIENCE_API_KEY`; run `uvicorn llm_budget_gateway.resilience_api:create_resilience_app --factory --port 8006`. Use bearer auth plus `X-Tenant-Id`. Dashboard: `/resilience`; OpenAPI: `/docs`.
