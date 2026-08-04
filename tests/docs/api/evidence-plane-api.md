# OpenTelemetry Evidence Plane API 13.6

The evidence plane gives teams a portable, vendor-neutral trace representation while keeping the cockpit as the action layer. Endpoints are local-only.

## Record a span

`POST /v1/console/evidence/spans` accepts tenant, 32-character trace ID, 16-character span ID, optional parent span ID, domain kind, name, nanosecond timing, status, safe attributes, and numeric metrics. It returns the persisted redacted event.

Supported domain kinds include `gateway`, `model`, `agent`, `tool`, `policy`, and `budget`. They map to OpenInference span kinds in exports.

## Export a trace

`GET /v1/console/evidence/traces/{trace_id}?tenant_id={tenant_id}` returns an OTLP-shaped `resourceSpans` document. A trace from another tenant returns 404, not cross-tenant evidence.

## Privacy and reliability

Prompt/output values, authorization, API keys, tokens, passwords, and secret-like nested attributes are redacted before persistence. Writes are idempotent by tenant, trace, and span. JSON serialization and trace ordering are deterministic.
