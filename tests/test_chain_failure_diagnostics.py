"""A 502 must leave a trace, or the next one is as undiagnosable as the last.

Four `smart` route 502s (2026-09-29 10:33, 10:36, 10:41, and one on
`gyenge2` at 02:47) shared an identical signature in `cost_records`:

    latency_ms=0  prompt_tokens=0  completion_tokens=0
    finish_reason=None  status=error

That is `GatewayProxy._handle_inner`'s `except Exception` branch: the end of
the chain, every candidate exhausted. What it does NOT say is why — and the
journal for those minutes held four lines, all `/health`. A `logger.warning`
without `exc_info` writes the message and drops the cause, so the failure
was unrecoverable after the fact.

The runtime test drives the real `_handle_inner` branch with a canary
exception and asserts the cause is recoverable from the log record alone —
no traceback archaeology, no cross-referencing timestamps.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import Mock

SRC = "/home/zoltan/llm-budget-gateway/src"
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import pytest  # noqa: E402

from llm_budget_gateway.config import Settings  # noqa: E402
from llm_budget_gateway.gateway_proxy import GatewayProxy  # noqa: E402

CANARY = "PROBE_CANARY_shard_unreachable"
LOGGER_NAME = "llm_budget_gateway.gateway_proxy"
SRC_FILE = Path(SRC) / "llm_budget_gateway" / "gateway_proxy.py"


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def proxy() -> GatewayProxy:
    """The same wiring tests/test_gateway_proxy.py uses, plus the two
    attributes `_handle_inner` iterates: a bare Mock is not iterable."""
    p = GatewayProxy(
        settings=Settings(virtual_keys={"k": "key1"}),
        cost_tracker=Mock(),
        budget_enforcer=Mock(),
        fallback_manager=Mock(),
    )
    # without this `_model_known` returns falsy and the request leaves on the
    # unknown-model branch, never reaching the chain-failure branch at all
    p._model_known = lambda model: True
    return p


@pytest.mark.asyncio
async def test_the_chain_failure_logs_the_cause_not_just_a_message(
    proxy: GatewayProxy,
) -> None:
    """The whole point: the next 502 must be answerable from the journal."""
    async def _boom(*args, **kwargs):
        raise RuntimeError(CANARY)

    proxy._forward_with_fallback = _boom

    cap = _Capture()
    log = logging.getLogger(LOGGER_NAME)
    log.addHandler(cap)
    log.setLevel(logging.DEBUG)
    try:
        await proxy._handle_inner({"model": "smart"}, "k", {}, "rid-canary")
    finally:
        log.removeHandler(cap)

    failures = [
        r for r in cap.records
        if "provider error" in r.getMessage() or "provider timeout" in r.getMessage()
    ]
    assert failures, (
        "the chain failure logged nothing: "
        f"{[r.getMessage() for r in cap.records]!r}"
    )
    rec = failures[-1]
    assert rec.exc_info, (
        "the record carries no exc_info — the cause is dropped, so a 502 is "
        "undiagnosable after the fact"
    )
    assert rec.exc_info[0] is RuntimeError
    msg = rec.getMessage()
    assert CANARY in msg, f"the exception detail must be in the message; got {msg!r}"
    assert "RuntimeError" in msg, f"the exception type must be in the message; got {msg!r}"
    assert "rid-canary" in msg, f"the request id must be there; got {msg!r}"
    assert "smart" in msg, f"the failing model must be there; got {msg!r}"


@pytest.mark.asyncio
async def test_a_timeout_also_keeps_its_cause(proxy: GatewayProxy) -> None:
    """The sibling branch, which had the same bare-warning shape."""
    from llm_budget_gateway.gateway_proxy import ProviderTimeoutError

    async def _slow(*args, **kwargs):
        raise ProviderTimeoutError("upstream took too long")

    proxy._forward_with_fallback = _slow

    cap = _Capture()
    log = logging.getLogger(LOGGER_NAME)
    log.addHandler(cap)
    log.setLevel(logging.DEBUG)
    try:
        await proxy._handle_inner({"model": "smart"}, "k", {}, "rid-timeout")
    finally:
        log.removeHandler(cap)

    failures = [r for r in cap.records if "provider timeout" in r.getMessage()]
    assert failures, "the timeout failure logged nothing"
    rec = failures[-1]
    assert rec.exc_info, "the timeout branch drops the cause too"
    assert "ProviderTimeoutError" in rec.getMessage()
    assert "rid-timeout" in rec.getMessage()


def test_a_bare_warning_would_lose_the_cause() -> None:
    """The regression being prevented, asserted as behaviour.

    `logger.warning(...)` with no `exc_info` yields a record with
    `exc_info is None`: the message survives, the reason does not.
    """
    cap = _Capture()
    log = logging.getLogger(LOGGER_NAME + ".bare_warning_probe")
    log.addHandler(cap)
    log.setLevel(logging.DEBUG)
    try:
        try:
            raise ValueError("cause only visible through exc_info")
        except ValueError:
            log.warning("provider error request=%s", "rid")
    finally:
        log.removeHandler(cap)

    assert cap.records[-1].exc_info is None, (
        "a bare warning is expected to lose the cause — that is the "
        "behaviour being replaced"
    )


@pytest.mark.parametrize("branch", ["provider error", "provider timeout"])
def test_both_failure_branches_keep_their_traceback(branch: str) -> None:
    """A static guard: neither branch may regress to a bare `warning`.

    Runtime behaviour is covered above; this catches an edit landing on one
    branch and missing the other.
    """
    text = SRC_FILE.read_text(encoding="utf-8")
    assert f'"{branch} request=%s model=%s type=%s detail=%s"' in text, (
        f"the `{branch}` branch lost its exception-type logging"
    )
    for indent in ("                ", "            "):
        assert f'logger.warning(\n{indent}"{branch}' not in text, (
            f"the `{branch}` branch must call logger.exception, not warning"
        )
