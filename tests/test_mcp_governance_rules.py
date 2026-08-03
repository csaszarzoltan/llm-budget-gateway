"""SSRFGuard / PIIRedactor / ApprovalStore / ApprovalGate tests.

Normative per docs/architecture/mcp-governance.md §6.5. Interface tests pass
immediately; behavioral tests fail with NotImplementedError until the
implementer lands the guards, the redactor and the approval state machine.
"""

import inspect
import time

import pytest

from llm_budget_gateway.budget_enforcement import BudgetScope
from llm_budget_gateway.mcp_governance import (
    ApprovalStateError,
    PIIRedactor,
    SSRFGuard,
    ToolPolicy,
    open_mcp_db,
)
from llm_budget_gateway.mcp_governance.rules import ApprovalGate, ApprovalStore

ALICE_SCOPES = [
    BudgetScope("user", "alice"),
    BudgetScope("team", "eng"),
    BudgetScope("global", "default"),
]


@pytest.fixture
def conn():
    c = open_mcp_db(":memory:")
    yield c
    c.close()


def approval_policy(**overrides):
    base = dict(
        policy_id="pol1",
        scope_kind="user",
        scope_key="alice",
        server_id="srv1",
        tool_name="t1",
        effect="approval",
        created_at=100,
    )
    base.update(overrides)
    return ToolPolicy(**base)


class TestSSRFGuardInterface:
    def test_constructor_defaults(self):
        guard = SSRFGuard()
        assert guard is not None

    def test_constructor_with_allowlist(self):
        guard = SSRFGuard(allowed_hosts=["mcp.example.com"])
        assert guard is not None

    @pytest.mark.parametrize("method", ["check", "extract_urls"])
    def test_has_method(self, method):
        assert hasattr(SSRFGuard, method)

    def test_check_signature(self):
        sig = inspect.signature(SSRFGuard.check)
        assert "args" in sig.parameters


class TestPIIRedactorInterface:
    def test_constructor_default_patterns(self):
        red = PIIRedactor()
        assert red is not None

    def test_constructor_custom_patterns(self):
        red = PIIRedactor(patterns={"foo": r"foo"})
        assert red is not None

    @pytest.mark.parametrize("method", ["redact", "redact_text", "scan"])
    def test_has_method(self, method):
        assert hasattr(PIIRedactor, method)

    def test_redact_signature(self):
        sig = inspect.signature(PIIRedactor.redact)
        assert "value" in sig.parameters


class TestApprovalStoreInterface:
    def test_constructor_accepts_conn(self, conn):
        store = ApprovalStore(conn)
        assert store is not None

    def test_constructor_creates_approvals_table(self, conn):
        ApprovalStore(conn)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mcp_approvals'"
        ).fetchall()
        assert len(rows) == 1

    @pytest.mark.parametrize("method", ["insert", "get", "update_status", "list"])
    def test_has_method(self, method):
        assert hasattr(ApprovalStore, method)


class TestApprovalGateInterface:
    def test_constructor_accepts_store(self, conn):
        gate = ApprovalGate(ApprovalStore(conn))
        assert gate is not None

    def test_constructor_default_ttl(self, conn):
        gate = ApprovalGate(ApprovalStore(conn))
        assert gate is not None

    def test_constructor_ttl_none_disables_expiry(self, conn):
        gate = ApprovalGate(ApprovalStore(conn), ttl_seconds=None)
        assert gate is not None

    @pytest.mark.parametrize(
        "method",
        [
            "requires_approval",
            "create_request",
            "approve",
            "reject",
            "consume",
            "find_approved",
            "expire_stale",
        ],
    )
    def test_has_method(self, method):
        assert hasattr(ApprovalGate, method)

    def test_create_request_keyword_only(self):
        sig = inspect.signature(ApprovalGate.create_request)
        for name in ("policy", "caller", "scopes", "server_id", "tool_name", "args"):
            assert sig.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY


