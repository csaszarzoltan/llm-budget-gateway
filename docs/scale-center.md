# Scale Center 7.0

Scale Center converts the project's repeated multi-instance, shared-store, residency, and recovery guidance into ten deterministic controls. It is additive and does not change existing proxy or database contracts.

## Capabilities

1. Storage topology blocks unsafe multi-node SQLite deployments.
2. Replication quorum calculates majority writability and failure tolerance.
3. Partition planning sizes workload partitions and tenant distribution.
4. Consistency policy requires strong consistency for budgets and key lifecycle.
5. Failover planning selects the first healthy approved region.
6. Migration readiness requires backups, schema validation, rehearsal, targeted tests, full regression, and rollback.
7. Connection-pool planning reserves capacity and bounds per-node connections.
8. Tenant shard assignment uses a stable SHA-256 fingerprint instead of returning tenant plaintext.
9. Residency topology allows same-region storage or explicit region pairs only.
10. Disaster-recovery objectives evaluate backup age and restore duration against RPO and RTO.

## Run

```bash
export GATEWAY_SCALE_API_KEY='replace-with-a-strong-secret'
uvicorn llm_budget_gateway.scale_api:create_scale_app --factory --port 8015
```

All capability calls use `POST /v1/scale/{capability}`, bearer authentication, and `X-Tenant-Id`.

```bash
curl -s http://localhost:8015/v1/scale/storage-topology \
  -H 'Authorization: Bearer replace-with-a-strong-secret' \
  -H 'X-Tenant-Id: tenant-a' \
  -H 'Content-Type: application/json' \
  -d '{"nodes":3,"backend":"postgres","transactional":true}'
```
