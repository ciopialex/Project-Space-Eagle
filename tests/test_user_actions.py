"""The one implementation "click/type/open/look on the visible browser"
now uses, whether the caller is a mission step or a single ad-hoc
browser_control action call.

Before this file existed, this logic lived only inside
core/mission_runners.py's _user_click/_user_type/_user_open/_user_look,
reachable only from inside a running mission. A live babysitting session
on 2026-08-13/14 showed the cost: a standalone "click the search bar"
voice command had no DOM-exact option at all, and fell through to
computer_control's pixel/AT-SPI path — unrelated code, its own bugs
(broken AT-SPI here), no relation to what the model was actually looking
at.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.grounding.web import user_actions as UA  # noqa: E402
from core.tool_result import ToolResult  # noqa: E402


class _Node:
    def __init__(self, name, role="button", states=frozenset({"EDITABLE", "VISIBLE"}),
                width=100, ref="e0"):
        self.name, self.role, self.states, self.width, self.ref = (
            name, role, states, width, ref)


class _Grounder:
    def __init__(self, node=None):
        self._node = node

    def find_node(self, description):
        return self._node


class _Port:
    def __init__(self, nodes=(), typed_into="", url=""):
        self._nodes = nodes
        self.clicked_ref = None
        self.filled = None
        self._typed_into = typed_into
        self._url = url

    def collect(self):
        return [{"ref": f"e{i}", "name": n.name, "role": n.role,
                 "states": list(n.states), "left": 0, "top": 0,
                 "width": n.width, "height": 20}
                for i, n in enumerate(self._nodes)]

    def url(self):
        return self._url

    def click(self, ref):
        self.clicked_ref = ref

    def fill(self, ref, text):
        self.filled = (ref, text)

    def type_into_focused(self, text):
        return self._typed_into


def test_user_click_with_no_window_open_is_an_explicit_failure(monkeypatch):
    monkeypatch.setattr(UA, "_user_window", lambda: (None, None))
    r = UA.user_click("the Search button")
    assert isinstance(r, ToolResult) and r.ok is False
    assert "no browser window" in r.message


def test_user_click_finds_and_clicks_the_named_control(monkeypatch):
    node = _Node("Search")
    port = _Port()
    monkeypatch.setattr(UA, "_user_window", lambda: (port, _Grounder(node)))
    r = UA.user_click("Search")
    assert r.ok is True
    assert port.clicked_ref == "e0"


def test_user_click_names_what_is_actually_on_the_page_when_nothing_matches(monkeypatch):
    port = _Port(nodes=[_Node("Home"), _Node("Download")])
    monkeypatch.setattr(UA, "_user_window", lambda: (port, _Grounder(None)))
    r = UA.user_click("Search")
    assert r.ok is False
    assert "Home" in r.message and "Download" in r.message


def test_user_type_with_no_description_types_into_whatever_is_focused(monkeypatch):
    port = _Port(typed_into="Search field")
    monkeypatch.setattr(UA, "_user_window", lambda: (port, _Grounder(None)))
    r = UA.user_type(None, "watch stand")
    assert r.ok is True
    assert "Search field" in r.message


def test_user_type_with_a_description_fills_that_named_field(monkeypatch):
    node = _Node("Search input", role="textbox")
    port = _Port(nodes=[node])
    monkeypatch.setattr(UA, "_user_window", lambda: (port, _Grounder(node)))
    r = UA.user_type("Search input", "watch stand")
    assert r.ok is True
    assert port.filled == ("e0", "watch stand")


def test_user_look_reports_how_many_controls_are_on_the_page(monkeypatch):
    port = _Port(nodes=[_Node("A"), _Node("B"), _Node("C")])
    monkeypatch.setattr(UA, "_user_window", lambda: (port, None))
    r = UA.user_look()
    assert r.ok is True
    assert "3" in r.message


def test_user_look_reports_failure_when_the_page_has_no_controls(monkeypatch):
    """`user_look` is a rung on mission_ladder's "read" ladder
    (web_look, user_look, screen_look); the ladder stops at the first rung
    that reports ok=True. A window with zero controls (blank/failed-to-load
    page) must report failure, or the ladder never falls through to
    screen_look, the visual fallback that exists exactly for this case —
    matching the pre-refactor `ok=bool(nodes)` behavior."""
    port = _Port(nodes=[])
    monkeypatch.setattr(UA, "_user_window", lambda: (port, None))
    r = UA.user_look()
    assert r.ok is False


def test_user_look_recognizes_a_bot_wall_instead_of_reporting_a_thin_page(monkeypatch):
    """Live, 2026-08-14: browser_control's DOM path has no bot-wall
    awareness at all — unlike web_agency, which already checks
    bot_wall_reason() and tells the user a human-verification page was
    served instead of the real site. Without it, a Cloudflare "Just a
    moment..." interstitial reads as an ordinary thin page and the model
    is left guessing at field names that were never really there."""
    port = _Port(nodes=[_Node("Just a moment...", role="heading"),
                        _Node("Cloudflare", role="link"),
                        _Node("Ray ID: 8f2a1c9", role="text")],
                url="https://makerworld.com/en")
    monkeypatch.setattr(UA, "_user_window", lambda: (port, None))
    r = UA.user_look()
    assert r.ok is False
    assert "block" in r.message.lower() or "human" in r.message.lower()


def test_user_look_does_not_misread_an_ordinary_thin_page_as_a_bot_wall(monkeypatch):
    port = _Port(nodes=[_Node("Home"), _Node("About"), _Node("Contact")],
                url="https://example.test")
    monkeypatch.setattr(UA, "_user_window", lambda: (port, None))
    r = UA.user_look()
    assert r.ok is True


def test_user_open_with_no_window_is_an_explicit_failure(monkeypatch):
    monkeypatch.setattr(UA, "_user_window", lambda create=False: (None, None))
    r = UA.user_open("https://example.test")
    assert r.ok is False
