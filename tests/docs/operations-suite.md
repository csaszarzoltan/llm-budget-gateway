# Gateway Operations Suite 0.8

## Feature specifications

### Immutable Prompt Registry
One sentence: Store tenant-isolated immutable prompt versions and assign experiment subjects deterministically.

User story: As an AI product engineer, I want prompt versions and stable A/B assignment so that improvements can be measured and good versions are never lost.

Acceptance criteria:
- Given a tenant, name and non-empty template, when a version is created, then the next immutable integer version is returned.
- Given metadata containing secret fields, when it is stored, then those fields are removed.
- Given an experiment and subject, when assignment is called repeatedly, then the same existing version is returned.
- Given a different tenant, when versions are listed, then no cross-tenant data is returned.

Technical solution: SQLite composite key `(tenant, name, version)`, SHA-256 deterministic assignment, authenticated FastAPI endpoints. Complexity M. Monetization: Pro prompt management add-on.

### Bounded Retry Safety
One sentence: Calculate full-jitter retries with strict attempt, elapsed-time and delay ceilings.

User story: As an SRE, I want retry decisions bounded so that transient provider failures cannot become retry storms.

Acceptance criteria:
- Given a transient status below all ceilings, when evaluated, then a bounded jitter delay is returned.
- Given attempt or elapsed limits, when evaluated, then retry is denied.
- Given a non-transient response, when evaluated, then retry is denied.

Technical solution: deterministic seeded full jitter and an explicit transient status allow-list. Complexity S. Monetization: core reliability feature.

### Actionable Quota Diagnostics
One sentence: Distinguish financial quota, token throughput, request throughput, provider availability and request errors.

User story: As an operator, I want ambiguous provider errors classified so that clients take the correct recovery action.

Acceptance criteria: Given a 429 and provider code/message, when classified, then a stable category, action and explanation are returned; invalid HTTP status types fail closed.

Technical solution: normalized provider-code classifier. Complexity S. Monetization: Pro observability.

### Rich Model Catalog
One sentence: Validate and normalize model pricing, context, capability and region metadata.

User story: As a platform owner, I want trustworthy catalog metadata so that routing, billing and compliance use the same facts.

Acceptance criteria: unique IDs, non-negative prices, positive context windows, deduplicated capabilities/regions and deterministic sorting.

Technical solution: typed validation service exposed through OpenAPI. Complexity S. Monetization: Enterprise custom catalog.

### SLO and Error-Budget Monitor
One sentence: Calculate availability, allowed failures, burn rate and operational state.

User story: As an SRE, I want error-budget burn reported so that reliability work is prioritized before an SLO breach.

Acceptance criteria: valid request counts produce availability and burn; invalid counts and targets fail closed; state is healthy, warning or critical.

Technical solution: stateless arithmetic domain service. Complexity S. Monetization: Pro reliability analytics.

## Three-month roadmap

- Month 1 MVP: prompt registry, retry policy and quota diagnostics; authenticated APIs; design-partner onboarding.
- Month 2: custom model catalog import, SLO dashboards, alert integrations and shadow-mode retry recommendations.
- Month 3 GA: prompt comparison UI, experiment outcome ingestion, provider-specific quota adapters and managed SLO notifications.

Dependencies: authentication precedes all mutations; model catalog feeds routing and price validation; SLO alerts depend on trustworthy request counters. Distributed traces remain a later repository-adapter project.

## Validation plan

- Landing-page fake door for “Prompt Experiments” and “Reliability SLOs,” segmented by developer, FinOps and SRE role.
- Ten-customer beta with retry decisions in shadow mode and weekly incident reviews.
- Van Westendorp willingness-to-pay survey for Prompt Management, Reliability and Enterprise Catalog bundles.
- A/B test prompt-registry onboarding versus documentation-only setup; measure activation, retained versions and first experiment completion.
- Reliability A/B test bounded retry policy versus current client defaults; guardrails are success rate, request amplification, p95 latency and provider cost.
