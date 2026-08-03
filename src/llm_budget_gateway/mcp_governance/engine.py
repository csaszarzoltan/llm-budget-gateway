"""Policy engine gating MCP tool calls before/after execution.

Normative per docs/architecture/mcp-governance.md §6.6. CallContext is a
fully functional dataclass; MCPPolicyEngine.before_call / after_call are
implemented here.
"""

import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import jsonschema

from llm_budget_gateway.budget_enforcement import (
    BudgetExceededError,
    BudgetScope,
)

from .exceptions import (
    AccessDeniedError,
    ApprovalRequiredError,
    InvalidArgumentsError,
    PolicyViolationError,
)
from .policy import PolicyEvaluator
from .rules import ApprovalGate, args_hash_of
from .schemas import AuditEvent

if TYPE_CHECKING:
    from collections.abc import Callable

    from .audit import AuditStore
    from .budgets import ToolBudgetService
    from .integration import ApprovalNotifier
    from .policy import ToolPolicyStore
    from .registry import MCPRegistry
    from .rules import ApprovalStore, PIIRedactor, SSRFGuard


@dataclass
class CallContext:
    """Everything after_call needs to record the outcome of a gated call."""

    call_id: str
    request_id: str | None
    caller: str
    scopes: list["BudgetScope"]
    server_id: str
    tool_name: str
    args_redacted: dict[str, Any]
    decision: str  # "allowed" | "approved"
    policy_id: str | None
    approval_id: str | None
    reason: str | None



