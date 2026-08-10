"""The model says `type_text`; the tool calls it `type`. Meet it halfway.

From a real session, trying to type "laptop stand" into a search box:

    [Tool] ✗ computer_control (0ms)
      why: 'type_text' is not something computer_control does.
      next: Use one of: type, smart_type, click, ...

The declaration DOES list `type`. The model read it, called `type_text`
anyway — twice — and then degraded to pressing individual keys:

    press l · press a · press p · press t · press o · press p · press space
    press s · press t · press a · press n · press d

Twelve tool calls, each a full round trip to the model and back, to type
twelve characters. That is why the user's "download a laptop stand" failed
while "press the l key" worked: not because small instructions are easier,
but because one wrong action name forced the slowest possible path through
the same task.

`type_text` is what several other computer-use APIs call this, so the model
has a strong prior. Arguing with that prior is a decision that depends on how
clever the model is — the roadmap's §0 says to move those into code.

The second trap is worse and was never reached. The model sent
`{"action": "type_text", "value": "laptop stand"}`, and `type` reads
`params.get("text")`. Had the action name matched, it would have typed an
EMPTY STRING and reported success.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actions.computer_control as CC  # noqa: E402


def _typed(monkeypatch):
    """Capture what actually reaches the keyboard."""
    seen = []
    monkeypatch.setattr(CC, "_type", lambda text, interval=0.03:
                        seen.append(text) or f"Typed: {text}")
    monkeypatch.setattr(CC, "_smart_type",
                        lambda text, clear_first=True:
                        seen.append(text) or f"Typed: {text}")
    return seen


# ── the exact call from the log ─────────────────────────────────────────────

def test_the_real_failing_call_now_types(monkeypatch):
    seen = _typed(monkeypatch)
    r = CC._dispatch_action("type_text", {"value": "laptop stand"})
    assert seen == ["laptop stand"], f"nothing reached the keyboard: {r}"


def test_type_text_is_accepted_as_type(monkeypatch):
    seen = _typed(monkeypatch)
    CC._dispatch_action("type_text", {"text": "hello"})
    assert seen == ["hello"]


def test_other_names_the_model_reaches_for(monkeypatch):
    for alias in ("type_text", "write", "write_text", "enter_text",
                  "input_text", "keyboard_type", "send_keys"):
        seen = _typed(monkeypatch)
        CC._dispatch_action(alias, {"text": "x"})
        assert seen == ["x"], f"{alias!r} was refused"


# ── the parameter trap ──────────────────────────────────────────────────────

def test_value_is_accepted_where_text_is_expected(monkeypatch):
    """The silent one: right action, wrong key, types nothing, claims success."""
    seen = _typed(monkeypatch)
    CC._dispatch_action("type", {"value": "laptop stand"})
    assert seen == ["laptop stand"]


def test_typing_nothing_at_all_is_a_failure_not_a_success(monkeypatch):
    """If no text arrives under ANY key, that is not a successful type."""
    _typed(monkeypatch)
    r = CC._dispatch_action("type", {})
    ok = getattr(r, "ok", None)
    assert ok is False, f"typing an empty string reported {ok!r}"


def test_text_still_wins_when_both_are_present(monkeypatch):
    seen = _typed(monkeypatch)
    CC._dispatch_action("type", {"text": "right", "value": "wrong"})
    assert seen == ["right"]


# ── nothing else moves ──────────────────────────────────────────────────────

def test_a_genuinely_unknown_action_is_still_refused():
    r = CC._dispatch_action("teleport", {})
    assert getattr(r, "ok", None) is False
    assert "teleport" in r.message


def test_press_is_untouched(monkeypatch):
    pressed = []
    monkeypatch.setattr(CC, "_press", lambda k: pressed.append(k) or f"Pressed: {k}")
    CC._dispatch_action("press", {"key": "l"})
    assert pressed == ["l"]


def test_the_aliases_are_not_advertised_as_separate_actions():
    """They are a safety net, not new API. Adding them to the declaration
    would spend context teaching the model seven names for one thing."""
    assert "type_text" not in CC._ACTIONS
    assert "type" in CC._ACTIONS
