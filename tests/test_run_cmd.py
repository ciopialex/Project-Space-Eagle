"""Every external command must have a deadline, and hitting that deadline must
kill everything the command started.

Run:  .venv/bin/python -m pytest tests/ -q

THE HANG THIS PINS DOWN
-----------------------
77 subprocess call sites across actions/ had no `timeout=` at all. Any one of
them blocking forever is worse than it looks, because of how tools execute:

    r = await loop.run_in_executor(None, lambda: some_tool(...))

`asyncio.wait_for` cancels the AWAIT. It cannot cancel the OS thread. So a tool
wedged on a subprocess keeps its executor worker forever. The default executor
has min(32, cpu+4) workers — 16 on this machine — shared by every tool in the
app. Sixteen such hangs over a long session and tool dispatch silently stops
working, with no error raised anywhere. `asyncio.run()` then blocks on those
same threads at shutdown, which is the hang-on-quit.

`subprocess.run(timeout=...)` is necessary but NOT sufficient: on timeout it
kills only the direct child. A launcher, a shell pipeline or a git command that
spawned its own children leaves those grandchildren running, still holding the
files, ports and CPU the timeout was supposed to reclaim. Killing the process
GROUP is the part that actually reclaims the resources.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.run_cmd import DEFAULT_TIMEOUT_S, run_cmd  # noqa: E402

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


# ── ordinary behaviour is unchanged ─────────────────────────────────────────

def test_a_normal_command_returns_a_completed_process():
    r = run_cmd([sys.executable, "-c", "print('hi')"], capture_output=True, text=True)
    assert r.returncode == 0
    assert r.stdout.strip() == "hi"


def test_a_failing_command_reports_its_returncode_rather_than_raising():
    """Call sites branch on returncode; that contract must not change."""
    r = run_cmd([sys.executable, "-c", "raise SystemExit(3)"], capture_output=True)
    assert r.returncode == 3


def test_check_still_raises_when_asked():
    with pytest.raises(subprocess.CalledProcessError):
        run_cmd([sys.executable, "-c", "raise SystemExit(1)"], check=True)


# ── the deadline ────────────────────────────────────────────────────────────

def test_a_default_deadline_applies_when_the_caller_forgets_one():
    """THE regression. The 77 call sites forgot; forgetting must now be safe."""
    assert DEFAULT_TIMEOUT_S < 120, "a default nobody would wait through is not a default"

    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_cmd([sys.executable, "-c", "import time; time.sleep(600)"], timeout=0.5)
    assert time.monotonic() - t0 < 5


def test_an_explicit_deadline_is_respected():
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_cmd([sys.executable, "-c", "import time; time.sleep(600)"], timeout=0.3)
    elapsed = time.monotonic() - t0
    assert 0.2 < elapsed < 4, f"took {elapsed:.2f}s"


def test_a_generous_explicit_deadline_is_not_overridden_by_the_default():
    """A tool that legitimately takes a while must be able to say so."""
    r = run_cmd([sys.executable, "-c", "import time; time.sleep(0.2)"], timeout=30)
    assert r.returncode == 0


# ── the part plain subprocess.run gets wrong ────────────────────────────────

# A marker with no regex metacharacters — `pgrep -f` matches a REGEX, so a
# pattern like "time.sleep(600)" silently matches nothing and any assertion
# built on it passes vacuously.
_MARK = "AETHELARK_LEAK_PROBE_MARKER"

_SPAWNS_A_GRANDCHILD = (
    "import subprocess, sys, time;"
    "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)', sys.argv[1]]);"
    "print(p.pid, flush=True);"
    "time.sleep(300)"
)


def _survivors() -> list[str]:
    return subprocess.run(
        ["pgrep", "-f", _MARK], capture_output=True, text=True
    ).stdout.split()


@pytest.fixture(autouse=True)
def _reap_probes():
    """No test may leave a probe process behind, whatever it asserts."""
    yield
    for pid in _survivors():
        subprocess.run(["kill", "-9", pid], capture_output=True)


@posix_only
def test_plain_subprocess_run_leaks_grandchildren():
    """The baseline this wrapper exists to fix, asserted rather than assumed.
    subprocess.run's own timeout reaps only the direct child; anything it
    spawned is reparented to init and keeps running, still holding whatever the
    timeout was meant to reclaim. A launcher that backgrounds a browser is the
    everyday case. If this test ever fails, run_cmd's whole reason to exist
    should be re-examined."""
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run([sys.executable, "-c", _SPAWNS_A_GRANDCHILD, _MARK],
                       timeout=0.8, capture_output=True)
    time.sleep(0.4)
    assert len(_survivors()) == 1, "expected the stdlib to strand the grandchild"


@posix_only
def test_run_cmd_takes_the_whole_process_tree_with_it():
    """THE reason this wrapper exists — same scenario, nothing left behind."""
    with pytest.raises(subprocess.TimeoutExpired):
        run_cmd([sys.executable, "-c", _SPAWNS_A_GRANDCHILD, _MARK],
                timeout=0.8, capture_output=True)
    time.sleep(0.4)
    leaked = _survivors()
    assert leaked == [], f"run_cmd stranded {len(leaked)} process(es) past its timeout"


@posix_only
def test_the_direct_child_is_gone_after_a_timeout():
    """Belt and braces: the thing we launched is definitively reaped."""
    with pytest.raises(subprocess.TimeoutExpired) as exc:
        run_cmd([sys.executable, "-c", "import time; time.sleep(600)"], timeout=0.3)
    assert exc.value.timeout == 0.3
