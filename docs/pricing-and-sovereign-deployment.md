# Pricing and Sovereign Deployment Guidance

## Recommended editions

- **Community:** no-cost local gateway, BYOK, routing, budgets, compatibility checks, seven-day local evidence, OpenTelemetry export, and signed community releases.
- **Team:** target USD/CHF 49 per control plane per month for managed updates, shared dashboards, alerts, backups, replay packs, and support. No percentage of upstream model spend.
- **Business:** target USD/CHF 249 per month for SSO/OIDC, RBAC, longer retention, policy packs, environment promotion, and priority support.
- **Enterprise:** annual contract for private/VPC or air-gapped deployment, Postgres/HA, SCIM, regional residency, compliance evidence, migration support, and SLA.

These are commercial recommendations from `research-findings.md`, not currently enforced entitlements.

## Sovereign and air-gapped operation

1. Mirror the pinned Python and npm dependencies into an approved internal registry.
2. Build the cockpit and release ZIP in a controlled CI runner.
3. Verify the ZIP hash, SBOM, and provenance before transfer.
4. Keep provider keys and the generated provider master key outside source and release archives.
5. Bind services to loopback or an approved private interface and terminate identity at the trusted reverse proxy.
6. Send OpenTelemetry evidence only to an approved in-region collector.
7. Create and verify a backup before every migration or upgrade.
8. Rehearse restore and rollback in the target environment before production promotion.

The current SQLite mode is single-node. HA and system-wide multi-tenancy require a supported Postgres deployment and must not be inferred from the local edition.
