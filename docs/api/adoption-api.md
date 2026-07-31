# Product Adoption API

Capabilities: `activation-funnel`, `cohort-retention`, `feature-adoption`, `experiment-assignment`, `experiment-outcome`, `feedback-themes`, `pricing-signal`, `rollout-cohort`, `success-threshold`, and `adoption-report`.

Configure `GATEWAY_ADOPTION_API_KEY`. Errors: 401 authentication/tenant, 404 unknown capability, 422 invalid input, and fail-closed 503 without the server key. `GET /health` is liveness.
