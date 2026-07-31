# FAQ
**Does the PII guard store prompts?** No. It returns redacted text and metadata only.

**Is caching semantic?** No. Version 0.7 uses exact canonical JSON matching to avoid semantic correctness and privacy risks.

**Are webhooks delivered automatically?** The release creates verifiable envelopes; delivery remains the caller's responsibility.

**Can this run on multiple nodes?** The domain API can, but SQLite cache consistency is single-node. Use a shared repository adapter for clustered deployments.


**Why does the Operations API return 503?** `GATEWAY_OPERATIONS_API_KEY` is not configured; this fails closed intentionally.

**Can prompt versions be edited?** No. Create a new immutable version so experiments remain reproducible.

**Does quota diagnostic retry requests?** No. It returns an action recommendation; the bounded retry endpoint makes the separate retry decision.


**Why does the Quality API return 503?** `GATEWAY_EVALUATION_API_KEY` is missing, so the service fails closed.

**Does evaluation call another model?** No. Version 0.9 uses deterministic local rules for reproducible CI.

**Can audit reports contain prompts or keys?** Prompt and credential fields are removed and embedded gateway/provider keys are redacted.


**Why 503?** `GATEWAY_SECURITY_API_KEY` is missing. **Are secrets stored?** No. **Is SQLite multi-instance?** Use a shared adapter for clusters.

**Why 503?** Configure `GATEWAY_RESILIENCE_API_KEY`.

**Why 503?** Configure `GATEWAY_OPTIMIZATION_API_KEY`.

**Why 503?** Configure `GATEWAY_COLLABORATION_API_KEY`.


**Why does Platform API return 503?** Configure `GATEWAY_PLATFORM_API_KEY`. **Does Platform Center replace existing APIs?** No, version 2.0 is additive. **Are DLP values logged?** No, only finding categories are returned.

**Why AgentOps 503?** Configure `GATEWAY_AGENTOPS_API_KEY`. **Does 3.0 replace existing APIs?** No, it is additive.
