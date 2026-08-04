## Features Done (this pass)
- OpenTelemetry Evidence Plane: Records gateway, model, agent, tool, policy, and budget spans in a portable vendor-neutral representation.
- OpenInference Semantic Export: Maps domain span kinds and cost/token metrics into OTLP-shaped trace documents.
- Tenant-Isolated Evidence Storage: Partitions every span by tenant, trace, and span identifier with idempotent SQLite writes.
- Privacy-Safe Telemetry: Redacts raw input, output, authorization, token, password, API-key, and secret fields before persistence.
- Deterministic Evidence Export: Produces stable OTLP-shaped JSON and canonical JSON Lines for offline ingestion.
- Evidence Console APIs: Adds local-only span ingestion and tenant-scoped trace export endpoints.
- Evidence Cockpit Entry: Adds a discoverable OpenTelemetry evidence destination to the React Advanced workspace.
## Sources
- research-findings.md items addressed: OpenTelemetry Evidence Plane, telemetry portability, OpenInference export, production observability without vendor lock-in
- CHANGELOG.md section this maps to: [13.6.0] - 2026-08-04
