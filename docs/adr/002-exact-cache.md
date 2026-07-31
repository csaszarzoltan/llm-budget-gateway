# ADR: 002-exact-cache

Status: Accepted, 2026-07-31.

Decision: implement this capability as a typed, deterministic local service behind tenant-authenticated FastAPI endpoints.

Consequences: no new remote dependency or prompt retention; SQLite remains single-node and a repository adapter is required for distributed deployment.
