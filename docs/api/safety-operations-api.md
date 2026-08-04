# Safety Operations API

Safety evidence endpoints are restricted to clients on the local machine.

## Runaway Cost Firewall

`POST /v1/console/runaway/evaluate` makes a fail-closed pre-step decision across cost, tokens, tool calls, delegation depth, elapsed time, retries, and emergency-stop state. The React Safety workspace invokes this API and displays the explanation and next action.

## Measured Provider Compatibility Lab

`POST /v1/console/compatibility/{provider_id}/run` loads the named encrypted provider connection and executes non-destructive HTTP checks for authentication, model discovery, chat, streaming, tools, structured output, and embeddings. It records measured latency and redacted failure evidence. Provider-specific unsupported capabilities fail visibly rather than being reported as successful.

`GET /v1/console/compatibility/{provider_id}/history?limit=20` returns the newest 1-100 measured or imported checks.

`POST /v1/console/compatibility/evaluate` is retained for importing externally measured offline probe evidence. It does not claim to execute provider traffic.

## Explain-and-Fix from real request evidence

`GET /v1/console/incidents/from-request/{request_id}` reads an actual product routing decision and creates a timeline for the request, route/model choice, provider outcome, latency, reason, and recorded cost. It returns `why`, `impact`, and a concrete next action.

`POST /v1/console/incidents/events` remains available for importing evidence from external systems. `GET /v1/console/incidents/{incident_id}` retrieves a previously built or imported timeline. Secret-bearing keys and common bearer, OpenAI-style, AWS access-key, and JWT patterns are redacted before persistence.
