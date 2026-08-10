"""The boundary: start, next, status, abandon — on the tool contract.

`next` is the whole design in one call. It loads the mission, walks ONE step
through the ladder, and only advances if that step is confirmed. The model
calls it repeatedly; it cannot thrash, because the ladder refuses a rung it
has already failed and the store remembers that across a reconnect.

Every seam that touches the world (`_store_path`, `_report_path`,
`_plan_locally`, `_runners`) is a module attribute so these tests never open a
browser, never call a model, and never write to the real config directory.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actions.mission as M  # noqa: E402
from core.tool_result import ToolResult  # noqa: E402


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "_store_path", lambda: tmp_path / "m.json")
    monkeypatch.setattr(M, "_report_path", lambda: tmp_path / "stuck.md")
    return tmp_path


def _plan(*steps):
    return lambda goal: list(steps)


# ── start ───────────────────────────────────────────────────────────────────

def test_start_needs_a_goal():
    r = M.mission({"action": "start"})
    assert isinstance(r, ToolResult) and r.ok is False and r.guidance


def test_start_plans_and_says_how_many_steps(monkeypatch):
    monkeypatch.setattr(M, "_plan_locally",
                        _plan("Open makerworld.com", "Click the search box"))
    r = M.mission({"action": "start", "goal": "download a laptop stand"})
    assert r.ok is True
    assert "2" in r.message and "makerworld" in r.message.lower()


def test_a_plan_that_comes_back_empty_is_a_failure_not_an_empty_mission(monkeypatch):
    monkeypatch.setattr(M, "_plan_locally", _plan())
    r = M.mission({"action": "start", "goal": "do something impossible"})
    assert r.ok is False and r.guidance


def test_starting_again_replaces_the_old_mission(monkeypatch):
    monkeypatch.setattr(M, "_plan_locally", _plan("a"))
    M.mission({"action": "start", "goal": "first"})
    monkeypatch.setattr(M, "_plan_locally", _plan("b", "c"))
    r = M.mission({"action": "start", "goal": "second"})
    assert r.ok is True
    assert "second" in M.mission({"action": "status"}).message


# ── next ────────────────────────────────────────────────────────────────────

def test_next_without_a_mission_fails_rather_than_inventing_one():
    r = M.mission({"action": "next"})
    assert r.ok is False and r.guidance


def test_next_runs_one_step_and_advances_when_it_works(monkeypatch):
    monkeypatch.setattr(M, "_plan_locally", _plan("Click the search box", "Type it"))
    monkeypatch.setattr(M, "_runners", lambda: {
        "web_click": lambda s: (True, "clicked"),
        "web_type": lambda s: (True, "typed"),
    })
    M.mission({"action": "start", "goal": "g"})
    r = M.mission({"action": "next"})
    assert r.ok is True
    assert "1" in r.message and "2" in r.message      # "step 1 of 2"


def test_next_does_NOT_advance_when_every_rung_fails(monkeypatch):
    monkeypatch.setattr(M, "_plan_locally", _plan("Click the search box", "second"))
    monkeypatch.setattr(M, "_runners", lambda: {
        "web_click": lambda s: (False, "no DOM"),
        "screen_click": lambda s: (False, "not in the a11y tree"),
        "vision_click": lambda s: (False, "429"),
    })
    M.mission({"action": "start", "goal": "g"})
    r = M.mission({"action": "next"})
    assert r.ok is False
    assert "search box" in r.message
    # The cursor must NOT have moved — a step nobody could do is not done.
    status = M.mission({"action": "status"}).message
    assert "0 of 2" in status, status
    assert "Click the search box" in status


def test_a_blocked_step_is_not_retried_from_scratch_on_the_next_call(monkeypatch):
    """After a reconnect or another `next`, the failed rungs must stay failed."""
    calls = []
    monkeypatch.setattr(M, "_plan_locally", _plan("Click the search box"))
    monkeypatch.setattr(M, "_runners", lambda: {
        "web_click": lambda s: (calls.append("w") or (False, "no DOM")),
        "screen_click": lambda s: (calls.append("s") or (False, "nope")),
        "vision_click": lambda s: (calls.append("v") or (False, "nope")),
    })
    M.mission({"action": "start", "goal": "g"})
    M.mission({"action": "next"})
    calls.clear()
    M.mission({"action": "next"})
    assert calls == [], f"re-ran rungs that had already failed: {calls}"


def test_running_the_last_step_finishes_the_mission(monkeypatch):
    monkeypatch.setattr(M, "_plan_locally", _plan("only step"))
    monkeypatch.setattr(M, "_runners", lambda: {"web_click": lambda s: (True, "ok")})
    M.mission({"action": "start", "goal": "g"})
    r = M.mission({"action": "next"})
    assert r.ok is True and "done" in r.message.lower()


def test_next_after_done_says_so_rather_than_failing(monkeypatch):
    monkeypatch.setattr(M, "_plan_locally", _plan("only"))
    monkeypatch.setattr(M, "_runners", lambda: {"web_click": lambda s: (True, "ok")})
    M.mission({"action": "start", "goal": "g"})
    M.mission({"action": "next"})
    r = M.mission({"action": "next"})
    assert "done" in r.message.lower() or "no mission" in r.message.lower()


def test_progress_survives_a_reload_between_calls(monkeypatch):
    """Each call reloads from disk — that is what makes it survive a GoAway."""
    monkeypatch.setattr(M, "_plan_locally", _plan("a", "b", "c"))
    monkeypatch.setattr(M, "_runners", lambda: {"web_click": lambda s: (True, "ok")})
    M.mission({"action": "start", "goal": "g"})
    M.mission({"action": "next"})
    M.mission({"action": "next"})
    assert "2 of 3" in M.mission({"action": "status"}).message


# ── status / abandon ────────────────────────────────────────────────────────

def test_status_without_a_mission_is_honest():
    r = M.mission({"action": "status"})
    assert "no mission" in r.message.lower()


def test_abandon_writes_the_report_and_clears(monkeypatch, _sandbox):
    monkeypatch.setattr(M, "_plan_locally", _plan("a"))
    M.mission({"action": "start", "goal": "g"})
    r = M.mission({"action": "abandon"})
    assert r.ok is True
    assert (_sandbox / "stuck.md").exists()
    assert M.mission({"action": "next"}).ok is False


def test_an_unknown_action_is_refused_with_the_real_list():
    r = M.mission({"action": "teleport"})
    assert r.ok is False
    for verb in M._ACTIONS:
        assert verb in r.message


def test_the_tool_never_raises(monkeypatch):
    def boom(goal):
        raise RuntimeError("planner exploded")
    monkeypatch.setattr(M, "_plan_locally", boom)
    r = M.mission({"action": "start", "goal": "g"})
    assert isinstance(r, ToolResult) and r.ok is False
