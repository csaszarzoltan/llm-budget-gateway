## Features Done (this pass)
- Logical Route Data Plane: OpenAI-compatible chat, completion, and embedding requests now execute published logical aliases against real serving models.
- Application-Key Authentication: One-time application keys authenticate directly on the gateway through constant-time stored hashes without provider secrets.
- Shared Persistent Routing Store: The console and gateway use the same configurable SQLite routing database so published changes apply without client redeployment.
- Live Monthly Budget Ledger: Successful provider cost is attributed to the actual serving model and used by future route decisions in the same calendar month.
- Runtime Provider Health Eligibility: Persisted model-health state removes unhealthy route targets before a provider call.
- Transient Status Failover: Logical routes retry ordered eligible models only for configured retry-safe statuses such as 429, 500, 502, 503, and 504.
- Gateway Decision Headers: Live responses expose route, version, actual serving model, decision ID, and fallback reason.
- Live Usage View: The React Usage screen shows per-route model spend, budget, percentage used, and remaining headroom.
- Live Activity View: The React Activity screen shows real explainable route decisions and provider fallback reasons.
- Logical Alias Discovery: Published route names are included by the gateway models endpoint.
## Sources
- research-findings.md items addressed: runtime enforcement, budget fallback, provider failover, cost attribution, actionable incident UX, unified live graphical cockpit
- CHANGELOG.md section this maps to: [11.1.0] - 2026-08-04
