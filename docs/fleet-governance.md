# Fleet Governance 4.0

## Twenty-one implemented specifications

1. Agent Identity Card: accountable owner, purpose, expiry, fingerprint. As security, I want unique non-human identities so actions are attributable. Valid IDs produce stable fingerprints. M, Enterprise.
2. Agent Inventory: sanctioned, shadow, and status summaries. As IT, I want one inventory so unknown agents are visible. S, Enterprise.
3. Lifecycle Policy: draft, active, suspended, retired transitions. As governance, I want controlled activation and offboarding. Invalid transitions fail closed. S, Enterprise.
4. Credential Expiry: valid, renew, revoke decisions. As IAM, I want short-lived credentials. S, Enterprise.
5. Capability Grant: capability, resource, and expiry scope. As security, I want least privilege. M, Enterprise.
6. Platform Authorization: verify approved external platform and terms version. As legal, I want platform permission before autonomous use. S, Enterprise.
7. Kill Switch: organization, team, and agent emergency stops. As incident command, I want immediate containment. S, Pro.
8. Policy Simulation: compare current and proposed decisions. As policy owners, I want report-only rollout. S, Pro.
9. Blast Radius: users, writable systems, and autonomy score. As risk owners, I want prioritization before deployment. S, Pro.
10. Human Responsibility: resolve explicit accountable person. As compliance, I want clear human ownership. S, Core.
11. Evidence Bundle: canonical artifact hashes. As audit, I want portable evidence. S, Enterprise.
12. Policy Coverage: governance and observability ratios. As leadership, I want fleet-wide control metrics. S, Pro.
13. Shadow Agent Detection: compare observed and registered identities. As security, I want unknown-agent alerts. S, Enterprise.
14. Cost Ceiling: projected workflow cost decision. As FinOps, I want pre-execution budget enforcement. S, Pro.
15. Runaway Detection: fan-out, retry, and tool-thrash indicators. As SRE, I want loops stopped early. S, Pro.
16. Outcome Economics: cost per completed outcome and ROI. As product leadership, I want business-unit economics. S, Pro.
17. Model Tier Policy: cheapest model meeting complexity. As FinOps, I want controlled downsizing. S, Pro.
18. Tool Cost Ledger: per-tool and total chargeback. As finance, I want the second-largest cost line visible. S, Pro.
19. Data Readiness: freshness, permissioning, and classification. As data governance, I want trusted context before agent access. S, Enterprise.
20. Reproducibility Record: prompt, model, tools, policy fingerprint. As QA, I want production runs reproducible. S, Pro.
21. Compliance Crosswalk: map controls to framework requirements. As compliance, I want explicit missing-control evidence. S, Enterprise.

Architecture: `fleet_suite.py` contains transport-independent typed domain services. `fleet_api.py` authenticates tenant requests and translates predictable input errors to HTTP 422. Existing 3.0 APIs remain unchanged.

## Roadmap

Month 1 MVP: identity, inventory, lifecycle, credentials, grants, kill switch, simulation. Month 2 beta: platform authorization, blast radius, responsibility, evidence, coverage, shadows, cost ceilings. Month 3 GA: runaway, economics, model tiers, tool costs, data readiness, reproducibility, compliance. Every milestone includes design, implementation, tests, docs, beta, and release.

## Validation

Fake-door cards; ten-customer report-only beta; pricing interviews for Enterprise Identity and FinOps bundles; guided-setup A/B test; measure identity coverage, shadow-agent reduction, policy false blocks, cost per outcome, and incident containment. Confirm with 80% identity coverage, 50% shadow reduction, 10% cost improvement, and under 2% false blocks.
