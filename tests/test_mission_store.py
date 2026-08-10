"""A mission held only in memory dies with the socket.

From the real MakerWorld log, mid-task:

    [Aethelark] 🔻 GoAway received (time_left=50s) — reconnecting with resume handle
    [Aethelark] ⏳ No server response for 26s — session wedged, reconnecting.

Both happened while the user was working through the task. Anything the eagle
was holding in memory was gone, and he was back to babysitting from the start.

Nothing here undoes anything. His words: "why would it undo, because if you
really work towards a goal, every checkpoint is valuable". So abandonment
writes down where it got to and stops — it does not tidy up.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.mission import Mission, Step  # noqa: E402
from core.mission_store import (clear, load, save,  # noqa: E402
                                write_stuck_report)


def test_a_mission_survives_a_round_trip(tmp_path):
    p = tmp_path / "m.json"
    m = Mission(goal="download a laptop stand",
                steps=[Step(intent="open makerworld", url="https://makerworld.com"),
                       Step(intent="click search", target="the search box")])
    m.record_attempt("web_click", ok=False, detail="no DOM")
    m.advance()
    save(m, p)

    back = load(p)
    assert back is not None
    assert back.goal == m.goal
    assert [s.intent for s in back.steps] == [s.intent for s in m.steps]
    assert back.cursor == 1
    assert back.steps[0].done is True
    assert back.steps[0].url == "https://makerworld.com"
    assert back.steps[0].attempts[0].detail == "no DOM"


def test_what_was_already_tried_survives_so_the_ladder_does_not_restart(tmp_path):
    """The point of persisting at all: after a reconnect the eagle must not
    re-run the rungs that already failed."""
    p = tmp_path / "m.json"
    m = Mission(goal="g", steps=[Step(intent="click search")])
    m.record_attempt("screen_click", ok=False, detail="not in the a11y tree")
    save(m, p)
    assert load(p).tried("screen_click") is True


def test_loading_nothing_returns_none_rather_than_an_empty_mission(tmp_path):
    assert load(tmp_path / "absent.json") is None


def test_a_corrupt_file_does_not_raise(tmp_path):
    p = tmp_path / "m.json"
    p.write_text("{not json")
    assert load(p) is None


def test_a_file_missing_its_goal_is_not_half_loaded(tmp_path):
    p = tmp_path / "m.json"
    p.write_text('{"steps": [], "cursor": 0}')
    assert load(p) is None


def test_clearing_removes_it(tmp_path):
    p = tmp_path / "m.json"
    save(Mission(goal="g", steps=[Step(intent="a")]), p)
    clear(p)
    assert load(p) is None


def test_clearing_something_absent_does_not_raise(tmp_path):
    clear(tmp_path / "never-existed.json")


# ── the note it leaves behind ───────────────────────────────────────────────

def test_a_stuck_mission_writes_a_report_naming_where_and_why(tmp_path):
    m = Mission(goal="print a laptop stand",
                steps=[Step(intent="open makerworld"),
                       Step(intent="click download")])
    m.advance()
    m.record_attempt("web_click", ok=False, detail="no control matches")
    m.record_attempt("screen_click", ok=False, detail="not in the a11y tree")
    m.block("ran out of ways to click it")

    text = write_stuck_report(m, tmp_path / "stuck.md").read_text()
    assert "print a laptop stand" in text
    assert "click download" in text
    assert "a11y tree" in text


def test_the_report_records_progress_made_not_only_the_failure(tmp_path):
    """Waking up to 'it failed' is useless. Waking up to 'these four are done,
    it stopped on the fifth because X' is a handover."""
    m = Mission(goal="g", steps=[Step(intent="open makerworld"),
                                 Step(intent="click download")])
    m.advance()
    text = write_stuck_report(m, tmp_path / "stuck.md").read_text()
    assert "open makerworld" in text
    assert "[x]" in text, "no way to see which steps actually completed"


def test_the_report_says_nothing_was_undone(tmp_path):
    m = Mission(goal="g", steps=[Step(intent="a")])
    text = write_stuck_report(m, tmp_path / "stuck.md").read_text().lower()
    assert "undone" in text or "undo" in text
