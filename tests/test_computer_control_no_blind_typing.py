"""Live, 2026-08-13/14: computer_control's own type/type_text/smart_type
actions reported success ("Typed: watch stand") with no verification the
text landed anywhere — and per the human watching, it didn't, until they
clicked the field themselves. The same guard mission_runners.py already
has (_refuse_blind, built earlier the same week) never covered
computer_control's own direct actions — this closes that gap by sharing
one focus-check both paths call.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actions.computer_control as CC  # noqa: E402
from core.tool_result import Failed  # noqa: E402


def test_type_refuses_when_nothing_has_focus(monkeypatch):
    monkeypatch.setattr(CC, "_require_pyautogui", lambda: None)
    import actions.grounding.focus as F
    monkeypatch.setattr(F, "focused_editable_name", lambda: None)
    result = CC._type("watch stand")
    assert isinstance(result, Failed)
    assert "focus" in str(result).lower()


def test_type_proceeds_when_something_has_focus(monkeypatch):
    monkeypatch.setattr(CC, "_require_pyautogui", lambda: None)
    typed = []
    monkeypatch.setattr(CC.pyautogui, "typewrite",
                        lambda text, interval=0.03: typed.append(text))
    import actions.grounding.focus as F
    monkeypatch.setattr(F, "focused_editable_name", lambda: "Search field")
    result = CC._type("watch stand")
    assert not isinstance(result, Failed)
    assert typed == ["watch stand"]
