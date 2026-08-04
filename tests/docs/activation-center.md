# Activation Center 8.0

Activation Center turns the documented setup journey into ten deterministic, privacy-safe controls: setup progress, redacted environment templates, provider-credential presence, port planning, configuration diagnosis, first-request generation, starter budgets, persona service profiles, diagnostic manifests, and a final fail-closed activation gate.

Run `GATEWAY_ACTIVATION_API_KEY=... uvicorn llm_budget_gateway.activation_api:create_activation_app --factory --port 8016`. Calls use `POST /v1/activation/{capability}`, bearer authentication, and `X-Tenant-Id`.

The feature never returns credential values. It is additive and does not change existing gateway or center routes.
