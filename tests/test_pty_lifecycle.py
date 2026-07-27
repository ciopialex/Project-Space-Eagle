"""Process-lifecycle invariants for PtySession.

Run:  .venv/bin/python -m pytest tests/ -q

Spawns only `sleep`. No coding agent, no billing, no terminal window (the
GUI viewer is opened separately by open_terminal_viewer, not by PtySession).

WHY THIS FILE EXISTS
--------------------
Killing agents is the most dangerous thing the swarm does routinely. Two ways
it goes wrong, both catastrophic and both silent:

  1. Signalling a process GROUP that isn't the agent's own. `close()` calls
     `os.killpg(self._proc.pid, ...)`, which is only correct if the child is a
     process-group leader. If it ever stopped being one, that call would signal
     Aethelark's own group and the daemon would kill itself.

  2. Addressing a process by a recycled PID. The OS reuses PIDs; a number that
     meant "frontend agent" a minute ago can mean something else now.
     PtySession is safe today because it holds a live Popen — an unreaped child
     keeps its PID reserved — so identity is the handle, never the integer.

Both properties are currently correct. These tests exist so that stays true:
they fail loudly if someone later drops `start_new_session` or starts passing
bare PIDs around.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.pty_session import PtySession  # noqa: E402

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX process-group semantics")


@pytest.fixture
def session(tmp_path):
    s = PtySession("test", "Test Agent", "sleep 30", tmp_path)
    yield s
    try:
        s.close()
    except Exception:
        pass


def test_child_is_its_own_process_group_leader(session):
    """The invariant that makes killpg safe.

    If pgid != pid the child is NOT a group leader, and os.killpg(pid) would
    signal whatever group it happens to belong to — Aethelark's own.
    """
    pid = session._proc.pid
    assert os.getpgid(pid) == pid, (
        "PTY child is not a process-group leader; os.killpg in close() would "
        "signal the daemon's own process group")


def test_child_is_not_in_aethelark_own_group(session):
    """Belt and braces: the agent's group must never be our group."""
    assert os.getpgid(session._proc.pid) != os.getpgid(0)


def test_close_actually_kills_and_is_verified_not_assumed(session):
    """Death must be observed. Releasing a worktree on an assumed-dead agent
    is how two agents end up writing the same tree."""
    assert session.is_alive()
    session.close()
    for _ in range(50):                     # up to ~5s for SIGHUP then SIGKILL
        if not session.is_alive():
            break
        time.sleep(0.1)
    assert not session.is_alive(), "session reported alive after close()"


def test_close_is_idempotent(session):
    session.close()
    session.close()                          # must not raise
    assert not session.is_alive()


def test_pid_stays_reserved_until_reaped(session):
    """Why bare PIDs are safe *here* and nowhere else.

    While Popen holds an unreaped child, the kernel cannot hand that PID to
    anyone else — so the integer still means the agent. This stops being true
    the moment a PID is written to disk and read back after a restart.
    """
    pid = session._proc.pid
    session.close()
    for _ in range(50):
        if session._proc.poll() is not None:
            break
        time.sleep(0.1)
    assert session._proc.poll() is not None, "child was never reaped"
    assert session._proc.pid == pid, "Popen identity changed underneath us"


def test_is_alive_is_false_after_the_process_exits_on_its_own(tmp_path):
    """A finished agent must read as dead, so the sentinel can replace it."""
    s = PtySession("test", "Test Agent", "true", tmp_path)
    try:
        for _ in range(50):
            if not s.is_alive():
                break
            time.sleep(0.1)
        assert not s.is_alive()
    finally:
        s.close()
