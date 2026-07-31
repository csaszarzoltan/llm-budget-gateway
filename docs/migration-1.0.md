# Migration to 1.0.0

This release is additive. Configure `GATEWAY_SECURITY_API_KEY`; protected routes otherwise return 503. Run on port 8005. Back up `security.db`. Multi-instance deployments must replace SQLite replay storage with a shared transactional adapter.
