## Features Done (this pass)
- Named Provider Accounts: The same provider type can be registered multiple times under unique human-readable names and slugs such as `openai-prod` and `openai-dev`.
- One Credential per Connection: A named provider connection stores one provider-specific credential set and reuses it for every discovered model.
- Provider-Adaptive Setup Wizard: OpenAI, Anthropic, Gemini, Azure OpenAI, OpenAI-compatible and Vertex AI connections expose only their relevant fields.
- Encrypted Provider Secrets: Provider credentials are AES-256-GCM encrypted at rest with a dedicated local master key and are never returned by list or detail APIs.
- Provider-Native Model Discovery: OpenAI-compatible, OpenAI, Anthropic, Gemini and Azure model-list response formats are normalized into one catalog.
- Automatic Credential Verification: Saving and model synchronization are separate safe steps with actionable authentication and connection errors.
- Alias-Prefixed Model IDs: Discovered models are addressed as `@provider-slug/model-id`, so multiple accounts of the same provider remain unambiguous.
- Persistent Cockpit Provider Store: Cockpit-first startup persists encrypted provider connections, discovered models and the master key under `.gateway-console/`.
- Route Model Picker Integration: New routes select from models actually discovered from connected provider accounts instead of manually typed or hard-coded models.
- Provider Catalog UX: Connection cards show alias, provider type, region, health, masked credential state, model count, last sync, errors and expandable model lists.
## Sources
- research-findings.md items addressed: centralized provider credentials, governed model catalog, low-friction provider onboarding, secure local-first operation, model discovery
- CHANGELOG.md section this maps to: [13.2.0] - 2026-08-04
