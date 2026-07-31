# Platform Center 2.0

## Twenty feature specifications

1. **Prompt Catalog**: version prompts across environments. As an AI engineer, I want semantic versions and environment labels so production prompts are identifiable. Given a name, SemVer, and environments, when registered, then normalized unique environments are returned. S, Core.
2. **Model Catalog**: register context, capabilities, and internal/external classification. As platform engineering, I want model metadata so routing understands trust boundaries. Invalid context or empty capabilities fail closed. S, Core.
3. **Usage Tags**: normalize bounded business metadata. As FinOps, I want consistent tags so spend is attributable. Keys and values are length and character constrained. S, Core.
4. **Cost Allocation**: proportionally distribute realized cost. As a budget owner, I want exact chargeback so teams own spend. Negative/non-finite values fail. S, Pro FinOps.
5. **Quota Planner**: calculate request and token headroom. As an SRE, I want both dimensions checked so large requests cannot bypass request-only controls. S, Pro Reliability.
6. **Alert Rules**: evaluate explicit threshold operators. As an operator, I want deterministic alerts so incidents are actionable. Unsupported operators fail closed. S, Core.
7. **SLO Calculator**: calculate availability and remaining error budget. As an SRE, I want release decisions tied to error budgets. S, Pro Reliability.
8. **Incident Digest**: summarize duration, kinds, and criticality. As incident command, I want a concise post-mortem seed. S, Pro Reliability.
9. **Retention Policy**: calculate expiry or legal hold. As compliance, I want deterministic retention so data is not held indefinitely. S, Enterprise Compliance.
10. **DLP Classifier**: detect email, provider keys, and card-like values without returning secrets. As security, I want local blocking before provider dispatch. S, Pro Security.
11. **Region Router**: select the lowest-latency healthy provider in allowed regions. As compliance, I want residency enforcement before egress. M, Enterprise Compliance.
12. **Provider Scorecard**: combine cost, latency, quality, and reliability. As procurement, I want comparable provider evidence. S, Pro FinOps.
13. **Canary Planner**: validate strictly increasing rollout stages ending at 100%. As release management, I want staged rollout so blast radius is bounded. S, Pro Releases.
14. **Rollback Guardrail**: rollback on quality, error, or latency regression. As release management, I want automatic stop criteria. S, Pro Releases.
15. **Feedback Aggregator**: summarize ratings and positive share. As product management, I want explicit user signal connected to AI quality. S, Core.
16. **Quality Drift Detector**: compare current and baseline means. As QA, I want drift alerts before trust erodes. S, Pro Quality.
17. **Dataset Curator**: remove duplicate canonical examples. As evaluation engineering, I want clean datasets so results are not biased by duplicates. S, Pro Quality.
18. **Export Manifest**: hash every file and aggregate manifest. As audit, I want portable integrity evidence. S, Enterprise Compliance.
19. **Contract Compatibility**: detect removed required fields. As API owners, I want additive changes so clients do not break. S, Core.
20. **Adoption Funnel**: calculate sequential conversions. As product management, I want onboarding drop-off visibility. S, Pro Analytics.

## Architecture and data flow

`platform_suite.py` contains deterministic domain services with no FastAPI dependency. `platform_api.py` is an authenticated adapter that translates JSON requests into domain calls and stable HTTP errors. The browser dashboard is presentation-only and never contains credentials. Dependency direction is UI/API to domain. Existing gateway APIs remain unchanged.

## Three-month roadmap

- **Month 1 MVP**: catalogs, tags, allocation, quotas, alerts, SLOs, DLP, contract checks. Design week 1, implementation weeks 2-3, tests/docs/release week 4.
- **Month 2 beta**: incidents, retention, region routing, scorecards, feedback, drift, curation. Add feature flags and ten design partners.
- **Month 3 GA**: canary, rollback, manifests, adoption analytics, shared persistence adapters, notifications, operational runbooks.

Dependencies: catalogs precede routing; tags precede allocation; quality metrics precede rollback; manifests precede portable audit; authenticated tenancy precedes every capability.

## Validation plan

- Fake-door cards for Catalog, FinOps, Quality, Releases, Compliance, and Adoption.
- Ten-customer early access with report-only policy decisions for two weeks.
- Van Westendorp interviews for Pro FinOps, Pro Quality, Pro Releases, and Enterprise Compliance.
- A/B test guided platform setup versus documentation-only setup.
- Confirm value with first-capability activation above 45%, weekly reuse above 35%, at least 10% cost-attribution improvement, and reduced release rollback time.
- Reject or redesign a feature if false policy blocks exceed 2%, dashboard completion drops 10%, or local median overhead exceeds 2 ms.
