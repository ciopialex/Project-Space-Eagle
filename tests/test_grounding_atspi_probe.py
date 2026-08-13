"""screen_click's AT-SPI tier burned ~20 real seconds across 4 calls in a
live session (5s timeout × up to 85 internal attempts, every time) because
nothing checked whether AT-SPI could answer at all before polling it
repeatedly. A single cheap probe, cached for the process, turns a doomed
20-second wait into an instant skip to the next rung (vision)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.grounding import resolver as R  # noqa: E402


def test_atspi_unavailable_is_detected_once_and_cached(monkeypatch):
    calls = []
    def fake_probe():
        calls.append(1)
        return False
    monkeypatch.setattr(R, "_atspi_probe", fake_probe)
    monkeypatch.setattr(R, "_atspi_cache", None)
    assert R.atspi_available() is False
    assert R.atspi_available() is False
    assert len(calls) == 1, "probed more than once — should cache per process"
