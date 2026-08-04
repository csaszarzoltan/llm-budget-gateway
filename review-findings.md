# Review Remediation Findings: 13.8.0

The independent 13.7.0 review was **REJECTED**. This fix pass addresses its release-blocking findings.

## Resolved blockers

- **Missing cockpit:** `ui/dist` is rebuilt before the release and the clean release builder fails closed if it is absent.
- **Secrets/runtime state in distribution:** the final release is produced only by `scripts/build_release.py`; archive-level checks verify that keys, databases, WAL/SHM files, logs, caches, environments, and dependency directories are absent.
- **Replay facade:** `/v1/console/replay/run` executes a real bounded candidate call through the fixed loopback gateway. The UI requires a candidate model, prior output, privacy-safe prompt, token limit and visible estimated-cost preflight.
- **Disconnected compatibility contracts:** measured live compatibility probes are persisted into per-model regional contracts and exposed through fail-closed eligibility scoring.
- **Dead UI links:** safe-release, outcome-optimization and evidence lookup are cockpit forms with result and error states rather than links to POST-only endpoints.
- **API documentation gaps:** all ten paths listed by the reviewer are now documented.
- **Commercial guidance gaps:** transparent pricing recommendations and sovereign deployment guidance are included without claiming implemented licensing or HA.

## Explicit remaining architecture boundary

SQLite remains the supported local/single-node store and MCP governance remains single-tenant. The documentation explicitly prevents representing the local edition as Postgres HA or system-wide multi-tenant. Implementing and certifying a distributed Postgres/SCIM/HA enterprise edition is a separate deployment program, not a safe patch to this local product.

## Remediation verdict

**APPROVED WITH ARCHITECTURE NOTE** when the exact clean release archive passes extraction, full Python tests, frontend tests/build, lint, secret scan, and cockpit smoke test. The architecture note is the explicit single-node/local-edition boundary above.
