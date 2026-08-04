# Enterprise control suite

`EnterprisePlatform` uses additive SQLite tables and an injectable clock. Approval requests are idempotent, expire, reject self-approval and count distinct approvers. Evidence exports exclude prompt, secret and authorization fields and include a deterministic SHA-256 integrity value. SCIM users are tenant scoped and deactivated users fail authorization. Model selection exposes every score and its weighting explanation. Privacy cases support export/delete semantics and legal-hold blocking. Tool policies fail closed, enforce cost ceilings and require explicit approval before completion.

The module does not change existing public routes. A production multi-instance deployment should substitute a transactional shared repository. Recovery reuses the original idempotency key or object identifier. Tests are network independent and use temporary databases and a fake clock.
