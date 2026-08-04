# Resilience Center 1.1

## Specifications

1. Adaptive Concurrency: As an SRE, I want latency/error-aware limits so overload recovers. Given healthy telemetry, increase by one; given high error/latency, halve within bounds. Complexity S, Pro Reliability.
2. Dead-letter Replay: As an operator, I want failed work durably stored and replayed exactly once. Sensitive fields are removed; tenant isolation and duplicate replay are enforced. Complexity M, Pro Operations.
3. Maintenance Windows: As a release manager, I want deterministic weekly UTC windows so changes occur safely. Invalid calendar inputs fail closed. Complexity S, Core.
4. Config Doctor: As a platform engineer, I want unsafe production settings detected before startup. It checks auth, shared storage, timeout and webhook secret. Complexity S, Freemium.
5. Incident Timeline: As an incident commander, I want ordered events, duration, severity and counts so post-mortems are fast. Complexity S, Pro Reliability.

## Roadmap
Month 1: concurrency, config doctor, dead letters MVP. Month 2: maintenance and incident UI, beta. Month 3: Redis/Postgres adapters, notifications and GA. Dependencies: auth before APIs, dead letters before replay, telemetry before adaptive enforcement.

## Validation
Fake-door Reliability cards; ten-customer shadow beta; Van Westendorp survey; A/B fixed versus adaptive limits. Success: lower 429/5xx, p95 within target, no duplicate replay, faster diagnosis. Reject if latency overhead exceeds 2 ms or false remediation exceeds 5%.
