"""An unrecognised action must not become code execution.

`desktop_control` documents eight actions plus an explicit AI-powered one
(`action="task"`). Its `else:` branch took ANY other string, handed it to
Gemini as a task description, and ran the generated Python through `exec`.

Found by calling it with `action="list_windows"` — a plausible-looking name
that does not exist. Instead of "unknown action", it generated
`pyautogui.getAllWindows()` (Windows-only) and executed it, then reported
"Execution error: module 'pyautogui' has no attribute 'getAllWindows'".

Three problems in one path:
  * a typo, or a name the model invented, silently becomes code execution
  * the caller cannot tell "that action does not exist" from "it ran"
  * the failure surfaces as an internal AttributeError, not a usable answer

Code generation is a real feature and stays — but it has to be ASKED for.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actions.desktop as D  # noqa: E402


def test_an_unknown_action_does_not_reach_the_code_generator(monkeypatch):
    called = []
    monkeypatch.setattr(D, "_ask_gemini_for_desktop_action",
                        lambda t: called.append(t) or "print('hi')")
    monkeypatch.setattr(D, "_execute_generated_code",
                        lambda c, player=None: called.append("EXEC") or "ran")

    result = D.desktop_control({"action": "list_windows"})
    assert called == [], "a bogus action was turned into generated code and run"
    assert "list_windows" in result


def test_an_unknown_action_names_the_real_ones(monkeypatch):
    monkeypatch.setattr(D, "_ask_gemini_for_desktop_action", lambda t: "")
    result = D.desktop_control({"action": "definitely_not_real"})
    for verb in ("wallpaper", "organize", "stats", "task"):
        assert verb in result, f"did not tell the caller about '{verb}'"


def test_the_code_generator_is_still_reachable_when_asked(monkeypatch):
    """The feature is not removed — it is made explicit."""
    seen = []
    monkeypatch.setattr(D, "_ask_gemini_for_desktop_action",
                        lambda t: seen.append(t) or "print('ok')")
    monkeypatch.setattr(D, "_execute_generated_code",
                        lambda c, player=None: "ok")

    D.desktop_control({"action": "task", "task": "tidy my desktop into folders"})
    assert seen == ["tidy my desktop into folders"]


def test_a_bare_task_string_still_works(monkeypatch):
    """`task` without `action` is the documented shorthand and stays."""
    seen = []
    monkeypatch.setattr(D, "_ask_gemini_for_desktop_action",
                        lambda t: seen.append(t) or "print('ok')")
    monkeypatch.setattr(D, "_execute_generated_code",
                        lambda c, player=None: "ok")

    D.desktop_control({"task": "sort my screenshots by date"})
    assert seen == ["sort my screenshots by date"]
