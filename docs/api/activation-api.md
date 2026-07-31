# Activation API

Capabilities: `setup-progress`, `environment-template`, `provider-credentials`, `port-plan`, `configuration-doctor`, `first-request`, `budget-starter`, `service-profile`, `diagnostic-bundle`, and `activation-gate`.

Configure `GATEWAY_ACTIVATION_API_KEY`. Errors are 401 for authentication/tenant failures, 404 for unknown capabilities, 422 for invalid input, and fail-closed 503 when the server key is absent. `GET /health` is public liveness.
