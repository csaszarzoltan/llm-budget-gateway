# Quality API

Set `GATEWAY_EVALUATION_API_KEY`, then run `uvicorn llm_budget_gateway.evaluation_api:create_evaluation_app --factory --port 8004`. Protected routes require `Authorization: Bearer <key>` and `X-Tenant-Id`. OpenAPI is at `/docs`; the responsive dashboard is `/quality`.

Routes: evaluations create/list, release gates, trace resolution, batch manifest planning, audit report creation and verification.
