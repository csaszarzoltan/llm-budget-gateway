## Features Done (this pass)
- Executed Production Replay: Runs an explicit bounded candidate request through the fixed local gateway and compares measured output, tokens, latency, and cost.
- Replay Cost Preflight: Shows estimated cost and requires an explicit user action before any candidate provider call.
- Replay SSRF Boundary: Restricts replay execution to the loopback gateway and redacts upstream error detail.
- Compatibility Contract Ingestion: Writes live non-destructive provider probe results into per-model regional contracts.
- Fail-Closed Contract Eligibility: Rejects missing, stale, unsupported, wrong-region, or required-unpriced models and returns route-health status.
- Interactive Operations Workspaces: Replaces dead release, optimization, and evidence links with accessible React forms and result states.
- Clean Release Enforcement: Builds a cockpit-inclusive archive while excluding keys, databases, WAL/SHM files, logs, environments, caches, and dependencies.
- API Documentation Completion: Documents previously unlisted console service and product extension endpoints.
- Pricing and Sovereign Guidance: Adds transparent edition recommendations and an air-gapped deployment checklist without falsely claiming entitlement enforcement.
## Sources
- research-findings.md items addressed: Production Replay and Change Impact Lab, Verified Provider and Model Contract Catalog, modern task-first workflows, transparent pricing, sovereign deployment
- CHANGELOG.md section this maps to: [13.8.0] - 2026-08-04
