# Feature specifications, roadmap, and validation

## 1. Local PII Guard
Description: deterministic local redaction for emails, phone numbers, and payment-card-like values.

User story: As a security administrator, I want sensitive values removed before provider dispatch so that data exposure is reduced.

Acceptance: Given text containing supported PII, when redaction runs, then values are replaced, categories and counts are returned, and original text is not persisted.

Technical solution: compiled regular expressions, immutable result, API endpoint. Complexity: S. Monetization: Pro security pack.

## 2. Tenant-isolated Exact Response Cache
Description: stable request-key caching with TTL and tenant isolation.

User story: As a platform engineer, I want duplicate requests served from cache so that latency and provider cost fall.

Acceptance: Given the same canonical request in one tenant, when it is looked up before expiry, then the stored response is returned; other tenants and expired records miss.

Technical solution: SHA-256 canonical JSON key and SQLite TTL table. Complexity: M. Monetization: usage-based cache tier.

## 3. Signed Alert Webhooks
Description: HMAC-SHA256 envelopes for trusted downstream automation.

User story: As an operator, I want to verify alert authenticity so that forged events cannot trigger automation.

Acceptance: Given a secret and payload, when signed then verification succeeds; after tampering it fails in constant time.

Technical solution: canonical JSON, timestamped HMAC envelope. Complexity: S. Monetization: Pro integrations.

## 4. Explainable Cost Anomalies
Description: identify cost spikes using z-score and ratio thresholds with explanations.

User story: As a FinOps owner, I want anomalous periods flagged with baseline metrics so that I can investigate early.

Acceptance: Given two or more non-negative historical values, when current usage materially exceeds the baseline, then anomaly, mean, z-score, ratio, and explanation are returned.

Technical solution: Python statistics library; deterministic, no ML service. Complexity: S. Monetization: FinOps tier.

## 5. Cost-aware Model Router
Description: pick the cheapest healthy model meeting quality and latency policy.

User story: As an AI platform owner, I want constrained cost optimization so that savings never bypass reliability requirements.

Acceptance: Given candidate models, when policy is evaluated, then unhealthy/ineligible models are excluded and the cheapest eligible model is selected deterministically.

Technical solution: pure policy engine over model telemetry. Complexity: M. Monetization: percentage-of-savings or Enterprise routing tier.

## Three-month roadmap

- Month 1 MVP: local PII guard, exact cache, signed webhooks; security review and beta SDK examples.
- Month 2: anomaly dashboards and cost-aware routing shadow mode; telemetry calibration and tenant-level rollout.
- Month 3 full release: policy presets, cache analytics, webhook delivery worker, enterprise packaging, GA migration guides.

Dependencies: auth and tenant headers precede every endpoint; routing depends on model telemetry; alert delivery builds on signed envelopes. Semantic caching and distributed stores remain future work.

## Validation plan

- Fake door: add Intelligence cards to the dashboard and measure click-through by role.
- Beta: 10 design partners, two weeks shadow-only, weekly interview and structured incident diary.
- Willingness to pay: Van Westendorp survey for Security, FinOps, and Routing bundles.
- A/B: cache enabled versus disabled for eligible exact requests; primary metrics are cost/request and p95 latency, guardrails are error rate and cache correctness.
- Release gates: no cross-tenant cache hit, no raw PII persistence, webhook tamper tests, routing quality floor, and less than 2 ms median local-policy overhead.
