# Security Center 1.0

## Feature specifications

### Local Secret Scanner
As a security engineer, I want credentials redacted before dispatch so that an LLM provider never receives them. Given provider keys, bearer tokens, assignments, or private keys, when scanning runs, then values are replaced locally and only categories/counts remain. Architecture: compiled local patterns and immutable result. Complexity S. Monetization: Pro Security.

### Durable Replay Protection
As an integration owner, I want webhook IDs reserved atomically so duplicate delivery cannot repeat side effects. Given tenant, event ID and TTL, when first reserved then accepted; when repeated before expiry then duplicate; after expiry accepted again. Architecture: SQLite WAL unique composite key and TTL cleanup. Complexity M. Monetization: Pro Integrations.

### Provider Compliance Policy
As a compliance owner, I want routing blocked unless certifications, data promises and regions meet policy. Missing or unknown evidence denies with an explainable reason. Architecture: deterministic fail-closed policy service. Complexity M. Monetization: Enterprise Compliance.

### Change Risk Assessor
As an operator, I want sensitive production changes scored so dangerous changes require two approvers. Known categories produce bounded severity and approval count; unknown categories fail closed. Architecture: explicit weighted policy. Complexity S. Monetization: Enterprise Change Governance.

### Security Posture Score
As a CISO, I want a concise control score and remediation list so teams know what to fix next. Architecture: six high-value controls, deterministic grade and remediation. Complexity S. Monetization: free score, paid remediation workflows.

## Three-month roadmap

- Month 1 MVP: secret scanner, replay protection, fail-closed API and dashboard.
- Month 2: provider compliance catalog, change-risk approval integration and beta calibration.
- Month 3 GA: shared replay adapter, dependency attestations, posture trends and managed notifications.

Dependencies: authentication precedes every endpoint; compliance evaluation precedes routing; replay reservation precedes webhook processing; change risk feeds existing approval workflows.

## Validation plan

- Fake-door Security Center cards segmented by security, platform and compliance roles.
- Ten-customer beta, first in report-only mode, then enforcement after two weeks.
- Van Westendorp pricing interviews for Security and Compliance bundles.
- A/B secret warnings versus automatic redaction; measure prevented exposures and false-positive overrides.
- Replay success metrics: duplicate side effects, reservation latency and expired-key cleanup.
