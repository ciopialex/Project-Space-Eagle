"""The memory that does not exist today.

Every turn currently starts from nothing, so the eagle cannot say "I am on
step 3 of 6, and step 3 has failed twice". Three consequences, all observed
live on the MakerWorld attempt: it could not decompose, it could not
self-correct, and it escalated to the user instead of to the next strategy —
which is why every keystroke had to be voice-commanded.

The rule with teeth here is `tried()`. The log shows `screen_click "Search
bar"` running twice, 81 attempts and 5 seconds each, identically. Code refuses
that repeat. A prompt asking nicely did not.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.mission import Mission, Step  # noqa: E402


def test_a_mission_tracks_which_step_it_is_on():
    m = Mission(goal="download a laptop stand", steps=[
        Step(intent="open makerworld.com"),
        Step(intent="find the search box"),
    ])
    assert m.current().intent == "open makerworld.com"
    m.advance()
    assert m.current().intent == "find the search box"


def test_the_same_strategy_is_never_offered_twice_for_one_step():
    m = Mission(goal="g", steps=[Step(intent="click the search box")])
    assert m.tried("screen_click") is False
    m.record_attempt("screen_click", ok=False, detail="not_found after 81 tries")
    assert m.tried("screen_click") is True


def test_a_strategy_tried_on_one_step_is_still_available_on_the_next():
    """Scoped to the step, not the mission. Clicking twice in a plan is
    normal; clicking the SAME thing the same failed way twice is not."""
    m = Mission(goal="g", steps=[Step(intent="click a"), Step(intent="click b")])
    m.record_attempt("web_click", ok=False, detail="nope")
    m.advance()
    assert m.tried("web_click") is False


def test_a_failed_attempt_keeps_its_reason():
    m = Mission(goal="g", steps=[Step(intent="click X")])
    m.record_attempt("web_click", ok=False, detail="no control matches")
    assert "no control matches" in m.current().attempts[0].detail


def test_advancing_marks_the_step_done():
    m = Mission(goal="g", steps=[Step(intent="a"), Step(intent="b")])
    m.advance()
    assert m.steps[0].done is True


def test_a_mission_is_done_when_the_last_step_advances():
    m = Mission(goal="g", steps=[Step(intent="only")])
    m.advance()
    assert m.status == "done"
    assert m.current() is None


def test_a_mission_with_no_steps_is_planning_not_running():
    assert Mission(goal="g", steps=[]).status == "planning"


def test_planning_becomes_running_once_steps_arrive():
    m = Mission(goal="g", steps=[])
    m.plan([Step(intent="a"), Step(intent="b")])
    assert m.status == "running"
    assert m.current().intent == "a"


def test_blocking_a_mission_stops_it_advancing():
    m = Mission(goal="g", steps=[Step(intent="a"), Step(intent="b")])
    m.block("ran out of ways to click it")
    assert m.status == "blocked"
    assert m.blocked_reason == "ran out of ways to click it"


def test_progress_reads_as_a_human_would_say_it():
    m = Mission(goal="g", steps=[Step(intent="a"), Step(intent="b"), Step(intent="c")])
    m.advance()
    assert m.progress() == (1, 3)


def test_advancing_past_the_end_does_not_explode():
    m = Mission(goal="g", steps=[Step(intent="only")])
    m.advance()
    m.advance()
    assert m.status == "done"
