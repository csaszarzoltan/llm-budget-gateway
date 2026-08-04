## Features Done (this pass)
- Iteration 1 — Application Key Lifecycle: One-time key rotation automatically revokes prior active keys and explicit revocation is supported.
- Iteration 2 — Budget Headroom: Per-scope limits, spend accumulation, reset day, remaining amount, and percentage used are persisted.
- Iteration 3 — Alert Rules: Cost, error-rate, latency, and fallback-rate thresholds are configurable and listable.
- Iteration 4 — Environment Registry: Multiple gateway environments with validated base URLs and one default environment are supported.
- Iteration 5 — Saved Role Views: Developer, operator, FinOps, and security filters can be stored as reusable views.
- Iteration 6 — Provider Verification: Health checks record availability, latency, time, and create actionable audit evidence.
- Iteration 7 — Route Snapshots and Rollback: Versioned route payloads can be snapshotted and restored safely.
- Iteration 8 — Soft Archival: Routes and other product resources can be archived without destructive deletion.
- Iteration 9 — Portable Configuration: Non-secret environments, alerts, budgets, and views can be exported and validated imports restore supported settings.
- Iteration 10 — Recommendations and Audit: Budget pressure and provider outages generate recommendations; sensitive lifecycle actions are recorded in an audit stream.
- Extension API Surface: All ten iterations are exposed through FastAPI endpoints and linked from Advanced tools.
## Sources
- research-findings.md items addressed: governed keys, scoped budget control, proactive alerts, multi-environment operations, health-aware routing, version safety, auditability, portable self-hosted configuration
- CHANGELOG.md section this maps to: [13.0.0] - 2026-08-04
