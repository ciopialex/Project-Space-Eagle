"""A wedged session must be noticed quickly, and noticing it must not depend on
how often we happen to print telemetry.

Run:  .venv/bin/python -m pytest tests/ -q

THE LATENCY THIS PINS DOWN
--------------------------
The wedge watchdog lived inside the telemetry printer's `while True: await
asyncio.sleep(30)`. The stall threshold was 25s, so the worst case was 30 + 25
= 55 seconds of the user staring at a mute eagle before anything tried to
recover — and the recovery itself was correct, it just never got a chance to
run on time.

Health checking and logging are different jobs with different natural rates.
Welding a 25-second decision to a 30-second log line means the log line wins.
So the loop now ticks fast for health and counts ticks for telemetry, which
keeps the console exactly as quiet as it was while cutting detection latency
to roughly the stall window itself.

The predicate is separated out because the *decision* is what deserves tests:
"the user asked something and the server has said nothing since" is the whole
of it, and it must never fire when the eagle is simply idle between turns.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402


class Watch:
    """The wedge predicate in isolation — AethelarkLive.__init__ needs audio,
    a Qt UI and an API key, none of which belong in a unit test."""

    _session_is_wedged = main.AethelarkLive._session_is_wedged

    def __init__(self, last_server: float, last_user: float, inflight=()):
        self._last_server_activity = last_server
        self._last_user_speech     = last_user
        # Work in flight suppresses the watchdog — a long tool is not a wedge.
        # Covered in depth by tests/test_nonblocking_tools.py; empty here so
        # these cases exercise the silence decision on its own.
        self._inflight_tools       = set(inflight)


# ── the decision ────────────────────────────────────────────────────────────

def test_an_idle_session_is_not_wedged():
    """Nobody has asked anything. Silence is correct behaviour, not a fault —
    reconnecting here would churn the session for no reason."""
    now = 1000.0
    w = Watch(last_server=now - 300.0, last_user=now - 400.0)
    assert w._session_is_wedged(now) is False


def test_a_session_answering_normally_is_not_wedged():
    """The user spoke, the server answered after. Nothing is owed."""
    now = 1000.0
    w = Watch(last_server=now - 1.0, last_user=now - 5.0)
    assert w._session_is_wedged(now) is False


def test_a_user_waiting_briefly_is_not_wedged():
    """Thinking time is not a wedge. Firing here would cut off slow answers."""
    now = 1000.0
    w = Watch(last_server=now - (main._TURN_STALL_S - 1), last_user=now - 1.0)
    assert w._session_is_wedged(now) is False


def test_a_user_waiting_past_the_stall_window_is_wedged():
    """THE condition. The user asked, the server has said nothing at all for
    longer than any real answer takes. That session is gone."""
    now = 1000.0
    w = Watch(last_server=now - (main._TURN_STALL_S + 1), last_user=now - 1.0)
    assert w._session_is_wedged(now) is True


# ── the cadence ─────────────────────────────────────────────────────────────

def test_detection_latency_is_bounded_by_the_stall_window_plus_one_tick():
    """THE regression. Detection used to cost the telemetry period on top of
    the stall window — 55s worst case. The tick must be fast enough that the
    stall window is what actually decides."""
    worst_case = main._TURN_STALL_S + main._WATCHDOG_TICK_S
    assert worst_case <= 30.0


def test_telemetry_stays_as_quiet_as_it_was():
    """Fixing the latency must not turn a 30s log line into a 2s one. The
    console noise budget is unchanged; only the health check got faster."""
    period = main._WATCHDOG_TICK_S * main._TELEMETRY_EVERY_N_TICKS
    assert 25.0 <= period <= 35.0
