# Safety Operations API

Research-ranked P0 workflows are served by the unified console app.

## Provider Compatibility Lab

`POST /v1/console/compatibility/evaluate` accepts a `provider_id` and a non-empty list of unique capability probes. Each probe contains `capability`, `passed`, `latency_ms`, and optional `detail`. The response reports `ready`, `degraded`, or `blocked`, a 0-100 score, evidence, and ordered repair actions. Invalid or duplicate probes return HTTP 422.

## Runaway Cost Firewall

`POST /v1/console/runaway/evaluate` evaluates cost, token, tool-call, delegation-depth, elapsed-time, retry, and emergency-stop ceilings before the next agent step. It returns an explainable allow/block decision and next action.

## Explain-and-Fix Incident Timeline

`POST /v1/console/incidents/events` appends one evidence event and returns HTTP 201. Secret-bearing fields and key-like values are redacted before SQLite persistence.

`GET /v1/console/incidents/{incident_id}` returns chronological evidence, status, impact, root explanation, and a concrete fix. Unknown incidents return HTTP 404.
