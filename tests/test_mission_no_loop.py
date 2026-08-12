"""Nothing in a mission may repeat work that is already done.

Reported live: "we're stuck in a loop where it opens the makerworld page again
and again."

Two ways that happens, and both are missing guards rather than bad luck:

1. `start` called again with the SAME goal. A blocked or slow mission makes
   the model try to be helpful; each `start` threw the old mission away,
   reset the cursor to zero, and step one opened the page again. Forever.
2. An open step that navigates unconditionally. If the window is already on
   that page, going there again is a page load, a lost scroll position, and
   any typing undone.

The rule: a step that is already satisfied is DONE, not repeated. That is the
same principle as the ladder never retrying a failed rung, applied to success.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actions.mission as M  # noqa: E402


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "_store_path", lambda: tmp_path / "m.json")
    monkeypatch.setattr(M, "_report_path", lambda: tmp_path / "stuck.md")
    monkeypatch.setattr(M, "_runners", lambda: {
        "web_open": lambda s: (True, "opened"),
        "web_click": lambda s: (True, "clicked"),
    })
    return tmp_path


def test_restarting_the_same_goal_resumes_instead_of_starting_over():
    """The loop, exactly."""
    steps = ["Go to makerworld.com", "Click the search box", "Click Download"]
    M.mission({"action": "start", "goal": "download a laptop stand", "steps": steps})
    M.mission({"action": "next"})            # step 1 done

    again = M.mission({"action": "start", "goal": "download a laptop stand",
                       "steps": steps})
    assert again.ok is True
    assert "1 of 3" in M.mission({"action": "status"}).message, \
        "restarted from zero and re-opened the page"


def test_the_resume_says_so_rather_than_pretending_it_planned():
    steps = ["a", "b"]
    M.mission({"action": "start", "goal": "g", "steps": steps})
    M.mission({"action": "next"})
    r = M.mission({"action": "start", "goal": "g", "steps": steps})
    assert "already" in r.message.lower() or "resum" in r.message.lower()


def test_a_genuinely_different_goal_does_start_fresh():
    M.mission({"action": "start", "goal": "download a laptop stand",
               "steps": ["a", "b"]})
    M.mission({"action": "next"})
    M.mission({"action": "start", "goal": "send a whatsapp to Mama",
               "steps": ["x", "y"]})
    assert "0 of 2" in M.mission({"action": "status"}).message


def test_a_finished_mission_can_be_run_again_from_the_top():
    """Resuming must not make a completed goal unrepeatable."""
    M.mission({"action": "start", "goal": "g", "steps": ["only"]})
    M.mission({"action": "next"})
    r = M.mission({"action": "start", "goal": "g", "steps": ["only"]})
    assert r.ok is True
    assert "0 of 1" in M.mission({"action": "status"}).message


def test_a_blocked_mission_restarts_rather_than_resuming_into_the_wall(monkeypatch):
    """Resuming a mission that is stuck would just hit the same wall — a NEW
    plan for the same goal is the point of asking again."""
    monkeypatch.setattr(M, "_runners", lambda: {})   # every rung missing
    M.mission({"action": "start", "goal": "g", "steps": ["a", "b"]})
    M.mission({"action": "next"})                    # blocks
    r = M.mission({"action": "start", "goal": "g", "steps": ["different", "plan"]})
    assert r.ok is True
    assert "0 of 2" in M.mission({"action": "status"}).message


# ── the open step ───────────────────────────────────────────────────────────

def test_opening_a_page_already_open_does_not_navigate_again(monkeypatch):
    from core.mission import Step
    import core.mission_runners as R

    navigated = []

    class _Port:
        def url(self): return "https://makerworld.com/en"
        def goto(self, u): navigated.append(u)
    monkeypatch.setattr(R, "_user_window",
                        lambda create=False: (_Port(), None))

    ok, detail = R._user_open(Step(intent="Go to makerworld.com",
                                   url="https://makerworld.com"))
    assert ok is True
    assert navigated == [], "reloaded a page it was already on"
    assert "already" in detail.lower()


def test_a_different_page_does_navigate(monkeypatch):
    from core.mission import Step
    import core.mission_runners as R

    navigated = []

    class _Port:
        def url(self): return "https://example.test"
        def goto(self, u): navigated.append(u)
    monkeypatch.setattr(R, "_user_window",
                        lambda create=False: (_Port(), None))

    ok, _ = R._user_open(Step(intent="go", url="https://makerworld.com"))
    assert ok is True and navigated == ["https://makerworld.com"]
