"""computer_control on the tool contract.

This is the tool that moves the mouse and presses keys. When it reports a
success it never had, the next action lands somewhere unintended and there is
nothing in the transcript to explain it. 15 of its returns describe a failure
and none of them told the model so.

Migrated at the boundary, as with file_processor: the entrypoint states what
it decided itself, and the helpers that genuinely know their outcome — focus
and clipboard, both fixed earlier today — now say so instead of returning
prose the caller has to interpret.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.computer_control import computer_control  # noqa: E402
from core.tool_result import ToolResult  # noqa: E402


def test_no_action_fails_and_lists_what_exists():
    r = computer_control({})
    assert isinstance(r, ToolResult) and r.ok is False
    assert "click" in r.guidance and "type" in r.guidance


def test_an_unknown_action_fails_rather_than_returning_prose():
    r = computer_control({"action": "levitate"})
    assert r.ok is False
    assert "levitate" in r.message
    assert r.guidance, "no next step offered"


def test_a_raising_helper_is_a_failure_not_a_success(monkeypatch):
    """This used to return "computer_control 'x' failed: boom" as a string,
    which normalize turned into ok=True — a crash reported as success, to the
    tool that drives the keyboard."""
    import actions.computer_control as C
    monkeypatch.setattr(C, "_type", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    r = computer_control({"action": "type", "text": "hello"})
    assert r.ok is False and "boom" in r.message


def test_focus_failure_is_reported_as_failure(monkeypatch):
    """The whole point. Claiming focus that never happened sends every
    following keystroke into the wrong window."""
    import actions.computer_control as C
    monkeypatch.setattr(C, "_active_window_title", lambda: "Some Other Window")
    monkeypatch.setattr(C.time, "sleep", lambda s: None)
    r = computer_control({"action": "focus_window", "title": "Nonexistent App"})
    assert r.ok is False
    assert "nothing was typed" in (r.message + r.guidance).lower()


def test_focus_success_is_reported_as_success(monkeypatch):
    import actions.computer_control as C
    monkeypatch.setattr(C, "_active_window_title", lambda: "My Editor — file.txt")
    monkeypatch.setattr(C.time, "sleep", lambda s: None)
    r = computer_control({"action": "focus_window", "title": "My Editor"})
    assert r.ok is True


def test_unverifiable_focus_is_neither_claimed_nor_denied(monkeypatch):
    """Wayland will not say which window is focused. That is a real third
    answer and flattening it either way is a lie."""
    import actions.computer_control as C
    monkeypatch.setattr(C, "_active_window_title", lambda: None)
    monkeypatch.setattr(C.time, "sleep", lambda s: None)
    r = computer_control({"action": "focus_window", "title": "My Editor"})
    assert r.ok is False, "unverified must not read as success"
    assert "unverified" in (r.message + r.guidance).lower()


def test_it_never_raises_whatever_it_is_handed():
    for junk in (None, {}, {"action": None}, {"action": 7}, {"action": "click"}):
        assert computer_control(junk) is not None
