# ADR: 014-batch-manifests

Status: Accepted, 2026-07-31.

Decision: implement this quality capability as deterministic typed Python behind tenant-authenticated FastAPI endpoints. Avoid provider calls and prompt retention in the MVP.

Consequences: tests and CI are reproducible; SQLite remains the single-node persistence boundary; distributed storage requires an adapter.
