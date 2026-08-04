# Product Adoption Center 9.0

Product Adoption Center operationalizes the validation plans repeated throughout the specifications. Ten deterministic controls cover activation funnels, cohort retention, feature adoption, stable experiment assignment, experiment outcomes, bounded feedback themes, pricing signals, staged rollout cohorts, success thresholds, and integrity-protected adoption reports.

Run with `GATEWAY_ADOPTION_API_KEY=... uvicorn llm_budget_gateway.adoption_api:create_adoption_app --factory --port 8017`. Calls use bearer authentication and `X-Tenant-Id`. The center stores no comments or user identifiers and is additive.
