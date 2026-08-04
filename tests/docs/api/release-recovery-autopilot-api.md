# Release Recovery and Autopilot API 13.7

## Release plan

`POST /v1/console/releases/plan` requires verified provenance, verified backup, migration readiness, complete regression evidence, and a canary percentage from 1 to 50. Any missing gate blocks rollout.

## Canary decision

`POST /v1/console/releases/canary-decision` compares measured error rate, p95 latency, and quality against configured guardrails. Any breach returns `rollback`; only a fully healthy canary returns `promote`.

## Outcome-aware optimization

`POST /v1/console/autopilot/recommend` filters measured candidates by minimum quality, minimum success rate, and maximum latency. It recommends the cheapest qualifying improvement with estimated savings, approval requirement, and rollback guidance. The endpoint never mutates a route.

## Backup domain service

`ReleaseRecoveryService` creates consistent SQLite backups, verifies SHA-256, size, and `PRAGMA integrity_check`, and performs a verified atomic restore. Unsafe release identifiers and tampered artifacts are rejected.
