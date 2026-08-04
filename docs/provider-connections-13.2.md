# Provider connections and model discovery 13.2

## UX structure

The provider setup uses four staged steps:

1. Choose a provider type from recognizable account-level integrations.
2. Name the connection, define a unique slug and enter provider-specific connection fields.
3. Save the encrypted credential and explain the resulting model namespace.
4. Verify the account and download the provider's model catalog.

This separates account identity from model selection. A user can register the same provider several times for production, development, region or billing-account separation.

## Security model

Provider fields declared as secrets are stored in one AES-256-GCM encrypted payload per connection. A 256-bit local master key is created with restrictive permissions. Public APIs expose only connection metadata, masked credential status and discovered model metadata.

## Discovery

- OpenAI and OpenAI-compatible: bearer-authenticated `GET {base_url}/models`.
- Anthropic: `x-api-key` plus Anthropic version header at `GET {base_url}/models`.
- Gemini: API-key query authentication at `GET {base_url}/models`.
- Azure OpenAI: resource endpoint, API key and API version.
- Vertex AI: provider-adaptive project, location and service-account fields; automatic discovery remains explicit until OAuth service-account exchange is configured.

Normalized models receive a stable gateway identifier: `@provider-slug/model-id`.

## API

- `GET /v1/product/provider-types`
- `GET|POST /v1/product/provider-connections`
- `POST /v1/product/provider-connections/{provider_id}/sync-models`
- `GET /v1/product/provider-connections/{provider_id}/models`
- `GET /v1/product/discovered-models`
