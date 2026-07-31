# ADR: 019-change-risk

Status: Accepted, 2026-07-31.

Decision: implement a deterministic, typed, fail-closed local security control behind tenant-authenticated FastAPI. No prompt or secret value is persisted.

Consequences: offline testability and incremental deployment; SQLite remains a documented single-node boundary.
