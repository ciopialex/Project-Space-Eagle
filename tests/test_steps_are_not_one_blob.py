"""Four steps on one line are four steps, not one.

From a live manual run:

    {steps=Open makerworld.com | Search for "watch stand" |
           Select a popular model | Click the "Download" button}
    ✓ Planned "download a watch stand from makerworld" as 1 steps
    ✓ ... — done. Mission done: all 1 steps

The model separated its steps with pipes. `_steps_from` splits on newlines,
so all four became a single step. The ladder read the first word — "Open" —
took the open ladder, loaded makerworld, and marked the whole blob done. The
mission then reported COMPLETE and released the browser, which is why the
user watched the page open and close a second later under the words
"Download completed".

Nothing was downloaded. This is a false success of the purest kind: the
system did a quarter of one step and announced the goal was met.

Two defences, because either alone leaves the hole open:

  Accept the separators a model actually uses — newline, pipe, semicolon,
  numbered and bulleted lists.

  Refuse a step that still contains several actions. A separator nobody
  anticipated must degrade to "I cannot plan this", never to "one step that
  happens to succeed on its first verb".
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actions.mission as M  # noqa: E402


def _steps(raw):
    return [s.intent for s in M._steps_from(raw)]


# ── the separators a model actually uses ────────────────────────────────────

def test_the_exact_pipe_separated_plan_from_the_live_run():
    got = _steps('Open makerworld.com | Search for "watch stand" | '
                 'Select a popular model | Click the "Download" button')
    assert len(got) == 4, got
    assert got[0].startswith("Open makerworld")
    assert got[-1].startswith("Click")


def test_newlines_still_work():
    assert len(_steps("Open the page\nClick search\nType a query")) == 3


def test_semicolons_work():
    assert len(_steps("Open the page; Click search; Type a query")) == 3


def test_a_numbered_list_still_works():
    got = _steps("1. Open the page\n2. Click search")
    assert got == ["Open the page", "Click search"]


def test_a_list_is_still_a_list():
    assert len(M._steps_from(["Open the page", "Click search"])) == 2


def test_a_url_containing_a_slash_is_not_split():
    got = _steps("Go to https://makerworld.com/en/models | Click Download")
    assert len(got) == 2
    assert "makerworld.com/en/models" in got[0]


def test_a_single_step_is_left_alone():
    assert _steps("Click the Download button") == ["Click the Download button"]


# ── the backstop, for a separator nobody thought of ─────────────────────────

def test_a_step_with_several_actions_is_refused_not_run():
    """The failure mode this prevents: one blob that succeeds on its first
    verb and reports the whole goal done."""
    from core.mission_ladder import is_compound
    assert is_compound("Open makerworld.com then search for watch stand "
                       "and click Download") is True


def test_an_ordinary_step_is_not_flagged():
    from core.mission_ladder import is_compound
    for good in ("Click the Download button", "Open makerworld.com",
                 "Type watch stand", "Read the results",
                 "Click the first result", "Search for 'phone stand'"):
        assert is_compound(good) is False, good


def test_a_plan_that_is_one_giant_step_fails_to_start(monkeypatch, tmp_path):
    monkeypatch.setattr(M, "_store_path", lambda: tmp_path / "m.json")
    monkeypatch.setattr(M, "_report_path", lambda: tmp_path / "s.md")
    r = M.mission({"action": "start", "goal": "g",
                   "steps": ["Open the page and click download and submit it"]})
    assert r.ok is False
    assert r.guidance
