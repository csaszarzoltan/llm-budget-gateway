# Governance and automation suite

`GovernanceService` stores tenant memberships, recommendations, evidence, privacy policies, governed records and audit activities in additive SQLite tables. Admins manage memberships and approve recommendations; auditors export deterministic SHA-256 evidence packages; privacy officers configure retention and allowed regions; operators create bounded recommendations and recovery decisions. Prompt, secret and authorization fields are removed before persistence. Unknown region, insufficient permission and invalid inputs fail closed. Automation remains human-approved and rollback creates an auditable activity. Community deployments can use SQLite on one node; distributed production deployments should provide a transactional repository adapter.

Recovery uses the existing object identifier, not a new request. Tests use a fake clock and temporary database and make no external calls.