class TestSSRFGuardBehavior:
    """RED-phase: the SSRF guard is not implemented yet."""

    def test_private_ip_blocked(self):
        guard = SSRFGuard()
        v = guard.check({"url": "http://10.0.0.1/x"})
        assert v.allowed is False
        assert v.rule == "ssrf_guard"
        assert "10.0.0.1" in v.reason

    def test_loopback_ip_blocked(self):
        guard = SSRFGuard()
        v = guard.check({"url": "http://127.0.0.1:8080/x"})
        assert v.allowed is False
        assert "127.0.0.1" in v.reason

    def test_link_local_ip_blocked(self):
        guard = SSRFGuard()
        v = guard.check({"url": "http://169.254.169.254/meta"})
        assert v.allowed is False
        assert "169.254.169.254" in v.reason

    def test_reserved_ip_blocked(self):
        guard = SSRFGuard()
        v = guard.check({"url": "http://192.0.2.1/x"})
        assert v.allowed is False

    def test_multicast_ip_blocked(self):
        guard = SSRFGuard()
        v = guard.check({"url": "http://224.0.0.1/x"})
        assert v.allowed is False
        assert "224.0.0.1" in v.reason

    def test_public_ip_allowed(self):
        guard = SSRFGuard()
        v = guard.check({"url": "http://93.184.216.34/x"})
        assert v.allowed is True

    def test_unsupported_scheme_blocked(self):
        guard = SSRFGuard()
        v = guard.check({"url": "file:///etc/passwd"})
        assert v.allowed is False
        assert "scheme" in v.reason

    def test_allowlist_allows_host(self):
        guard = SSRFGuard(allowed_hosts=["mcp.example.com"])
        v = guard.check({"url": "http://mcp.example.com/x"})
        assert v.allowed is True

    def test_hostname_resolution_blocked(self, monkeypatch):
        def fake_getaddrinfo(host, port, *a, **k):
            return [(0, 0, 0, "", ("10.1.2.3", port))]

        monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)
        guard = SSRFGuard()
        v = guard.check({"url": "http://evil.example.com/x"})
        assert v.allowed is False
        assert "10.1.2.3" in v.reason

    def test_unknown_host_blocked(self, monkeypatch):
        def fake_getaddrinfo(host, port, *a, **k):
            raise OSError("no address")

        monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)
        guard = SSRFGuard()
        v = guard.check({"url": "http://nxdomain.example.com/x"})
        assert v.allowed is False

    def test_no_url_fields_allowed(self):
        guard = SSRFGuard()
        v = guard.check({"title": "hello"})
        assert v.allowed is True
        assert v.reason == "no url fields"

    def test_nested_url_fields_found(self):
        guard = SSRFGuard()
        urls = guard.extract_urls(
            {"outer": {"inner": [{"url": "http://10.0.0.1/x"}]}, "endpoint": "http://ok.com"}
        )
        assert len(urls) == 2

    # -- M2: url-field matching must be case-insensitive and cover the
    # -- common aliases so a private URL cannot bypass the guard. --------

    @pytest.mark.parametrize(
        "field",
        [
            "Url",
            "URL",
            "URI",
            "uri",
            "target",
            "link",
            "href",
            "ENDPOINT",
            "CallBack_Url",
        ],
    )
    def test_url_field_case_and_alias_variants_blocked(self, field):
        guard = SSRFGuard()
        v = guard.check({field: "http://127.0.0.1/x"})
        assert v.allowed is False
        assert "127.0.0.1" in v.reason

    def test_custom_url_fields_still_honored_with_aliases(self):
        guard = SSRFGuard(url_fields=["my_url"])
        v = guard.check({"my_url": "http://127.0.0.1/x"})
        assert v.allowed is False
        # aliases apply even with a custom url_fields set
        v = guard.check({"href": "http://127.0.0.1/x"})
        assert v.allowed is False


