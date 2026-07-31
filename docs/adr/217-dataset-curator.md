# ADR 217: dataset-curator

Status: Accepted, 2026-07-31.

Decision: implement the capability as a deterministic typed domain service, exposed through the tenant-authenticated Platform API. Domain code has no transport dependency and fails closed on invalid input.

Consequences: offline tests are reproducible and existing APIs remain compatible. Persistence and external providers require future adapters.
