# Market research and feature decision (2026-07-31)

## Decision
The selected feature is an authenticated **preflight cost-estimation API**. Existing gateway functionality is broad, but operators and application developers still lack a safe way to answer “what could this request cost?” before dispatch. This complements, rather than duplicates, post-request ledgers, budgets, routing, and alerts.

## Evidence summary
Current competitors emphasize real-time per-request cost tracking, hierarchical budgets, alerts, semantic caching, and cost-aware routing. LiteLLM documents budgets across users, teams, keys and tags, while Portkey and Helicone emphasize request cost analytics and alerts. The competitive gap for this repository is therefore not more passive reporting, but a developer-facing pre-dispatch decision primitive that can power UI warnings, approval checks, simulations, and client-side model selection.

## Alternatives considered
1. Semantic caching: high potential savings, but it introduces prompt retention/privacy, embedding infrastructure, invalidation, and correctness risks.
2. Distributed Redis/Postgres adapters: important for scale, but primarily operator infrastructure rather than an immediately visible user benefit.
3. More dashboards: valuable, but the repository already has six control-center workspaces.
4. Preflight cost estimation: small integration surface, no provider call, no prompt persistence, immediately useful to developers, FinOps, and approval workflows.

## Acceptance criteria
Authenticated OpenAI-shaped request body; no upstream call; same price map as billing; explicit unknown-pricing state; deterministic validation; unit and full regression tests; user and repository documentation.
