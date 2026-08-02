"""The Windows and macOS grounders, and platform dispatch.

The COM/PyObjC bindings in windows.py and macos.py have not been executed on
their target operating systems — this machine is Linux. What these tests pin
is everything above the binding: role normalization, matching, bounds sanity,
state mapping, and that the right grounder is selected per platform.
"""
import pytest

from actions.grounding.base import UINode
from actions.grounding.macos import MacGrounder, permission_status
from actions.grounding.resolver import platform_hit_test, structural_grounder
from actions.grounding.windows import WindowsGrounder

LIVE_WIN = frozenset({"ENABLED", "SENSITIVE", "VISIBLE", "SHOWING"})


def _win_nodes():
    return [
        UINode("Cancel", "Button", 100, 500, 80, 30, LIVE_WIN),
        UINode("Save",   "Button", 200, 500, 80, 30, LIVE_WIN),
        UINode("Save",   "MenuItem", 0, 10, 60, 20, LIVE_WIN),
        UINode("Search", "Edit", 300, 100, 200, 30, LIVE_WIN),
        UINode("Hidden", "Button", 0, 0, 0, 0, LIVE_WIN),
    ]


def _mac_nodes():
    return [
        UINode("Cancel", "AXButton", 100, 500, 80, 30, LIVE_WIN),
        UINode("Save",   "AXButton", 200, 500, 80, 30, LIVE_WIN),
        UINode("Save",   "AXMenuItem", 0, 10, 60, 20, LIVE_WIN),
        UINode("Search", "AXTextField", 300, 100, 200, 30, LIVE_WIN),
    ]


# ---- Windows ------------------------------------------------------------

def test_windows_finds_a_button_by_uia_control_type():
    g = WindowsGrounder(walker=_win_nodes)
    el = g.find("the Save button")
    assert el is not None
    assert el.center == (240, 515)
    assert el.source == "uia"


def test_windows_role_hint_disambiguates():
    g = WindowsGrounder(walker=_win_nodes)
    assert g.find("the Save menu").center == (30, 20)
    assert g.find("the Save button").center == (240, 515)


def test_windows_finds_an_edit_control_as_a_field():
    assert WindowsGrounder(walker=_win_nodes).find("the Search field") is not None


def test_windows_skips_zero_sized_controls():
    assert WindowsGrounder(walker=_win_nodes).find("Hidden button") is None


def test_windows_carries_states_through():
    el = WindowsGrounder(walker=_win_nodes).find("the Save button")
    assert el.has("ENABLED") and el.has("SENSITIVE")


def test_windows_never_raises_when_the_backend_explodes():
    def boom():
        raise OSError("no COM")
    g = WindowsGrounder(walker=boom)
    assert g.find("anything") is None
    assert g.available() is False


def test_windows_grounder_is_fast_tier():
    assert WindowsGrounder(walker=_win_nodes).cost == "fast"


# ---- macOS --------------------------------------------------------------

def test_macos_finds_a_button_by_ax_role():
    el = MacGrounder(walker=_mac_nodes).find("the Save button")
    assert el is not None
    assert el.center == (240, 515)
    assert el.source == "ax"


def test_macos_role_hint_disambiguates():
    g = MacGrounder(walker=_mac_nodes)
    assert g.find("the Save menu").center == (30, 20)
    assert g.find("the Save button").center == (240, 515)


def test_macos_finds_a_text_field():
    assert MacGrounder(walker=_mac_nodes).find("the Search field") is not None


def test_macos_never_raises_when_the_backend_explodes():
    def boom():
        raise OSError("not trusted")
    assert MacGrounder(walker=boom).find("anything") is None


def test_macos_permission_status_never_raises_and_explains_itself():
    status = permission_status()
    assert set(status) >= {"available", "trusted", "hint"}
    if not status["trusted"]:
        assert status["hint"]


def test_macos_grounder_is_fast_tier():
    assert MacGrounder(walker=_mac_nodes).cost == "fast"


# ---- platform dispatch --------------------------------------------------

@pytest.mark.parametrize("platform,expected", [
    ("win32", "uia"),
    ("darwin", "ax"),
    ("linux", "atspi"),
])
def test_the_right_grounder_is_selected_per_platform(platform, expected):
    g = structural_grounder(platform)
    assert g is not None
    assert g.name == expected


def test_every_platform_gets_a_hit_test():
    """Without one, 'receives events' can never pass and every click times
    out — a bug this codebase has already shipped once."""
    for platform in ("win32", "darwin", "linux"):
        assert callable(platform_hit_test(platform)), platform


def test_an_unknown_platform_falls_back_rather_than_crashing():
    assert structural_grounder("plan9") is not None


def test_default_resolver_puts_structure_before_vision():
    from actions.grounding import default_resolver
    names = [g.name for g in default_resolver()._grounders]
    assert names[-1] == "vision"
    assert len(names) >= 2
