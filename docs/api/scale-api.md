# Scale API

Configure `GATEWAY_SCALE_API_KEY`, then run `uvicorn llm_budget_gateway.scale_api:create_scale_app --factory --port 8015`.

Capabilities: `storage-topology`, `replication-quorum`, `partition-plan`, `consistency-policy`, `failover-plan`, `migration-readiness`, `connection-pool`, `tenant-shard`, `residency-topology`, and `disaster-recovery`.

Protected routes require `Authorization: Bearer <key>` and `X-Tenant-Id`. Errors are 401 for authentication/tenant failures, 404 for an unknown capability, 422 for invalid input, and fail-closed 503 when the server key is not configured. `GET /health` is the liveness endpoint.