class MCPPolicyEngine:
    """Gate a tool call BEFORE execution, record the outcome AFTER."""

    def __init__(
        self,
        *,
        registry: "MCPRegistry",
        policies: "ToolPolicyStore",
        budgets: "ToolBudgetService",
        audit: "AuditStore",
        approvals: "ApprovalStore",
        redactor: "PIIRedactor",
        ssrf: "SSRFGuard",
        notifier: "ApprovalNotifier | None" = None,
        request_id_factory: "Callable[[], str] | None" = None,
    ) -> None:
        self._registry = registry
        self._policies = policies
        self._budgets = budgets
        self._audit = audit
        self._approvals = approvals
        self._redactor = redactor
        self._ssrf = ssrf
        self._notifier = notifier
        self._request_id_factory = request_id_factory
        self._gate = ApprovalGate(store=approvals)
        self._evaluator = PolicyEvaluator(policies)

    def _blocked_scope(self, scopes: list["BudgetScope"]) -> "BudgetScope":
        """The scope used for audit rows: the caller's most specific scope."""
        return scopes[0] if scopes else BudgetScope("global", "default")

    def _audit_blocked(
        self,
        *,
        caller: str,
        scopes: list["BudgetScope"],
        server_id: str,
        tool_name: str,
        args_redacted: dict[str, Any],
        decision: str,
        reason: str | None,
        approval_id: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """Persist a blocked-attempt audit row (decision/status per §4.6)."""
        scope = self._blocked_scope(scopes)
        event = AuditEvent(
            event_id="",
            server_id=server_id,
            tool_name=tool_name,
            caller=caller,
            scope_kind=scope.kind,
            scope_key=scope.key,
            args=args_redacted,
            decision=decision,
            status="blocked",
            reason=reason,
            cost=0.0,
            latency_ms=0,
            timestamp=int(time.time()),
            redacted=True,
            approval_id=approval_id,
            request_id=request_id,
        )
        self._audit.append(event)

    @staticmethod
    def _validate_args(tool: Any, args: dict[str, Any]) -> None:
        """JSON-Schema validate tool args against the registered input_schema.

        S2: the registry stores ``input_schema_json`` but nothing enforced it
        at call time, so an LLM could pass arbitrary/unexpected arguments to
        any tool (OWASP LLM06). An empty schema ({}) validates everything, so
        tools registered without a schema keep their current behavior.
        """
        schema = getattr(tool, "input_schema", None)
        if not schema:
            return
        try:
            jsonschema.validate(instance=args, schema=schema)
        except (jsonschema.ValidationError, jsonschema.SchemaError):
            raise InvalidArgumentsError(
                "tool arguments failed input_schema validation"
            ) from None

    async def before_call(
        self,
        *,
        caller: str,
        scopes: list["BudgetScope"],
        server_id: str,
        tool_name: str,
        args: Any,
        request_id: str | None = None,
    ) -> CallContext:
        """Gate a tool call BEFORE execution (RED stub)."""
        effective_request_id = request_id
        if effective_request_id is None and self._request_id_factory is not None:
            effective_request_id = self._request_id_factory()

        # 0. Args must be a mapping — dict(args) below would TypeError on a
        #    list/string and surface as an unhandled 500 (S14).
        if not isinstance(args, Mapping):
            raise InvalidArgumentsError("tool arguments must be a JSON object")

        # 1. Server must exist.
        server = self._registry.get_server(server_id)  # -> MCPServerNotFoundError
        # 2. Retired servers are fail-closed.
        if server.status == "retired":
            reason = "server is retired"
            self._audit_blocked(
                caller=caller,
                scopes=scopes,
                server_id=server_id,
                tool_name=tool_name,
                args_redacted=self._redactor.redact(dict(args)),
                decision="denied",
                reason=reason,
                request_id=effective_request_id,
            )
            raise AccessDeniedError(reason)
        # 3. Tool must be registered.
        tool = self._registry.get_tool(server_id, tool_name)  # -> MCPToolNotFoundError
        # 4. Disabled tools are fail-closed.
        if not tool.enabled:
            reason = "tool is disabled"
            self._audit_blocked(
                caller=caller,
                scopes=scopes,
                server_id=server_id,
                tool_name=tool_name,
                args_redacted=self._redactor.redact(dict(args)),
                decision="denied",
                reason=reason,
                request_id=effective_request_id,
            )
            raise AccessDeniedError(reason)

        # 4b. Tool args must match the registered input_schema (S2, OWASP
        #     LLM06) — reject malformed calls with 422 before any policy work.
        self._validate_args(tool, dict(args))

        # 5. Access policy resolution (deny-by-default engine gate).
        decision = self._evaluator.decide(
            scopes=scopes, server_id=server_id, tool_name=tool_name
        )
        approval_id: str | None = None
        if decision.effect == "deny":
            self._audit_blocked(
                caller=caller,
                scopes=scopes,
                server_id=server_id,
                tool_name=tool_name,
                args_redacted=self._redactor.redact(dict(args)),
                decision="denied",
                reason=decision.reason,
                request_id=effective_request_id,
            )
            raise AccessDeniedError(decision.reason)
        if decision.effect == "approval":
            gate = self._gate
            args_hash = args_hash_of(dict(args))
            # S15: find + consume run in ONE transaction so concurrent callers
            # cannot double-consume the same approval.
            approved = gate.consume_approved(
                caller=caller,
                server_id=server_id,
                tool_name=tool_name,
                args_hash=args_hash,
            )
            if approved is not None:
                decision_effect = "approved"
                approval_id = approved.approval_id
            else:
                policy = self._policies.get_policy(decision.policy_id)
                request = gate.create_request(
                    policy=policy,
                    caller=caller,
                    scopes=scopes,
                    server_id=server_id,
                    tool_name=tool_name,
                    args=dict(args),
                )
                if self._notifier is not None:
                    self._notifier.notify(request)
                self._audit_blocked(
                    caller=caller,
                    scopes=scopes,
                    server_id=server_id,
                    tool_name=tool_name,
                    args_redacted=self._redactor.redact(dict(args)),
                    decision="approval_required",
                    reason=decision.reason,
                    approval_id=request.approval_id,
                    request_id=effective_request_id,
                )
                raise ApprovalRequiredError(request.approval_id, decision.reason)
        else:
            decision_effect = "allowed"

        # 6. SSRF guard on tool args (async path — DNS off the event loop, S11).
        verdict = await self._ssrf.acheck(dict(args))
        if not verdict.allowed:
            self._audit_blocked(
                caller=caller,
                scopes=scopes,
                server_id=server_id,
                tool_name=tool_name,
                args_redacted=self._redactor.redact(dict(args)),
                decision="denied",
                reason=verdict.reason,
                request_id=effective_request_id,
            )
            raise PolicyViolationError(verdict.reason)

        # 7. Budget ceilings.
        try:
            await self._budgets.check(scopes, server_id, tool_name)
        except BudgetExceededError as exc:
            self._audit_blocked(
                caller=caller,
                scopes=scopes,
                server_id=server_id,
                tool_name=tool_name,
                args_redacted=self._redactor.redact(dict(args)),
                decision="denied",
                reason=str(exc),
                request_id=effective_request_id,
            )
            raise

        # 8. All gates passed -> build the CallContext.
        return CallContext(
            call_id=secrets.token_hex(8),
            request_id=effective_request_id,
            caller=caller,
            scopes=list(scopes),
            server_id=server_id,
            tool_name=tool_name,
            args_redacted=self._redactor.redact(dict(args)),
            decision=decision_effect,
            policy_id=decision.policy_id,
            approval_id=approval_id,
            reason=decision.reason,
        )

    async def after_call(
        self, ctx: CallContext, *, status: str, cost: float, latency_ms: int
    ) -> AuditEvent:
        """Record the outcome AFTER execution (RED stub)."""
        scope = self._blocked_scope(ctx.scopes)
        event = AuditEvent(
            event_id="",
            server_id=ctx.server_id,
            tool_name=ctx.tool_name,
            caller=ctx.caller,
            scope_kind=scope.kind,
            scope_key=scope.key,
            args=ctx.args_redacted,
            decision=ctx.decision,
            status=status,
            reason=ctx.reason,
            cost=cost,
            latency_ms=latency_ms,
            timestamp=int(time.time()),
            redacted=True,
            approval_id=ctx.approval_id,
            request_id=ctx.request_id,
        )
        stored = self._audit.append(event)
        if status == "completed":
            await self._budgets.record_usage(event=stored)
        return stored
