# AgentOps Center 3.0

## Feature specifications

1. MCP Server Registry: HTTPS server cards and tool discovery. User story: platform engineers register trusted MCP servers so agents use known tools. Given valid metadata, return normalized tools. M, Enterprise.
2. Tool Access Policy: explicit allow/deny decision. Security owners constrain tools so least agency is enforced. Deny wins. S, Enterprise.
3. Delegation Depth: bound recursive agent calls. SREs prevent runaway graphs. Given maximum depth, block the next step at the boundary. S, Pro.
4. Task Lease: owner/expiry semantics for asynchronous work. Workers safely resume tasks after expiry. M, Pro.
5. Replay Protection: timestamped HMAC verification. Integrators reject stale or modified callbacks. M, Enterprise.
6. Session Affinity: deterministic backend selection. Operators keep stateful sessions stable across healthy pools. S, Pro.
7. Circuit Breaker: closed/open/half-open policy. SREs isolate failing providers and allow controlled probes. S, Pro.
8. Semantic Cache Key: normalized privacy-preserving hash. Engineers reuse equivalent requests without storing plaintext keys. S, Pro.
9. Sensitive Data Redaction: local e-mail and key redaction. Security teams prevent accidental sensitive egress. S, Core.
10. Injection Risk: indicator-based review score. Reviewers triage untrusted content before execution. S, Core.
11. Human Approval Gate: impact-based approval requirement. Governance owners retain control over consequential actions. S, Enterprise.
12. Audit Chain: tamper-evident event linkage. Auditors verify chronology. S, Enterprise.
13. Trace Sampling: deterministic sampling with incident override. SREs control telemetry cost without losing incidents. S, Pro.
14. Task Cost Meter: token cost and cost per step. FinOps measures task unit economics. S, Pro.
15. Token Density: useful units per thousand tokens. Product teams identify waste. S, Pro.
16. Carbon Estimator: energy and grid-intensity estimate. Sustainability teams quantify operational emissions. S, Add-on.
17. Change Risk: scope, criticality, and coverage score. Release managers route review effort. S, Pro.
18. Support Triage: severity, impact, and workaround priority. Support teams respond consistently. S, Core.
19. Locale Negotiation: exact and language-prefix matching. Global teams deliver supported locale UX. S, Core.
20. Residency Policy: same-region or explicit pair authorization. Compliance blocks unauthorized data movement. S, Enterprise.

All services are typed, deterministic domain objects in `agentops_suite.py`; `agentops_api.py` is the tenant-authenticated transport adapter. Existing APIs remain additive and compatible.

## Roadmap

Month 1 MVP: registry, access, depth, leases, replay, circuit breaker, redaction, approval. Month 2 beta: affinity, cache, injection, audit, tracing, cost and density. Month 3 GA: carbon, change risk, support, locale, residency, shared stores, notifications. Each feature passes design, implementation, tests, docs, beta, and release milestones.

## Validation

Fake-door capability cards; ten-customer report-only beta; willingness-to-pay interviews for Pro and Enterprise bundles; A/B guided setup; analytics for activation, repeat use, cost per completed task, false blocks, incident recovery. Confirm at 40% activation, 30% weekly reuse, 10% task-cost improvement, and under 2% false blocks. Reject or redesign otherwise.
