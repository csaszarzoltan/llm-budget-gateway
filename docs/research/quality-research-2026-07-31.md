# Quality Suite multi-source research

Research date: 2026-07-31. Frequency is the count of distinct concrete signals, not a population estimate.

| Source type | Specific pain points or missing features | Frequency |
|---|---|---:|
| Reddit / community | “Evaluations is something everyone seems to be struggling with”; users “lose good [prompt] versions all the time”; startups prioritize speed, cost and simpler tools over heavy enterprise platforms. | 3 |
| GitHub issues and roadmaps | Gateway projects add evaluation/experimentation, raw usage, prompt-caching tests, UI E2E and audit-detail fixes; users need stable request/session correlation and reproducible quality workflows. | 5 |
| G2 reviews | Poor documentation, missing features, limited advanced analytics, complexity for newcomers, and alert issues recur in verified review summaries. | 5 |
| Hacker News / launches | Provider schemas are not genuinely uniform; retries, routing and observability leak into app code; production trust requires complete inbound auth and transparent behavior. | 3 |
| Stack Overflow | Users cannot reliably distinguish financial quota from throttling; rate-limit headers can be unavailable or `-1`; batch and asynchronous workloads require clear preflight validation and correlation. | 3 |
| Competitor changelogs | Competitors ship organization analytics, custom catalogs, guardrails, compliance filters, detailed usage breakdowns and tracked agent traffic. | 6 |
| Technology/practice reports | Modern LLM operations emphasize span-level traces, evaluation workflows, regression detection, tag-based cost attribution and offline testing before production. | 5 |

## RICE prioritization

| Feature | Reach | Impact | Confidence | Effort | RICE |
|---|---:|---:|---:|---:|---:|
| Deterministic evaluation rules | 9 | 3 | .95 | 2 | 12.83 |
| Release quality gates | 9 | 3 | .90 | 2 | 12.15 |
| Trace/session propagation | 8 | 2 | .90 | 2 | 7.20 |
| Batch manifest planning | 7 | 2 | .85 | 2 | 5.95 |
| Signed audit evidence | 7 | 3 | .90 | 3 | 6.30 |
| LLM-as-judge service | 8 | 3 | .65 | 8 | 1.95 |

The first five were selected. A hosted LLM judge was deferred because it introduces nondeterminism, provider cost, calibration requirements and possible prompt retention.
