## Features Done (this pass)
- Deterministic CycloneDX SBOM: Pinned Python and npm dependencies are normalized into a stable, auditable component inventory with package URLs.
- SLSA-Style Provenance: Release artifacts receive SHA-256-bound in-toto provenance statements with builder and source identity.
- Offline Artifact Verification: Local release bytes are verified against provenance and tampering is detected without a network dependency.
- Dependency Upgrade Risk Gate: Major, minor, patch, removed, unpinned, and advisory-affected changes are classified before rollout.
- Supply-Chain APIs: The Unified Console exposes SBOM generation and upgrade-risk assessment with fail-closed validation.
- Cockpit Supply-Chain Navigation: The React cockpit surfaces SBOM, provenance, and upgrade-risk controls alongside operational features.
## Sources
- research-findings.md items addressed: P1 Security supply-chain center, signed reproducible secure distribution
- CHANGELOG.md section this maps to: [9.7.0] - 2026-08-04
