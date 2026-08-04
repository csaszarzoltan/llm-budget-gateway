"""Pydantic v2 request/response models for mcp_governance.

Normative per docs/architecture/mcp-governance.md §4. Fully functional in the
RED phase: construction and validation work so interface tests pass
immediately. Request models forbid extra fields; ``Literal`` fields reject
unknown values with 422; regex and cross-field rules are enforced here.
"""

import calendar
import re
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NAME_PATTERN = r"^[A-Za-z0-9_.-]{1,64}$"
TOOL_NAME_PATTERN = r"^[A-Za-z0-9_.-]{1,128}$"
VERSION_PATTERN = r"^\d+\.\d+\.\d+$"

TRANSPORT_TYPES = ("stdio", "sse", "http", "websocket")
SCOPE_KINDS = ("user", "team", "project", "global")
EFFECTS = ("allow", "deny", "approval")


def _window_seconds(window: str) -> int:
    """Map a window string to seconds, mirroring budget_enforcement semantics.

    ``daily`` -> 86400, ``monthly`` -> the current calendar month, otherwise
    ``<n><s|m|h|d>``. Unknown window strings raise ValueError (-> 422).
    """
    if window == "daily":
        return 86_400
    if window == "monthly":
        year, month = time.gmtime(int(time.time()))[:2]
        return calendar.monthrange(year, month)[1] * 86_400
    if len(window) < 2 or window[-1] not in "smhd":
        raise ValueError(f"unknown budget window: {window!r}")
    amount = int(window[:-1])
    if amount < 1:
        # S12: 0/negative windows would disable the ceiling (spend window of 0
        # seconds never trips the hard limit) — reject them outright.
        raise ValueError(f"budget window amount must be >= 1: {window!r}")
    seconds = {"s": 1, "m": 60, "h": 3600, "d": 86_400}[window[-1]]
    return amount * seconds


def _check_endpoint(transport: str, endpoint: str | None) -> None:
    """Endpoint must be non-empty whenever the transport is not stdio (422)."""
    if transport != "stdio" and (endpoint is None or not endpoint.strip()):
        raise ValueError(f"endpoint required for transport {transport!r}")


def _check_tool_wildcard(server_id: str | None, tool_name: str | None) -> None:
    """tool_name may be set only when server_id is set (422)."""
    if tool_name is not None and server_id is None:
        raise ValueError("tool_name requires server_id")


# -- Shared validators (M6): one implementation, reused by every model so the
# -- regex / cross-field rules cannot drift between request and response types.

def _check_name(v: str, pattern: str, label: str) -> str:
    """Validate ``v`` against a name pattern (server or tool name)."""
    if not re.fullmatch(pattern, v):
        raise ValueError(f"invalid {label}: {v!r}")
    return v


def _check_version(v: str) -> str:
    """Validate a semantic-version string (``MAJOR.MINOR.PATCH``)."""
    if not re.fullmatch(VERSION_PATTERN, v):
        raise ValueError(f"invalid version: {v!r}")
    return v


def _check_scope_key(v: str) -> str:
    """scope_key must be non-empty (policy / budget scope)."""
    if not v:
        raise ValueError("scope_key must be non-empty")
    return v


def _check_limits(v: float | None) -> float | None:
    """Budget limits must be finite and >= 0."""
    if v is not None and (v < 0 or v != v or v in (float("inf"), float("-inf"))):
        raise ValueError("limits must be finite and >= 0")
    return v


def _check_window(v: str) -> str:
    """Validate a budget window string (raises ValueError for unknown ones)."""
    _window_seconds(v)
    return v


def _check_budget_model(
    server_id: str | None,
    tool_name: str | None,
    soft_limit: float | None,
    hard_limit: float | None,
) -> None:
    """Cross-field budget rules: wildcard constraint + at least one limit."""
    _check_tool_wildcard(server_id, tool_name)
    if soft_limit is None and hard_limit is None:
        raise ValueError("at least one of soft_limit / hard_limit must be set")


class MCPServer(BaseModel):
    server_id: str
    name: str
    transport: Literal["stdio", "sse", "http", "websocket"]
    endpoint: str | None = None
    version: str = "1.0.0"
    description: str = ""
    status: Literal["active", "retired"] = "active"
    config: dict[str, Any] = Field(default_factory=dict)
    created_at: int
    updated_at: int

    @field_validator("name")
    @classmethod
    def _name_matches(cls, v: str) -> str:
        return _check_name(v, NAME_PATTERN, "server name")

    @field_validator("version")
    @classmethod
    def _version_matches(cls, v: str) -> str:
        return _check_version(v)

    @model_validator(mode="after")
    def _endpoint_required(self) -> "MCPServer":
        _check_endpoint(self.transport, self.endpoint)
        return self


class ToolInfo(BaseModel):
    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _name_matches(cls, v: str) -> str:
        return _check_name(v, TOOL_NAME_PATTERN, "tool name")


class MCPRegistryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    transport: Literal["stdio", "sse", "http", "websocket"]
    endpoint: str | None = None
    version: str = "1.0.0"
    description: str = ""
    tools: list[ToolInfo] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name_matches(cls, v: str) -> str:
        return _check_name(v, NAME_PATTERN, "server name")

    @field_validator("version")
    @classmethod
    def _version_matches(cls, v: str) -> str:
        return _check_version(v)

    @model_validator(mode="after")
    def _validate(self) -> "MCPRegistryRequest":
        _check_endpoint(self.transport, self.endpoint)
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("duplicate tool names in request")
        return self


class ToolPolicy(BaseModel):
    policy_id: str
    scope_kind: Literal["user", "team", "project", "global"]
    scope_key: str
    server_id: str | None = None
    tool_name: str | None = None
    effect: Literal["allow", "deny", "approval"]
    description: str = ""
    created_by: str = "admin"
    created_at: int

    @field_validator("scope_key")
    @classmethod
    def _scope_key_nonempty(cls, v: str) -> str:
        return _check_scope_key(v)

    @model_validator(mode="after")
    def _wildcard_rule(self) -> "ToolPolicy":
        _check_tool_wildcard(self.server_id, self.tool_name)
        return self


class ToolPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope_kind: Literal["user", "team", "project", "global"]
    scope_key: str
    server_id: str | None = None
    tool_name: str | None = None
    effect: Literal["allow", "deny", "approval"]
    description: str = ""

    @field_validator("scope_key")
    @classmethod
    def _scope_key_nonempty(cls, v: str) -> str:
        return _check_scope_key(v)

    @model_validator(mode="after")
    def _wildcard_rule(self) -> "ToolPolicyRequest":
        _check_tool_wildcard(self.server_id, self.tool_name)
        return self


class ToolBudget(BaseModel):
    budget_id: str
    scope_kind: Literal["user", "team", "project", "global"]
    scope_key: str
    server_id: str | None = None
    tool_name: str | None = None
    soft_limit: float | None = None
    hard_limit: float | None = None
    window: str = "30d"
    created_at: int

    @field_validator("scope_key")
    @classmethod
    def _scope_key_nonempty(cls, v: str) -> str:
        return _check_scope_key(v)

    @field_validator("soft_limit", "hard_limit")
    @classmethod
    def _limits_valid(cls, v: float | None) -> float | None:
        return _check_limits(v)

    @field_validator("window")
    @classmethod
    def _window_valid(cls, v: str) -> str:
        return _check_window(v)

    @model_validator(mode="after")
    def _validate(self) -> "ToolBudget":
        _check_budget_model(
            self.server_id, self.tool_name, self.soft_limit, self.hard_limit
        )
        return self


class ToolBudgetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope_kind: Literal["user", "team", "project", "global"]
    scope_key: str
    server_id: str | None = None
    tool_name: str | None = None
    soft_limit: float | None = None
    hard_limit: float | None = None
    window: str = "30d"

    @field_validator("scope_key")
    @classmethod
    def _scope_key_nonempty(cls, v: str) -> str:
        return _check_scope_key(v)

    @field_validator("soft_limit", "hard_limit")
    @classmethod
    def _limits_valid(cls, v: float | None) -> float | None:
        return _check_limits(v)

    @field_validator("window")
    @classmethod
    def _window_valid(cls, v: str) -> str:
        return _check_window(v)

    @model_validator(mode="after")
    def _validate(self) -> "ToolBudgetRequest":
        _check_budget_model(
            self.server_id, self.tool_name, self.soft_limit, self.hard_limit
        )
        return self


class AuditEvent(BaseModel):
    event_id: str
    server_id: str
    tool_name: str
    caller: str
    scope_kind: Literal["user", "team", "project", "global"]
    scope_key: str
    args: dict[str, Any] = Field(default_factory=dict)
    decision: Literal["allowed", "denied", "approval_required", "approved", "error"]
    status: Literal["started", "completed", "blocked", "failed"]
    reason: str | None = None
    cost: float = 0.0
    latency_ms: int = 0
    timestamp: int
    redacted: bool = True
    approval_id: str | None = None
    request_id: str | None = None


class AuditPage(BaseModel):
    object: Literal["list"] = "list"
    data: list[AuditEvent]
    limit: int
    offset: int
    total: int


class PolicyDecision(BaseModel):
    effect: Literal["allow", "deny", "approval"]
    policy_id: str | None = None
    reason: str
    matched_scope: str | None = None


class RuleVerdict(BaseModel):
    allowed: bool
    rule: str
    reason: str
    detail: str | None = None


class ApprovalRequest(BaseModel):
    approval_id: str
    server_id: str
    tool_name: str
    caller: str
    scope_kind: Literal["user", "team", "project", "global"]
    scope_key: str
    args_redacted: dict[str, Any] = Field(default_factory=dict)
    args_hash: str
    status: Literal["pending", "approved", "rejected", "consumed", "expired"]
    requested_at: int
    decided_at: int | None = None
    decided_by: str | None = None
    expires_at: int | None = None
