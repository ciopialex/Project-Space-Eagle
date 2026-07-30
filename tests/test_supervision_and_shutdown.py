"""The brain must not be able to die quietly, and quitting must actually stop
the things we started.

Run:  .venv/bin/python -m pytest tests/ -q

THE SILENT DEATH THIS PINS DOWN
-------------------------------
    def runner():
        ui.wait_for_api_key()
        aethelark = AethelarkLive(ui)          # unguarded
        try: asyncio.run(aethelark.run())
        except KeyboardInterrupt: ...

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()

Anything raised by that constructor, and any non-KeyboardInterrupt escape from
run(), killed a DAEMON thread with no supervision and no output. The window
stayed open, the pill kept animating, the clock kept ticking — and nothing
worked, forever, with no indication that anything was wrong. run() re-raises
SystemExit and KeyboardInterrupt deliberately, so this path is reachable.

A beautiful dead window is the worst failure mode available: it looks fine.

THE ORPHANS THIS PINS DOWN
--------------------------
core/proc_registry exists to answer "stop everything", and nothing ever called
it at exit. Only actions/pty_session registered an atexit hook. Close the
window and spawned agent processes kept running — holding worktrees, burning
CPU, invisible until the next `ps`.
"""
from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402
from core import proc_registry  # noqa: E402


class FakeUI:
    def __init__(self):
        self.logs = []

    def write_log(self, text):
        self.logs.append(text)

    def set_state(self, state):
        pass


# ── the supervisor ──────────────────────────────────────────────────────────

def test_a_core_that_finishes_cleanly_is_not_restarted():
    """Shutting down on purpose must not be treated as a fault."""
    calls = []
    main._run_core(FakeUI(), lambda: calls.append("ran"), sleep=lambda s: None)
    assert calls == ["ran"]


def test_a_crashed_core_is_restarted():
    """THE regression. A constructor blowing up used to end the assistant for
    the lifetime of the window, silently."""
    calls = []

    def _start():
        calls.append("attempt")
        if len(calls) < 3:
            raise RuntimeError("boom")

    main._run_core(FakeUI(), _start, sleep=lambda s: None)
    assert len(calls) == 3, "the core was not restarted after failing"


def test_the_user_is_told_when_the_core_falls_over():
    """A dead brain behind a live window must be visible. This is the whole
    difference between 'broken' and 'broken and unknowable'."""
    ui = FakeUI()
    calls = []

    def _start():
        calls.append(1)
        if len(calls) < 2:
            raise RuntimeError("boom")

    main._run_core(ui, _start, sleep=lambda s: None)
    assert any("ERR" in line for line in ui.logs), ui.logs
    assert any("restart" in line.lower() for line in ui.logs), ui.logs


def test_restarts_back_off_instead_of_hot_looping():
    """A core that fails instantly and forever must not spin the CPU."""
    delays = []
    attempts = []

    def _start():
        attempts.append(1)
        raise RuntimeError("always")

    main._run_core(FakeUI(), _start, sleep=delays.append, max_restarts=4)
    assert delays == sorted(delays), f"backoff not monotonic: {delays}"
    assert delays[-1] > delays[0], f"backoff never grew: {delays}"


def test_the_supervisor_gives_up_rather_than_restarting_forever():
    """Endless restarts of something permanently broken is a worse failure than
    stopping — it hides the cause behind a wall of identical errors."""
    attempts = []

    def _start():
        attempts.append(1)
        raise RuntimeError("always")

    ui = FakeUI()
    main._run_core(ui, _start, sleep=lambda s: None, max_restarts=3)
    assert len(attempts) == 4, f"expected 1 try + 3 restarts, got {len(attempts)}"
    assert any("give" in l.lower() or "gave" in l.lower() for l in ui.logs), ui.logs


@pytest.mark.parametrize("exc", [KeyboardInterrupt, SystemExit])
def test_deliberate_shutdown_is_never_restarted(exc):
    """Ctrl-C means stop, not try harder."""
    attempts = []

    def _start():
        attempts.append(1)
        raise exc()

    main._run_core(FakeUI(), _start, sleep=lambda s: None)
    assert len(attempts) == 1


# ── exit cleanup ────────────────────────────────────────────────────────────

@pytest.fixture
def restore_signals():
    saved = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    proc_registry._exit_handlers_installed = False
    yield
    for s, h in saved.items():
        signal.signal(s, h)
    proc_registry._exit_handlers_installed = False


def test_installing_exit_handlers_takes_over_sigterm(restore_signals):
    """THE regression. Closing the window left agent processes running because
    nothing connected 'we are stopping' to 'stop everything'."""
    before = signal.getsignal(signal.SIGTERM)
    proc_registry.install_exit_handlers()
    after = signal.getsignal(signal.SIGTERM)
    assert after is not before


def test_installing_exit_handlers_twice_is_harmless(restore_signals):
    """Imported from several places; installing must be idempotent or the
    second call chains a handler onto itself."""
    proc_registry.install_exit_handlers()
    first = signal.getsignal(signal.SIGTERM)
    proc_registry.install_exit_handlers()
    assert signal.getsignal(signal.SIGTERM) is first


def test_the_shutdown_path_kills_registered_agents(restore_signals, monkeypatch):
    """The handler must actually reach kill_all, not merely exist.

    Registers this very process as the agent — it is guaranteed alive, so
    `running()` reports it — with kill_all stubbed out, because a test that
    genuinely reaped its own runner would be a short test.
    """
    called = []
    monkeypatch.setattr(proc_registry, "kill_all",
                        lambda: called.append(True) or {"killed": [], "failed": []})
    proc_registry.clear()
    proc_registry.register("m1", "ws1", "claude", os.getpid())
    try:
        assert proc_registry.running(), "fixture process was not seen as alive"
        proc_registry.install_exit_handlers()
        proc_registry._on_exit()
        assert called == [True]
    finally:
        proc_registry.clear()


def test_shutdown_is_safe_when_nothing_is_registered(restore_signals):
    """The overwhelmingly common case: quit with no agents running."""
    proc_registry.clear()
    proc_registry.install_exit_handlers()
    proc_registry._on_exit()          # must not raise
