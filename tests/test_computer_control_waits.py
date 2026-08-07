"""Waiting for an effect, instead of assuming it after N milliseconds.

`focus_window` slept 300ms and then reported "Focused window: X" whether or
not the window was ever focused. Two failures in one line: a fixed delay
standing in for a measurement, and a tool claiming an outcome it never
checked. On a loaded machine 300ms is not enough and the eagle types into
whatever had focus instead; on an idle one it is 290ms of nothing.

The clipboard path has the same shape — copy, sleep 100ms, paste — where the
condition is trivially observable: is the text in the clipboard yet.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions import computer_control as C  # noqa: E402


def test_focus_returns_as_soon_as_the_window_is_focused(monkeypatch):
    """The win: an idle machine focuses in ~20ms and should not be billed
    for the full budget."""
    seen = iter(["Something Else", "My App — Editor"])
    monkeypatch.setattr(C, "_active_window_title", lambda: next(seen, "My App — Editor"))
    slept = []
    monkeypatch.setattr(C.time, "sleep", lambda s: slept.append(s))

    assert C._await_focus("My App", timeout=2.0, poll=0.02) is True
    assert sum(slept) < 0.2, f"waited {sum(slept)}s for a window already focused"


def test_focus_that_never_happens_is_reported_as_failure(monkeypatch):
    """The correctness half. Saying "Focused window: X" when focus never
    moved makes every following keystroke land somewhere unintended, and the
    model has no way to know."""
    monkeypatch.setattr(C, "_active_window_title", lambda: "Some Other Window")
    monkeypatch.setattr(C.time, "sleep", lambda s: None)
    assert C._await_focus("My App", timeout=0.1, poll=0.02) is False


def test_focus_is_matched_loosely_enough_to_be_useful(monkeypatch):
    """Window titles carry suffixes nobody asks for — "report.txt — gedit",
    "Inbox (12) - Gmail — Chrome". Requiring an exact match would fail on
    every real window."""
    monkeypatch.setattr(C, "_active_window_title", lambda: "report.txt — gedit")
    monkeypatch.setattr(C.time, "sleep", lambda s: None)
    assert C._await_focus("gedit", timeout=0.5, poll=0.01) is True
    assert C._await_focus("libreoffice", timeout=0.05, poll=0.01) is False


def test_an_unreadable_desktop_does_not_block_the_action(monkeypatch):
    """No xdotool, or Wayland refusing to say what is focused. The eagle
    cannot verify, so it must not pretend to — but it also must not refuse to
    act. It waits out a short grace and proceeds."""
    monkeypatch.setattr(C, "_active_window_title", lambda: None)
    slept = []
    monkeypatch.setattr(C.time, "sleep", lambda s: slept.append(s))
    assert C._await_focus("My App", timeout=1.0, poll=0.02) is None
    assert sum(slept) > 0, "did not pause at all before typing blind"


def test_clipboard_wait_returns_when_the_text_lands(monkeypatch):
    monkeypatch.setattr(C, "_clipboard_text", lambda: "hello world")
    slept = []
    monkeypatch.setattr(C.time, "sleep", lambda s: slept.append(s))
    assert C._await_clipboard("hello world", timeout=1.0, poll=0.01) is True
    assert sum(slept) < 0.05


def test_clipboard_wait_gives_up_rather_than_pasting_the_previous_thing(monkeypatch):
    """A clipboard that never updates means Ctrl+V pastes whatever was there
    before — silently wrong text, in someone else's document."""
    monkeypatch.setattr(C, "_clipboard_text", lambda: "something older")
    monkeypatch.setattr(C.time, "sleep", lambda s: None)
    assert C._await_clipboard("new text", timeout=0.1, poll=0.02) is False


def test_paste_waits_for_the_copy_rather_than_guessing(monkeypatch):
    """_clipboard_paste slept 100ms then pressed Ctrl+V. If the copy had not
    landed, Ctrl+V pastes the PREVIOUS clipboard into the user's document."""
    order = []
    monkeypatch.setattr(C, "pyperclip", type("P", (), {
        "copy": staticmethod(lambda t: order.append(("copy", t))),
        "paste": staticmethod(lambda: "new text")})())
    monkeypatch.setattr(C, "_clipboard_text", lambda: "new text")
    monkeypatch.setattr(C, "_require_pyautogui", lambda: None)
    monkeypatch.setattr(C, "pyautogui", type("A", (), {
        "hotkey": staticmethod(lambda *k: order.append(("paste", k)))})())
    monkeypatch.setattr(C.time, "sleep", lambda s: order.append(("sleep", s)))

    C._clipboard_paste("new text")
    assert ("paste", ("ctrl", "v")) in order or any(o[0] == "paste" for o in order)
    assert not any(o[0] == "sleep" and o[1] >= 0.1 for o in order), \
        "still sleeping a fixed 100ms instead of checking"


def test_paste_refuses_rather_than_pasting_the_wrong_thing(monkeypatch):
    monkeypatch.setattr(C, "pyperclip", type("P", (), {
        "copy": staticmethod(lambda t: None),
        "paste": staticmethod(lambda: "something older")})())
    monkeypatch.setattr(C, "_clipboard_text", lambda: "something older")
    monkeypatch.setattr(C, "_require_pyautogui", lambda: None)
    pressed = []
    monkeypatch.setattr(C, "pyautogui", type("A", (), {
        "hotkey": staticmethod(lambda *k: pressed.append(k))})())
    monkeypatch.setattr(C.time, "sleep", lambda s: None)

    result = C._clipboard_paste("new text")
    assert not pressed, "pasted the previous clipboard contents"
    assert "not" in result.lower() or "could" in result.lower()