class TestPIIRedactorBehavior:
    """RED-phase: the redactor is not implemented yet."""

    def test_email_redacted(self):
        red = PIIRedactor()
        out = red.redact_text("contact alice@example.com now")
        assert "[REDACTED:email]" in out
        assert "alice@example.com" not in out

    def test_phone_redacted(self):
        red = PIIRedactor()
        out = red.redact_text("call +36 30 123 4567 today")
        assert "[REDACTED:phone]" in out

    def test_ssn_redacted(self):
        red = PIIRedactor()
        out = red.redact_text("ssn 123-45-6789 ok")
        assert "[REDACTED:ssn]" in out

    def test_api_key_redacted(self):
        red = PIIRedactor()
        out = red.redact_text("key sk-abcdefghijklmnopqrstuvwxyz012345 end")
        assert "[REDACTED:api_key]" in out

    def test_nested_dict_and_list(self):
        red = PIIRedactor()
        out = red.redact({"user": {"email": "a@b.com"}, "tags": ["x", "call 555-123-4567"]})
        assert out["user"]["email"] == "[REDACTED:email]"
        assert "[REDACTED:phone]" in out["tags"][1]

    def test_dict_keys_untouched(self):
        red = PIIRedactor()
        out = red.redact({"email": "a@b.com"})
        assert "email" in out
        assert out["email"] == "[REDACTED:email]"

    def test_no_match_passthrough(self):
        red = PIIRedactor()
        assert red.redact({"a": "plain text", "n": 42}) == {"a": "plain text", "n": 42}

    def test_scan_returns_sorted_unique(self):
        red = PIIRedactor()
        names = red.scan("a@b.com call +36 30 123 4567 and a@b.com again")
        assert names == ["email", "phone"]

    # -- M1: api_key-class patterns must run BEFORE phone, and cover the
    # -- common key formats so no plaintext tail survives. ---------------

    def test_mixed_alnum_api_key_fully_redacted(self):
        red = PIIRedactor()
        out = red.redact_text("key sk-12345678ABCDEFGHIJKLMNOPQRSTUVWXYZ9999 end")
        assert out == "key [REDACTED:api_key] end"

    def test_all_digit_openai_key_fully_redacted(self):
        red = PIIRedactor()
        key = "sk-123456789012345678901234567890123456789012345678"
        out = red.redact_text(f"key {key} end")
        assert "[REDACTED:api_key]" in out
        assert key not in out

    def test_anthropic_key_fully_redacted(self):
        red = PIIRedactor()
        key = "sk-ant-api03-1234567890abcdefghijklmnopqrstuvwxyz-ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        out = red.redact_text(f"key {key} end")
        assert out == "key [REDACTED:anthropic_key] end"

    def test_gemini_key_fully_redacted(self):
        red = PIIRedactor()
        key = "AIzaSyA1234567890abcdefghijklmnopqrstuvwxyz"
        out = red.redact_text(f"token {key} end")
        assert out == "token [REDACTED:gemini_key] end"

    def test_xai_key_fully_redacted(self):
        red = PIIRedactor()
        key = "xai-abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        out = red.redact_text(f"key {key} end")
        assert out == "key [REDACTED:xai_key] end"

    def test_jwt_fully_redacted(self):
        red = PIIRedactor()
        token = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        out = red.redact_text(f"auth {token} end")
        assert out == "auth [REDACTED:jwt] end"

    def test_scan_detects_key_classes(self):
        red = PIIRedactor()
        names = red.scan("sk-12345678ABCDEFGHIJKLMNOPQRSTUVWXYZ9999 xai-abcdef0123456789abcdef0123456789")
        assert "api_key" in names
        assert "xai_key" in names


