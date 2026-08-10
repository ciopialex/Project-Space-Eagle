"""Escalate through ways of doing a step. Never repeat one.

The observed failure, verbatim from the log:

    [Tool] ? computer_control (5049ms)  Did not click 'Search bar'.
           never became actionable: not_found (after 5049ms, 81 attempts)
    [Tool] ? computer_control (5044ms)  Did not click 'Search bar'.
           never became actionable: not_found (after 5043ms, 81 attempts)

Ten seconds spent proving the same thing twice, then a guess at (500,500).
The ladder makes that structurally impossible: a rung that has failed is not
offered again, and when the rungs run out the step says so instead of looping.

Ordering is accuracy, not preference. The DOM knows exactly where a control
is. The accessibility tree knows exactly where it is when the app publishes
one — Chrome publishes nothing without --force-renderer-accessibility, which
is why screen_click was blind on MakerWorld. Vision guesses: measured live at
5808ms and ~650px off.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.mission import Mission, Step  # noqa: E402
from core.mission_ladder import attempt, strategies_for  # noqa: E402


def _m(**kw):
    kw.setdefault("intent", "click the search box")
    kw.setdefault("target", "the search box")
    return Mission(goal="g", steps=[Step(**kw)])


def _runners(**outcomes):
    """name -> (ok, detail), recording call order."""
    order = []

    def make(name, result):
        def run(step):
            order.append(name)
            return result
        return run
    return {k: make(k, v) for k, v in outcomes.items()}, order


# ── the ladders ─────────────────────────────────────────────────────────────

def test_a_click_step_ladders_from_dom_to_screen_to_vision():
    assert strategies_for(Step(intent="click the search box", target="x")) == [
        "web_click", "screen_click", "vision_click"]


def test_a_type_step_has_its_own_ladder_ending_in_single_keys():
    s = strategies_for(Step(intent="type laptop stand", target="x",
                            text="laptop stand"))
    assert s[0] == "web_type"
    assert s[-1] == "press_keys", "no last resort for a page that will not take text"


def test_an_open_step_prefers_the_eagles_own_browser():
    """browser_control opens the USER's Chrome, which web_agency cannot see
    into — the prompt already warns about it, and the MakerWorld run fell into
    exactly that trap."""
    s = strategies_for(Step(intent="open makerworld.com", url="https://makerworld.com"))
    assert s[0] == "web_open"


def test_a_read_step_does_not_click_anything():
    s = strategies_for(Step(intent="read the results"))
    assert all("click" not in x for x in s), s


# ── escalation ──────────────────────────────────────────────────────────────

def test_the_first_rung_that_works_wins_and_nothing_below_it_runs():
    runners, order = _runners(web_click=(True, "clicked"),
                              screen_click=(True, "clicked"),
                              vision_click=(True, "clicked"))
    m = _m()
    out = attempt(m.current(), m, runners)
    assert out.ok is True and out.strategy == "web_click"
    assert order == ["web_click"]


def test_a_failing_rung_escalates_to_the_next():
    runners, order = _runners(web_click=(False, "no DOM here"),
                              screen_click=(True, "clicked"),
                              vision_click=(True, "clicked"))
    m = _m()
    out = attempt(m.current(), m, runners)
    assert out.ok is True and out.strategy == "screen_click"
    assert order == ["web_click", "screen_click"]


def test_a_rung_already_tried_is_never_run_again():
    """The exact observed bug, as an enforced rule."""
    runners, order = _runners(web_click=(False, "nope"),
                              screen_click=(False, "not_found"),
                              vision_click=(False, "nope"))
    m = _m()
    attempt(m.current(), m, runners)
    order.clear()
    attempt(m.current(), m, runners)
    assert order == [], f"re-ran strategies that had already failed: {order}"


def test_exhausting_the_ladder_says_so_rather_than_looping():
    runners, _ = _runners(web_click=(False, "a"), screen_click=(False, "b"),
                          vision_click=(False, "c"))
    m = _m()
    out = attempt(m.current(), m, runners)
    assert out.ok is False and out.exhausted is True


def test_a_rung_that_raises_is_a_failed_rung_not_a_crash():
    def boom(step):
        raise RuntimeError("browser died")
    runners = {"web_click": boom, "screen_click": lambda s: (True, "clicked")}
    m = _m()
    out = attempt(m.current(), m, runners)
    assert out.ok is True and out.strategy == "screen_click"
    assert "browser died" in m.current().attempts[0].detail


def test_a_missing_runner_is_skipped_not_counted_as_failure():
    """A rung with no implementation yet must not burn the step's budget."""
    runners = {"screen_click": lambda s: (True, "clicked")}
    m = _m()
    out = attempt(m.current(), m, runners)
    assert out.ok is True
    assert not any(a.strategy == "web_click" for a in m.current().attempts)


def test_every_failure_reason_is_kept_for_the_handoff():
    runners, _ = _runners(web_click=(False, "no DOM"),
                          screen_click=(False, "not in the a11y tree"),
                          vision_click=(False, "429 rate limit"))
    m = _m()
    attempt(m.current(), m, runners)
    detail = " ".join(a.detail for a in m.current().attempts)
    for reason in ("no DOM", "a11y tree", "429"):
        assert reason in detail


def test_the_outcome_carries_the_last_reason_for_the_user():
    runners, _ = _runners(web_click=(False, "no DOM"),
                          screen_click=(False, "not in the a11y tree"),
                          vision_click=(False, "vision could not look: 429"))
    m = _m()
    out = attempt(m.current(), m, runners)
    assert "429" in out.detail
