# Gateway Quality Suite 0.9

## Deterministic Evaluation Runs
Description: rule-based offline quality evaluation with immutable tenant-scoped run history.

User story: As an AI engineer, I want deterministic checks before release so that obvious regressions never reach production.

Acceptance criteria: Given exact, containment, forbidden-pattern or length rules, when an output is evaluated, then each check, aggregate score and pass state are returned; empty rule sets and invalid limits fail closed.

Technical solution: pure Python evaluator plus SQLite run history. Complexity S. Monetization: Pro Quality tier.

## Release Quality Gates
Description: block deployment when average quality or regression tolerance is violated.

User story: As a release manager, I want explicit quality thresholds so that model or prompt changes cannot silently reduce performance.

Acceptance criteria: Given normalized scores and thresholds, when evaluated, then mean, regression, reason and pass state are deterministic; invalid scores fail closed.

Technical solution: stateless domain policy callable from CI. Complexity S. Monetization: Pro CI integration.

## Privacy-safe Trace Context
Description: resolve validated gateway and vendor session identifiers without storing prompt content.

User story: As an operator, I want requests correlated across providers so that failures can be debugged end to end.

Acceptance criteria: gateway trace ID has priority, then gateway session ID, then vendor session headers; malformed or missing identifiers fail closed.

Technical solution: allow-listed identifier syntax and documented precedence. Complexity S. Monetization: Observability tier.

## Batch Manifest Planner
Description: validate single-model asynchronous batches and estimate discounted spend.

User story: As a FinOps-aware engineer, I want batch cost prepared before submission so that offline workloads remain predictable.

Acceptance criteria: custom IDs are unique, costs non-negative, one model is used, discount is bounded, and estimated discounted cost is returned.

Technical solution: provider-independent manifest validator. Complexity S. Monetization: usage optimization tier.

## Integrity-protected Audit Reports
Description: create schema-versioned, redacted reports with SHA-256 integrity verification.

User story: As an auditor, I want portable evidence whose integrity can be verified so that reviews do not depend on mutable dashboards.

Acceptance criteria: prompt, authorization and secret fields are removed; embedded key patterns are redacted; schema and hash verify; tampering fails verification.

Technical solution: canonical JSON and SHA-256. Complexity M. Monetization: Enterprise Compliance.

## Three-month roadmap

- Month 1 MVP: deterministic evaluations, release gates, tenant run history and CI examples.
- Month 2: trace propagation, batch planning, quality dashboard and design-partner calibration.
- Month 3 GA: audit exports, signed release attestations, experiment comparison and managed notifications.

## Validation plan

- Fake-door cards for “Quality Gates” and “Audit Evidence,” segmented by engineer and compliance roles.
- Ten-customer beta with shadow-only release decisions for two weeks.
- Van Westendorp pricing survey for Quality, CI and Compliance bundles.
- A/B test guided evaluation setup versus docs-only setup; measure first run, second run and gate adoption.
- Compare gated versus ungated releases on rollback rate, quality regression, time to diagnosis and evaluation cost.
