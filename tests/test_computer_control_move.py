"""move() used to only accept an absolute (x, y) target the model had to
guess. A live babysitting session (2026-08-13/14) asked it to move the
cursor in one direction and it moved on two axes at once — because the
model was supplying both coordinates from a guess, not a delta from where
the cursor actually was. One of those guesses drove the cursor into a
screen corner and tripped PyAutoGUI's fail-safe abort.

direction/amount mirrors scroll's own interface exactly (already correct,
already used successfully all through that same session).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actions.computer_control as CC  # noqa: E402


def test_direction_up_moves_only_the_y_axis(monkeypatch):
    monkeypatch.setattr(CC, "_require_pyautogui", lambda: None)
    monkeypatch.setattr(CC.pyautogui, "position", lambda: (500, 500))
    calls = []
    monkeypatch.setattr(CC.pyautogui, "moveTo",
                        lambda x, y, duration=0.3: calls.append((x, y)))
    CC._move(direction="up", amount=100)
    assert calls == [(500, 400)]


def test_direction_right_moves_only_the_x_axis(monkeypatch):
    monkeypatch.setattr(CC, "_require_pyautogui", lambda: None)
    monkeypatch.setattr(CC.pyautogui, "position", lambda: (500, 500))
    calls = []
    monkeypatch.setattr(CC.pyautogui, "moveTo",
                        lambda x, y, duration=0.3: calls.append((x, y)))
    CC._move(direction="right", amount=50)
    assert calls == [(550, 500)]


def test_explicit_x_y_still_means_absolute_as_before(monkeypatch):
    monkeypatch.setattr(CC, "_require_pyautogui", lambda: None)
    calls = []
    monkeypatch.setattr(CC.pyautogui, "moveTo",
                        lambda x, y, duration=0.3: calls.append((x, y)))
    CC._move(x=700, y=200)
    assert calls == [(700, 200)]


def test_neither_direction_nor_coordinates_is_a_clean_failure(monkeypatch):
    monkeypatch.setattr(CC, "_require_pyautogui", lambda: None)
    from core.tool_result import Failed
    result = CC._move()
    assert isinstance(result, Failed)
