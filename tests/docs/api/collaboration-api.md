# Collaboration API
Set `GATEWAY_COLLABORATION_API_KEY`; run `uvicorn llm_budget_gateway.collaboration_api:create_collaboration_app --factory --port 8008`. Use bearer auth and `X-Tenant-Id`; invitation acceptance uses the single-use token. Dashboard `/collaboration`; OpenAPI `/docs`.
