# ADR: 008-quota-diagnostics

Status: Accepted, 2026-07-31.

Context: market research identified this as a repeated production requirement.

Decision: implement a typed deterministic domain service behind an authenticated FastAPI endpoint, with fail-closed validation and no remote dependency.

Consequences: the capability is testable offline and deployable incrementally. SQLite-backed prompt versions remain single-node until a shared repository adapter is selected.
