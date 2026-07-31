# AgentOps API

Set `GATEWAY_AGENTOPS_API_KEY`, run `uvicorn llm_budget_gateway.agentops_api:create_agentops_app --factory --port 8010`, and open `/agentops` or `/docs`. Every capability requires bearer authentication and `X-Tenant-Id`. Errors: 401 authentication, 404 capability, 422 payload, 503 missing configuration.
