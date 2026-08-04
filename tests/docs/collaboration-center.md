# Collaboration Center 1.3

## Specifications
1. Project RBAC: As an admin, I want owner/admin/developer/viewer permissions and project scopes so contractors see only assigned projects. Invalid roles fail closed. Complexity S, Enterprise.
2. One-time Invitations: As a team admin, I want expiring single-use tokens so onboarding is safe. Only token hashes are stored. Complexity M, Pro Teams.
3. Key Lifecycle: As security, I want rotate/revoke/keep guidance from age and inactivity so stale credentials disappear. Complexity S, Pro Security.
4. Member Budgets: As FinOps, I want request-spend and active-key caps per member so one script cannot drain the organization. Complexity S, Pro Teams.
5. Delegated Approvals: As governance owner, I want time-limited delegation while preventing self-approval so workflows continue during absence. Complexity S, Enterprise Governance.

## Roadmap
Month 1 MVP: RBAC, invitations, member budgets. Month 2: key lifecycle and delegated approvals beta. Month 3: SSO/SCIM adapter, shared Postgres store, notifications, GA. Dependencies: tenant auth before mutations; role policy before invites; member identity before budgets; delegation before approval execution.

## Validation
Fake-door team controls; ten-customer beta; Van Westendorp interviews; A/B guided invite flow versus manual setup. Confirm with faster activation, fewer shared keys, lower unowned spend, and zero privilege leaks. Reject if invite completion drops over 10% or false denials exceed 2%.
