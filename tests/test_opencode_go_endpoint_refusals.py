"""Two opencode-go refusals the gateway treated as "the model is gone".

Live evidence, same model, same credential, same x-opencode-session header
(scripts/probe_wire_shape.py):

    POST /chat/completions  -> 200  space-bunny-free answers
    POST /responses         -> 400  ModelProtocolUnsupported

and the provider-sweep (scripts/probe_responses_support.py) over 14 models:

    deepseek-*    /chat 200   /responses 200      both endpoints fine
    glm-5.2       /chat 200   /responses 400      parked as "retired"
    glm-5.3       /chat 200   /responses 400      parked as "retired"
    glm-5.3-flash /chat 200   /responses 400      parked as "retired"

So `ModelProtocolUnsupported` is the SAME refusal as the 503 "Endpoint is
unavailable" that `_responses_endpoint_unavailable` already recovers from by
retrying the other endpoint inside the same attempt. Matching only the 503
meant the 400 models were never retried — and because the 400 text is in
`_TERMINAL_PATTERNS`, they were parked for 30 days as "retired /
unavailable" while being perfectly alive. The user reaches the same model
directly on /chat/completions, which is why it works for them.

The second, separate refusal: opencode-go answers a request with no
`x-opencode-session` header with `400 MissingSessionID` on BOTH endpoints, in
~0.4s. The gateway has no such header in its registry (`extra_headers_json`
is `'{}'` on opencode-go2), so every opencode-go request dies before the
model is ever reached. A provider-wide static header is the right place for
it; a per-request session id is not needed, the routing hint just has to
exist.

Each test below fails against the current code.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

SRC = "/home/zoltan/llm-budget-gateway/src"
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from llm_budget_gateway.provider_direct import (  # noqa: E402
    UpstreamProviderError,
    _responses_endpoint_unavailable,
)

DATA = Path("/home/zoltan/llm-budget-gateway/.gateway-console")
SOURCE = Path(SRC) / "llm_budget_gateway" / "main.py"


# --- 1. the 400 is the same refusal as the 503 ---------------------------


def test_protocol_unsupported_400_retries_the_other_endpoint() -> None:
    """400 ModelProtocolUnsupported must trigger the same-attempt retry.

    The 400 text is already in `_TERMINAL_PATTERNS`, so without this the
    model is parked for 30 days as retired while working fine on the other
    endpoint.
    """
    exc = UpstreamProviderError(
        400,
        "upstream provider error: opencode-go (HTTP 400)",
        body='{"type":"error","error":{"type":"ModelProtocolUnsupported",'
             '"message":"Model does not support this protocol."}}',
    )
    assert _responses_endpoint_unavailable(exc) is True


def test_a_real_client_400_still_surfaces() -> None:
    """A 400 that is not an endpoint refusal must NOT be retried.

    Retrying a genuine client error (bad schema, unknown param) would burn
    the chain budget and hide the actual problem behind a second attempt.
    """
    exc = UpstreamProviderError(
        400,
        "upstream provider error: opencode-go (HTTP 400)",
        body='{"error":{"message":"Unknown parameter: max_tokens is not supported"}}',
    )
    assert _responses_endpoint_unavailable(exc) is False


def test_a_real_5xx_still_surfaces() -> None:
    """An outage is not an endpoint refusal: it must reach the chain."""
    exc = UpstreamProviderError(500, "upstream provider error: opencode-go (HTTP 500)")
    assert _responses_endpoint_unavailable(exc) is False


# --- 2. opencode-go needs x-opencode-session -----------------------------


def test_opencode_go_providers_carry_a_session_header() -> None:
    """The registry must give opencode-go an x-opencode-session header.

    Without it every request answers `400 MissingSessionID` on both
    endpoints in ~0.4s — the model is never reached at all.
    """
    if not (DATA / "providers.db").exists():
        pytest.skip("providers.db not present")
    from llm_budget_gateway.provider_connections import (
        CredentialVault,
        ProviderConnectionStore,
    )

    store = ProviderConnectionStore(
        sqlite3.connect(str(DATA / "providers.db"), check_same_thread=False),
        CredentialVault(DATA / "provider-master.key"),
    )
    import json

    # Measured 2026-09-26, same model and credential, both ways:
    #   opencode-zen   no header -> 200   (does not need one)
    #   opencode-go    no header -> 400 MissingSessionID, with header -> 200
    #   opencode-go2   no header -> 400 MissingSessionID, with header -> 403
    #     ("an active OpenCode Go subscription is required") — that key is
    #     expired, so the header is not what would fix it.
    # So the requirement is per endpoint, not per brand name.
    NEEDS_HEADER = {"opencode-go"}
    checked = 0
    for conn in store.list():
        slug = str(conn["slug"])
        if slug not in NEEDS_HEADER:
            continue
        checked += 1
        secret = store.connection_secret(str(conn["id"]))
        raw = str(secret.get("extra_headers_json") or "")
        assert raw, (
            f"provider {slug} has empty extra_headers_json: opencode-go "
            f"answers 400 MissingSessionID without x-opencode-session"
        )
        headers = json.loads(raw)
        assert "x-opencode-session" in {k.lower() for k in headers}, (
            f"provider {slug} must send x-opencode-session, has {sorted(headers)}"
        )
    assert checked, "no opencode provider found to check"


def test_the_registry_forwards_extra_headers_to_the_upstream() -> None:
    """The header must actually reach the request, not just sit in the DB.

    `main.py` reads `extra_headers_json` into the registry; if the transport
    ignores it the header is stored and never sent, which is the same bug
    wearing a different hat.
    """
    src = SOURCE.read_text()
    assert "extra_headers_json" in src, (
        "main.py must read extra_headers_json into the provider registry"
    )
    transport = Path(SRC) / "llm_budget_gateway" / "provider_direct.py"
    tsrc = transport.read_text()
    assert "extra_headers" in tsrc, (
        "provider_direct.py must send the registry's extra_headers upstream"
    )


def test_the_header_is_config_driven_not_hardcoded() -> None:
    """The header comes from the provider's own config, not a hardcoded branch.

    Reading `extra_headers_json` per provider keeps unrelated providers clean:
    only the opencode connections carry the session header, and a future
    provider that needs a different one needs no code change.
    """
    src = SOURCE.read_text()
    assert "extra_headers_json" in src
    code_lines = [
        ln for ln in src.splitlines()
        if "x-opencode-session" in ln and not ln.lstrip().startswith("#")
    ]
    assert not code_lines, (
        f"x-opencode-session must be data, not code: {code_lines}"
    )
