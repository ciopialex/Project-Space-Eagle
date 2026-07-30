"""Reconnect delay must grow under failure and forget that growth once a
session proves healthy.

Run:  .venv/bin/python -m pytest tests/ -q

THE RATCHET THIS PINS DOWN
--------------------------
`_conn_backoff` doubled on every network error, capped at 60, and was never
once reset on a successful connect. So a single bad-Wi-Fi stretch in the
morning left the delay pinned at 60 for the entire rest of the process. Hours
later, long after the network had recovered, one transient blip cost a full
minute of dead assistant — the eagle sitting silent because of an outage that
had ended before lunch.

Backoff exists to stop hammering a server that is down. Once a session has
lived long enough to prove the path is healthy, the evidence for backing off
is gone and the delay must go with it.

The reset is deliberately gated on HEALTH, not merely on connecting. A session
that connects and immediately dies is a crash loop, and a crash loop that
resets its own backoff is just a hot loop with extra steps — so a short-lived
session keeps growing the delay.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402


class FakeClock:
    """Monotonic time we control, so 'a session lived 30 seconds' costs nothing."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _backoff() -> tuple[main.ConnectionBackoff, FakeClock]:
    clock = FakeClock()
    return main.ConnectionBackoff(clock=clock), clock


def test_starts_at_the_base_delay():
    b, _ = _backoff()
    assert b.delay == main.ConnectionBackoff.BASE


def test_repeated_failures_grow_the_delay():
    """Consecutive failures must back off, or a hard-down server gets hammered."""
    b, _ = _backoff()
    b.grow()
    assert b.delay == 6.0
    b.grow()
    assert b.delay == 12.0


def test_growth_is_capped():
    """An outage must never push the delay past the ceiling."""
    b, _ = _backoff()
    for _ in range(20):
        b.grow()
    assert b.delay == main.ConnectionBackoff.MAX


def test_a_healthy_session_clears_the_accumulated_delay():
    """THE regression. An outage raises the delay to the ceiling; a session that
    then stays up long enough to prove the network is fine must hand the next
    reconnect a fresh base delay, not the ceiling from a bygone outage."""
    b, clock = _backoff()
    for _ in range(20):
        b.grow()
    assert b.delay == main.ConnectionBackoff.MAX

    b.on_connected()
    clock.advance(main.ConnectionBackoff.HEALTHY_AFTER_S + 1)
    b.on_failure()

    assert b.delay == main.ConnectionBackoff.BASE


def test_a_crash_looping_session_keeps_backing_off():
    """A session that dies on arrival is not evidence of health. If connecting
    alone reset the delay, a server rejecting us instantly would be retried in
    a tight loop forever."""
    b, clock = _backoff()
    b.grow()
    b.grow()
    assert b.delay == 12.0

    b.on_connected()
    clock.advance(0.5)          # died almost immediately
    b.on_failure()
    b.grow()

    assert b.delay == 24.0


def test_health_is_measured_from_connect_not_from_process_start():
    """Two short sessions back to back must not add up to one healthy one."""
    b, clock = _backoff()
    b.grow()

    for _ in range(3):
        b.on_connected()
        clock.advance(main.ConnectionBackoff.HEALTHY_AFTER_S / 3)
        b.on_failure()

    assert b.delay == 6.0       # still the grown value — never reset


def test_a_classified_error_can_set_an_explicit_delay():
    """A GoAway is an expected, instant migration — it must not inherit the
    delay accumulated by unrelated network failures."""
    b, _ = _backoff()
    b.grow()
    b.grow()
    b.set(1)
    assert b.delay == 1.0


def test_an_explicit_delay_is_still_the_base_for_later_growth():
    """Growth after a classified error resumes from that error's delay, so the
    sequence stays monotonic instead of jumping back to the old ceiling."""
    b, _ = _backoff()
    b.set(2)
    b.grow()
    assert b.delay == 4.0
