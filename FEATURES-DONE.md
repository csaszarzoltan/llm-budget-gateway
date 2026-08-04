## Features Done (this pass)
- Verified SQLite Backup: Creates consistent database backups bound to release identifiers, size evidence, and SHA-256 digests.
- Tamper-Safe Recovery: Runs SQLite integrity checks and refuses restoration when backup evidence no longer matches.
- Fail-Closed Canary Planner: Requires provenance, backup, migration, regression, and bounded traffic evidence before rollout.
- Canary Promote-or-Rollback Gate: Uses measured error, p95 latency, and quality guardrails to choose promotion or rollback.
- Outcome-Aware Autopilot: Recommends only measured lower-cost candidates that preserve quality, success, and latency floors.
- Approval and Rollback Boundary: Never mutates production automatically and attaches explicit approval and rollback guidance.
- Release and Autopilot Console APIs: Adds local-only rollout planning, canary decision, and optimization recommendation endpoints.
- Production Cockpit Navigation: Adds discoverable Safe releases and Optimization autopilot entries to the React Advanced workspace.
## Sources
- research-findings.md items addressed: Safe Upgrade and Recovery Channel, Outcome-Aware Autopilot, upgrade rollback under ten minutes, bounded quality-safe cost optimization
- CHANGELOG.md section this maps to: [13.7.0] - 2026-08-04
