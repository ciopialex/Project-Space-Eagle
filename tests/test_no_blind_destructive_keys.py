"""Never close, quit or kill a window you have not identified.

Reported live: the eagle tried to close the user's TERMINAL — the one running
Claude Code — apparently after he closed a MakerWorld window and it went
looking for something to close.

`computer_settings.close_window()` is one line:

    pyautogui.hotkey("alt", "f4")

That closes whatever holds focus. Not "the browser", not "the window the
mission opened" — whatever happens to be in front. The same shape as
`_desktop_send` blind-typing "Go to sleep" into a terminal, and as
`screen_type` putting "motherboard" there yesterday. Third instance of one
bug: an OS-level action fired without knowing its target.

Closing is worse than typing. Typing into the wrong window leaves a mess you
can see and undo. Closing the wrong window destroys unsaved work and, in this
case, the session the user was working in.

The rule: an action that destroys something must name what it is destroying,
and fail CLOSED when it cannot.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actions.computer_settings as CS  # noqa: E402


def _keys(monkeypatch):
    sent = []
    monkeypatch.setattr(CS, "_focused_window_name", lambda: None)
    import pyautogui
    monkeypatch.setattr(pyautogui, "hotkey", lambda *k: sent.append(k))
    return sent


def test_close_window_refuses_when_it_cannot_name_the_window(monkeypatch):
    """The reported failure, exactly."""
    sent = _keys(monkeypatch)
    r = CS.close_window()
    assert sent == [], f"sent a window-closing chord blind: {sent}"
    assert "refus" in str(r).lower() or "which window" in str(r).lower()


def test_close_window_refuses_a_terminal_even_when_identified(monkeypatch):
    """A terminal is where the operator lives. Closing it kills the session
    that is driving the eagle — including, once, this very conversation."""
    sent = []
    monkeypatch.setattr(CS, "_focused_window_name", lambda: "shennyonthebeat@7EVEN: ~")
    import pyautogui
    monkeypatch.setattr(pyautogui, "hotkey", lambda *k: sent.append(k))
    r = CS.close_window()
    assert sent == [], "closed a terminal"
    assert "terminal" in str(r).lower()


def test_close_window_proceeds_on_an_ordinary_identified_window(monkeypatch):
    """The capability is not removed — it is conditioned on knowing the target."""
    sent = []
    monkeypatch.setattr(CS, "_focused_window_name", lambda: "MakerWorld - Google Chrome")
    import pyautogui
    monkeypatch.setattr(pyautogui, "hotkey", lambda *k: sent.append(k))
    r = CS.close_window()
    assert sent, "refused a window it could name"
    assert "MakerWorld" in str(r)


def test_a_focus_check_that_explodes_refuses(monkeypatch):
    """Fail closed. Unknown is not permission."""
    sent = []
    def boom():
        raise RuntimeError("no accessibility bus")
    monkeypatch.setattr(CS, "_focused_window_name", boom)
    import pyautogui
    monkeypatch.setattr(pyautogui, "hotkey", lambda *k: sent.append(k))
    CS.close_window()
    assert sent == []


# ── the generic hotkey path is the same hazard ──────────────────────────────

def test_a_destructive_chord_is_refused_when_focus_is_unknown(monkeypatch):
    import actions.computer_control as CC
    sent = []
    monkeypatch.setattr(CC, "_focused_window_name", lambda: None, raising=False)
    import pyautogui
    monkeypatch.setattr(pyautogui, "hotkey", lambda *k: sent.append(k))
    for chord in (("alt", "f4"), ("ctrl", "w"), ("ctrl", "q"), ("command", "q")):
        CC._hotkey(*chord)
    assert sent == [], f"sent destructive chords blind: {sent}"


def test_an_ordinary_chord_still_works(monkeypatch):
    """Copy, paste, select-all are harmless and must not be gated."""
    import actions.computer_control as CC
    sent = []
    monkeypatch.setattr(CC, "_focused_window_name", lambda: None, raising=False)
    import pyautogui
    monkeypatch.setattr(pyautogui, "hotkey", lambda *k: sent.append(k))
    CC._hotkey("ctrl", "c")
    assert sent == [("ctrl", "c")]
