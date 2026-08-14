"""Final whole-branch review, Finding 4: `focused_editable_name()` called
`atspi_available()` unconditionally with no platform check. That function
is Linux-specific — `import gi` always fails on Windows/macOS — so on those
platforms `focused_editable_name()` always returned None, which meant
computer_control's typing guard (Task 4) refused EVERY type on non-Linux,
a real regression on platforms that previously typed successfully.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.grounding.base import UINode  # noqa: E402
from actions.grounding.focus import focused_editable_name  # noqa: E402

_FOCUSED_EDITABLE = frozenset({"FOCUSED", "EDITABLE", "VISIBLE"})
_PLAIN = frozenset({"VISIBLE"})


def test_windows_does_not_gate_on_the_linux_only_atspi_probe(monkeypatch):
    """A focused, editable Windows control must be found without ever
    consulting `atspi_available()` — the Linux-only check that always
    returns False on this platform and, before this fix, made the guard
    refuse to type there unconditionally."""
    import actions.grounding.windows as W
    monkeypatch.setattr(
        W, "live_walker",
        lambda scope="active": iter([UINode("Search", "Edit", 0, 0, 10, 10, _FOCUSED_EDITABLE)]))

    def _boom():
        raise AssertionError("atspi_available() must not be consulted on win32")
    import actions.grounding.resolver as R
    monkeypatch.setattr(R, "atspi_available", _boom)

    assert focused_editable_name(platform="win32") == "Search"


def test_macos_does_not_gate_on_the_linux_only_atspi_probe(monkeypatch):
    """Same regression, macOS side."""
    import actions.grounding.macos as M
    monkeypatch.setattr(
        M, "live_walker",
        lambda scope="active": iter([UINode("Search", "AXTextField", 0, 0, 10, 10, _FOCUSED_EDITABLE)]))

    def _boom():
        raise AssertionError("atspi_available() must not be consulted on darwin")
    import actions.grounding.resolver as R
    monkeypatch.setattr(R, "atspi_available", _boom)

    assert focused_editable_name(platform="darwin") == "Search"


def test_windows_returns_none_when_nothing_is_focused(monkeypatch):
    import actions.grounding.windows as W
    monkeypatch.setattr(
        W, "live_walker",
        lambda scope="active": iter([UINode("Save", "Button", 0, 0, 10, 10, _PLAIN)]))
    assert focused_editable_name(platform="win32") is None


def test_linux_path_still_gates_on_atspi_available(monkeypatch):
    """The Linux behaviour this fix must not disturb: no live AT-SPI bus
    means an honest 'I don't know', not a guess."""
    import actions.grounding.resolver as R
    monkeypatch.setattr(R, "atspi_available", lambda: False)
    assert focused_editable_name(platform="linux") is None
