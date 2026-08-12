"""Never type into a window you have not identified.

Reported live, with a screenshot: a mission's "Type motherboard" step put the
word **into the user's terminal**, where Claude Code was running. The browser
never saw it. The step reported success.

This is not a new bug. It is the oldest one in this codebase, reintroduced by
me as a ladder rung. `actions/whatsapp_web.py` opens with:

    "the legacy desktop send blind-types into whatever window has focus. On
     Linux that meant 'Go to sleep' landed in the terminal instead of a chat."

That is exactly what happened again. `screen_type` and `press_keys` drive
pyautogui, which types wherever focus happens to be — and when the two precise
rungs above them fail, focus is usually NOT where the mission thinks it is.
That is precisely the moment they fire.

The rule: a rung that cannot say where the text will land must refuse. Typing
into an unknown window is not a fallback, it is an unbounded side effect on
the user's machine — it could as easily be a terminal, a chat, or a form
someone is halfway through.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.mission_runners as R  # noqa: E402
from core.mission import Step  # noqa: E402


def _typed(monkeypatch):
    """Record anything that reaches the physical keyboard."""
    sent = []
    import actions.computer_control as CC
    monkeypatch.setattr(CC, "_type", lambda t, interval=0.03:
                        sent.append(t) or f"Typed: {t}")
    monkeypatch.setattr(CC, "_smart_type", lambda t, clear_first=True:
                        sent.append(t) or f"Typed: {t}")
    monkeypatch.setattr(CC, "_press", lambda k: sent.append(k) or f"Pressed: {k}")
    return sent


def test_screen_type_refuses_when_focus_is_unknown(monkeypatch):
    """The exact reported failure: the word went into a terminal."""
    sent = _typed(monkeypatch)
    monkeypatch.setattr(R, "_focused_text_field", lambda: None)
    ok, detail = R._runners_screen_type(Step(intent="Type motherboard",
                                             text="motherboard"))
    assert ok is False
    assert sent == [], f"typed blind into whatever had focus: {sent}"
    assert "focus" in detail.lower()


def test_press_keys_refuses_when_focus_is_unknown(monkeypatch):
    """The per-key fallback is the same hazard, one character at a time."""
    sent = _typed(monkeypatch)
    monkeypatch.setattr(R, "_focused_text_field", lambda: None)
    ok, detail = R._press_keys(Step(intent="Type motherboard", text="motherboard"))
    assert ok is False
    assert sent == []


def test_typing_proceeds_when_a_text_field_really_has_focus(monkeypatch):
    """The capability is not removed — it is conditioned on knowing where the
    text goes."""
    sent = _typed(monkeypatch)
    monkeypatch.setattr(R, "_focused_text_field", lambda: "Search box")
    ok, _ = R._runners_screen_type(Step(intent="Type motherboard",
                                        text="motherboard"))
    assert ok is True
    assert sent == ["motherboard"]


def test_the_refusal_names_what_to_do_instead(monkeypatch):
    monkeypatch.setattr(R, "_focused_text_field", lambda: None)
    _, detail = R._runners_screen_type(Step(intent="Type x", text="x"))
    assert "click" in detail.lower() or "field" in detail.lower()


def test_a_focus_check_that_explodes_refuses_rather_than_typing(monkeypatch):
    """Fail closed. An unknown answer is not permission."""
    sent = _typed(monkeypatch)

    def boom():
        raise RuntimeError("no accessibility bus")
    monkeypatch.setattr(R, "_focused_text_field", boom)
    ok, _ = R._runners_screen_type(Step(intent="Type x", text="x"))
    assert ok is False and sent == []
