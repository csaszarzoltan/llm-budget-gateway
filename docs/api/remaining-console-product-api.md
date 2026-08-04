# Console and Product Extension API

The following existing endpoints are part of the supported local control-plane API:

- `GET /v1/console/services`: list managed local services.
- `POST /v1/console/services/start-all`: start every configured service.
- `POST /v1/console/services/stop-all`: stop processes owned by the console.
- `GET /v1/product/alerts`: list configured product alerts.
- `GET /v1/product/audit`: list privacy-safe control-plane change events.
- `GET /v1/product/environments`: list deployment environments.
- `GET /v1/product/export`: export non-secret product configuration.
- `POST /v1/product/import`: import validated non-secret configuration.
- `GET /v1/product/recommendations`: list evidence-based product recommendations.
- `GET /v1/product/views`: list saved product views.

Service-management mutations require the local console action boundary. Product endpoints inherit the console's local deployment boundary and never export stored provider credentials.
