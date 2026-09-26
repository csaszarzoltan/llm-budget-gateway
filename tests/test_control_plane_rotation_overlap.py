"""Key rotation's overlap window does not exist.

`rotate_key(t, role, kid, overlap_seconds=N)` writes `overlap_until` on the
OLD key and issues a new one. The intent is a grace window: during it, the
old secret must still authenticate, so a fleet of clients can be rolled
without downtime.

`authenticate()` selects `overlap_until` and then never reads it. The check
is only `status != 'active'` and `expires <= now`. So the window is written,
listed, and completely inert — a caller who rotates with
`overlap_seconds=3600` gets a revoked-style cutover with no warning, and
every client holding the old secret is rejected the instant the new key
exists.

This is a silent no-op in a security control: it looks like a working grace
period in the API surface, so nobody tests the behaviour.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from llm_budget_gateway.control_plane import ControlPlane


@pytest.fixture()
def cp() -> ControlPlane:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    plane = ControlPlane(path)
    yield plane
    plane.db.close()
    os.unlink(path)


class _Clock:
    def __init__(self) -> None:
        self.now = 1_700_000_000

    def __call__(self) -> int:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += seconds


def _issue(cp: ControlPlane, clock: _Clock, label: str = "svc") -> dict:
    return cp.issue_key("acme", "admin", label, ["gpt-4o"])


def test_rotation_grace_window_is_ignored(cp: ControlPlane) -> None:
    """The old secret must keep working during the overlap window.

    With the bug: the old key stops authenticating the moment the new one
    is issued, whatever overlap_seconds says.
    """
    clock = _Clock()
    cp.clock = clock

    first = _issue(cp, clock)
    assert cp.authenticate(first["secret"]) is not None

    cp.rotate_key("acme", "admin", first["id"], overlap_seconds=3600)
    new = cp.issue_key("acme", "admin", "svc rotated", ["gpt-4o"])

    # inside the 1h window the old secret must still be accepted
    still_valid = cp.authenticate(first["secret"])
    assert still_valid is not None, (
        "the old secret stopped working the instant the new key was issued; "
        "overlap_seconds=3600 is a no-op — authenticate() never reads "
        "overlap_until"
    )
    assert cp.authenticate(new["secret"]) is not None


def test_overlap_window_closes(cp: ControlPlane) -> None:
    """After the window elapses the old secret must be rejected."""
    clock = _Clock()
    cp.clock = clock
    cp._clock = clock

    first = _issue(cp, clock)
    cp.rotate_key("acme", "admin", first["id"], overlap_seconds=3600)

    clock.advance(3601)
    assert cp.authenticate(first["secret"]) is None, (
        "the old secret still authenticates well past its overlap window"
    )


def test_overlap_column_is_actually_populated(cp: ControlPlane) -> None:
    """The write itself works — only the read is missing.

    If this passes while test_rotation_grace_window_is_ignored fails, the
    defect is precisely the missing check in authenticate(), not the
    rotation path.
    """
    clock = _Clock()
    cp.clock = clock
    cp._clock = clock

    first = _issue(cp, clock)
    cp.rotate_key("acme", "admin", first["id"], overlap_seconds=3600)
    row = cp.db.execute(
        "SELECT overlap_until FROM keys WHERE id=?", (first["id"],)
    ).fetchone()
    assert row is not None
    assert row["overlap_until"] == clock.now + 3600


def test_a_zero_overlap_still_cuts_over_immediately(cp: ControlPlane) -> None:
    """The default must keep its meaning: no grace, immediate cutover."""
    clock = _Clock()
    cp.clock = clock
    cp._clock = clock

    first = _issue(cp, clock)
    cp.rotate_key("acme", "admin", first["id"])
    assert cp.authenticate(first["secret"]) is None


def test_revoked_key_stays_dead_regardless_of_overlap(cp: ControlPlane) -> None:
    """revoke_key must win over any overlap — it is an explicit kill."""
    clock = _Clock()
    cp.clock = clock
    cp._clock = clock

    first = _issue(cp, clock)
    cp.rotate_key("acme", "admin", first["id"], overlap_seconds=3600)
    cp.revoke_key("acme", "admin", first["id"])
    assert cp.authenticate(first["secret"]) is None
