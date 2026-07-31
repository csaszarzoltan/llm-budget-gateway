# Resilience Center research

| Source type | Concrete requirements | Frequency |
|---|---|---:|
| Reddit/community | Easy migration, privacy, unified access, usable assets rather than overwhelming catalogs. | 4 |
| GitHub issues | Retry storms, swallowed cancellation, health-aware routing, served-model visibility, pricing metadata. | 5 |
| G2 reviews | Poor docs, missing/limited analytics, complexity, alert issues. | 5 |
| Hacker News | Gateways fail speed and scalability together; inbound auth is often incomplete; routing/observability leaks into app code. | 3 |
| Stack Overflow | 429 ambiguity, RPM/TPM differences, burst handling and queue complexity. | 4 |
| Competitor changelogs | Adaptive routing, session affinity, OTel, streaming reliability, multi-pod accuracy, organization analytics. | 6 |
| Trend reports | Pragmatic embedded governance, supervision, infrastructure rigor and standardized observability. | 4 |

## RICE
Adaptive concurrency 12.2; dead-letter replay 10.8; config doctor 8.1; incident timeline 7.2; maintenance windows 5.6. All five were implemented.
