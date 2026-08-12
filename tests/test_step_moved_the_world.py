"""A step is done when the world moved, not when a call returned.

This is the last form of the bug this codebase has been fighting for weeks.
`ToolResult` fixed "the tool said nothing"; `settled` fixed "the tool said
nothing on success". Both prove the CALL succeeded. Neither proves anything
happened.

Live examples of the gap, all from real runs:

  - `computer_control` typed into whatever had focus and reported success. It
    had typed; whether it typed into the search box was never checked.
  - `screen_click` clicked at coordinates vision guessed, ~650px off target,
    and the click genuinely happened. Onto nothing.
  - A step that opened a page already open "succeeded" every time, forever.

So a rung reporting ok is now necessary and not sufficient: the page is
fingerprinted before and after, and a step that claims to have changed
something while the world sat still is reported as suspect.

Steps that legitimately change nothing exist — reading a page, or opening one
already open — so this cannot simply fail on "no change". It is carried as
evidence, and the step's own expectation decides what to make of it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.mission import Mission, Step  # noqa: E402
from core.mission_ladder import attempt  # noqa: E402
from core.world_state import Signature  # noqa: E402


def _m(intent="Click the search box", target="the search box", **kw):
    return Mission(goal="g", steps=[Step(intent=intent, target=target, **kw)])


def _sig(url="https://a.test/", n=3, h="abc"):
    return Signature(url=url, control_count=n, controls_hash=h)


def test_a_step_that_moved_the_world_is_recorded_as_moved():
    # First call is the "before", second the "after". Keyed on call count, not
    # on mission state — the check runs before the attempt is recorded.
    seen = iter([_sig(n=3), _sig(n=9, h="zzz")])
    m = _m()
    out = attempt(m.current(), m, {"web_click": lambda s: (True, "clicked")},
                  observe=lambda: next(seen))
    assert out.ok is True
    assert out.moved is True


def test_a_step_that_claims_success_while_nothing_moved_is_flagged():
    """The one that matters: it clicked, and the page did not react."""
    same = _sig()
    m = _m()
    out = attempt(m.current(), m, {"web_click": lambda s: (True, "clicked")},
                  observe=lambda: same)
    assert out.ok is True, "the rung did work; this is not a rung failure"
    assert out.moved is False
    assert "nothing" in out.detail.lower()


def test_the_evidence_is_kept_on_the_attempt_for_the_handoff():
    same = _sig()
    m = _m()
    attempt(m.current(), m, {"web_click": lambda s: (True, "clicked")},
            observe=lambda: same)
    assert "nothing" in m.current().attempts[0].detail.lower()


def test_a_reading_step_is_not_expected_to_move_anything():
    """`read`/`look` legitimately change nothing. Flagging those would make
    the signal useless within one page."""
    same = _sig()
    m = _m(intent="Read the page", target="")
    out = attempt(m.current(), m, {"web_look": lambda s: (True, "601 controls")},
                  observe=lambda: same)
    assert out.ok is True
    assert out.moved is not False or "nothing" not in out.detail.lower()


def test_a_failed_read_is_not_reported_as_a_still_world():
    """Two unknown signatures must not read as 'nothing changed' — that is
    the same collapse of "could not look" into "nothing there"."""
    m = _m()
    import itertools
    c = itertools.count()
    out = attempt(m.current(), m, {"web_click": lambda s: (True, "clicked")},
                  observe=lambda: Signature(unknown=True, nonce=next(c)))
    assert out.ok is True
    assert "could not" in out.detail.lower() or out.moved is None


def test_without_an_observer_behaviour_is_exactly_as_before():
    """Every existing caller passes no observer. Nothing may change for them."""
    m = _m()
    out = attempt(m.current(), m, {"web_click": lambda s: (True, "clicked")})
    assert out.ok is True and out.strategy == "web_click"
    assert out.moved is None


def test_a_failing_rung_still_escalates_with_an_observer_present():
    m = _m()
    out = attempt(m.current(), m,
                  {"web_click": lambda s: (False, "no DOM"),
                   "user_click": lambda s: (True, "clicked")},
                  observe=lambda: _sig())
    assert out.ok is True and out.strategy == "user_click"


def test_an_observer_that_explodes_does_not_break_the_step():
    def boom():
        raise RuntimeError("collect died")
    m = _m()
    out = attempt(m.current(), m, {"web_click": lambda s: (True, "clicked")},
                  observe=boom)
    assert out.ok is True
