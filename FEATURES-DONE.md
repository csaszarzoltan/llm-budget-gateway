## Features Done (this pass)
- Live Trace Run API: Tenant-scoped run summaries expose span count, duration, and attributed cost without leaking other tenants.
- Production Trace Explorer Flow: The React cockpit loads real trace summaries from the backend with tenant input, loading, empty, result, and friendly error states.
- Trace Deep Links: Each run summary links to its nested trace evidence endpoint for agent, model, and tool investigation.
- Privacy-Safe Observability UX: The trace UI explicitly excludes prompt and response content while preserving operational evidence.
## Sources
- research-findings.md items addressed: End-to-end agent trace explorer, modern live graphical cockpit, actionable incident UX
- CHANGELOG.md section this maps to: [10.1.0] - 2026-08-04