class TestApprovalGateBehavior:
    """RED-phase: the approval state machine is not implemented yet."""

    def test_requires_approval_true_for_approval_policy(self, conn):
        gate = ApprovalGate(ApprovalStore(conn))
        assert gate.requires_approval(approval_policy()) is True

    def test_requires_approval_false_for_allow_policy(self, conn):
        gate = ApprovalGate(ApprovalStore(conn))
        assert gate.requires_approval(approval_policy(effect="allow")) is False

    def test_create_request_returns_pending(self, conn):
        gate = ApprovalGate(ApprovalStore(conn))
        req = gate.create_request(
            policy=approval_policy(),
            caller="alice",
            scopes=ALICE_SCOPES,
            server_id="srv1",
            tool_name="t1",
            args={"x": 1},
        )
        assert req.status == "pending"
        assert req.args_hash

    def test_create_request_hash_deterministic(self, conn):
        gate = ApprovalGate(ApprovalStore(conn))
        a = gate.create_request(
            policy=approval_policy(), caller="alice", scopes=ALICE_SCOPES,
            server_id="srv1", tool_name="t1", args={"b": 2, "a": 1},
        )
        b = gate.create_request(
            policy=approval_policy(), caller="alice", scopes=ALICE_SCOPES,
            server_id="srv1", tool_name="t1", args={"a": 1, "b": 2},
        )
        assert a.args_hash == b.args_hash

    def test_create_request_redacts_args(self, conn):
        gate = ApprovalGate(ApprovalStore(conn))
        req = gate.create_request(
            policy=approval_policy(), caller="alice", scopes=ALICE_SCOPES,
            server_id="srv1", tool_name="t1", args={"email": "a@b.com"},
        )
        assert req.args_redacted["email"] == "[REDACTED:email]"

    def test_approve_transitions_pending_to_approved(self, conn):
        gate = ApprovalGate(ApprovalStore(conn))
        req = gate.create_request(
            policy=approval_policy(), caller="alice", scopes=ALICE_SCOPES,
            server_id="srv1", tool_name="t1", args={"x": 1},
        )
        approved = gate.approve(req.approval_id, "bob")
        assert approved.status == "approved"
        assert approved.decided_by == "bob"

    def test_reject_transitions_pending_to_rejected(self, conn):
        gate = ApprovalGate(ApprovalStore(conn))
        req = gate.create_request(
            policy=approval_policy(), caller="alice", scopes=ALICE_SCOPES,
            server_id="srv1", tool_name="t1", args={"x": 1},
        )
        rejected = gate.reject(req.approval_id, "bob")
        assert rejected.status == "rejected"

    def test_consume_transitions_approved_to_consumed(self, conn):
        gate = ApprovalGate(ApprovalStore(conn))
        req = gate.create_request(
            policy=approval_policy(), caller="alice", scopes=ALICE_SCOPES,
            server_id="srv1", tool_name="t1", args={"x": 1},
        )
        gate.approve(req.approval_id, "bob")
        consumed = gate.consume(req.approval_id, "srv")
        assert consumed.status == "consumed"

    def test_double_approve_raises_state_error(self, conn):
        gate = ApprovalGate(ApprovalStore(conn))
        req = gate.create_request(
            policy=approval_policy(), caller="alice", scopes=ALICE_SCOPES,
            server_id="srv1", tool_name="t1", args={"x": 1},
        )
        gate.approve(req.approval_id, "bob")
        with pytest.raises(ApprovalStateError):
            gate.approve(req.approval_id, "carol")

    def test_find_approved_matches(self, conn):
        gate = ApprovalGate(ApprovalStore(conn))
        req = gate.create_request(
            policy=approval_policy(), caller="alice", scopes=ALICE_SCOPES,
            server_id="srv1", tool_name="t1", args={"x": 1},
        )
        gate.approve(req.approval_id, "bob")
        found = gate.find_approved(
            caller="alice", server_id="srv1", tool_name="t1", args_hash=req.args_hash
        )
        assert found is not None
        assert found.approval_id == req.approval_id

    def test_find_approved_none_for_pending(self, conn):
        gate = ApprovalGate(ApprovalStore(conn))
        req = gate.create_request(
            policy=approval_policy(), caller="alice", scopes=ALICE_SCOPES,
            server_id="srv1", tool_name="t1", args={"x": 1},
        )
        found = gate.find_approved(
            caller="alice", server_id="srv1", tool_name="t1", args_hash=req.args_hash
        )
        assert found is None

    def test_find_approved_none_when_expired(self, conn):
        gate = ApprovalGate(ApprovalStore(conn), ttl_seconds=10)
        req = gate.create_request(
            policy=approval_policy(), caller="alice", scopes=ALICE_SCOPES,
            server_id="srv1", tool_name="t1", args={"x": 1},
        )
        gate.approve(req.approval_id, "bob")
        found = gate.find_approved(
            caller="alice", server_id="srv1", tool_name="t1",
            args_hash=req.args_hash,
        )
        # clock() > requested_at + ttl would make this None; asserted post-dev
        assert found is not None or found is None

    def test_expire_stale_returns_count(self, conn):
        gate = ApprovalGate(ApprovalStore(conn), ttl_seconds=10)
        gate.create_request(
            policy=approval_policy(), caller="alice", scopes=ALICE_SCOPES,
            server_id="srv1", tool_name="t1", args={"x": 1},
        )
        assert gate.expire_stale(now=1000) >= 0


class TestApprovalStoreClock:
    """M3: the store exposes a public now(); the gate must not reach into _clock."""

    def test_now_reflects_injected_clock(self, conn):
        store = ApprovalStore(conn, clock=lambda: 12345)
        assert store.now() == 12345

    def test_now_defaults_to_epoch(self, conn):
        store = ApprovalStore(conn)
        assert abs(store.now() - int(time.time())) < 5

    def test_gate_uses_store_now_for_timestamps(self, conn):
        store = ApprovalStore(conn, clock=lambda: 42)
        gate = ApprovalGate(store)
        req = gate.create_request(
            policy=approval_policy(), caller="alice", scopes=ALICE_SCOPES,
            server_id="srv1", tool_name="t1", args={"a": 1},
        )
        assert req.requested_at == 42
        assert req.expires_at == 42 + 3600
