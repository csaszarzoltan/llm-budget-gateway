"""A 400 that means "this model can't speak this protocol" is not request-level.

`_cooldown_decision` returned `skip=True` for every 400 — "the REQUEST is at
fault, not the provider" — so a model that answers
`400 ModelProtocolUnsupported: "Model does not support this protocol."`
was retried on EVERY request, forever, with no cooldown recorded.

Measured on hermes-default 2026-09-26: 74 such attempts in 24h, each
burning a chain slot and ~450ms before falling through to the next target
that actually answers. The 400 is not a malformed request: the identical
request succeeds on the next candidate. The model is permanently unable to
serve this protocol, which is exactly the `_TERMINAL_PATTERNS` signal the
module already defines for 401/403/404.

The request-level skip must apply to a REAL client error (bad JSON, bad
param) and not to a body that says the model is gone.
"""
from __future__ import annotations

from llm_budget_gateway.gateway_proxy import (
    _cooldown_decision,
    _is_terminal_unavailability,
)

INFO = {"seconds": 3600, "dynamic": True}

PROTOCOL_BODY = (
    '{"type":"error","error":{"type":"ModelProtocolUnsupported",'
    '"message":"Model does not support this protocol."}}'
)
GONE_BODY = '{"error":{"message":"This model does not exist"}}'


def test_a_protocol_400_is_not_a_plain_request_error() -> None:
    seconds, strike, terminal, skip = _cooldown_decision(400, INFO, PROTOCOL_BODY)
    assert skip is False, (
        "a 400 saying the model cannot speak the protocol is recorded as a "
        "client error, so nothing is parked and the model is retried forever"
    )
    assert terminal is True, "the body matches _TERMINAL_PATTERNS"
    assert seconds > 0


def test_a_genuine_client_400_is_still_skipped() -> None:
    """A real malformed request must not park anything — the next request
    with the same body would hit the same client error, and parking a model
    for it would punish the whole fleet for one bad caller."""
    for body in (
        '{"error":{"message":"invalid_request_error: max_tokens must be > 0"}}',
        '{"error":{"message":"messages is required"}}',
        '{"detail":"Unprocessable Entity"}',
    ):
        seconds, strike, terminal, skip = _cooldown_decision(400, INFO, body)
        assert skip is True, f"a plain client 400 must not park: {body}"
        assert seconds == 0


def test_the_terminal_detector_recognises_the_protocol_body() -> None:
    assert _is_terminal_unavailability(PROTOCOL_BODY) is True
    assert _is_terminal_unavailability(GONE_BODY) is True
    assert _is_terminal_unavailability('{"error":{"message":"bad request"}}') is False


def test_non_400_request_level_statuses_are_untouched() -> None:
    for code in (405, 408, 409, 413, 415, 422, 424, 425, 428):
        seconds, strike, terminal, skip = _cooldown_decision(code, INFO, "{}")
        assert skip is True, code
        assert seconds == 0, code


def test_a_protocol_400_does_not_climb_the_strike_ladder() -> None:
    """Terminal parking is its own signal; escalating the ladder on an
    auth/protocol signal is what parks a model for a day."""
    seconds, strike, terminal, skip = _cooldown_decision(400, INFO, PROTOCOL_BODY)
    assert strike is False, "a protocol mismatch must not count a strike"
