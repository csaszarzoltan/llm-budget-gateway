# Autonomous development iterations 13.0

This release contains ten independently tested increments.

1. Key lifecycle prevents long-lived application credentials.
2. Budget headroom makes remaining spend explicit at any scope.
3. Alert rules convert thresholds into operational policy.
4. Environment registry separates development and production endpoints.
5. Saved views reduce repeated filtering for each role.
6. Provider verification adds current health and latency evidence.
7. Route snapshots enable deliberate rollback.
8. Soft archival preserves history and avoids destructive deletion.
9. Export/import supports self-hosted portability without exporting secrets.
10. Recommendations turn budget and health evidence into next actions, while audit records lifecycle changes.

All imported bundles require schema `gateway-console/v1`. Export never includes plaintext application or provider keys.
