"""The actionability layer must actually be wired to the actions.

Plan 1 built wait_for and act_and_verify, and then only wired _screen_find —
so the eagle found things faster but still fired clicks into the void and
still claimed success without looking. These tests pin the wiring shut.
"""
import inspect

import actions.computer_control as cc
from actions.grounding.base import Element

LIVE = frozenset({"ENABLED", "SENSITIVE", "VISIBLE", "SHOWING"})


def _el(states=LIVE, left=100, value=""):
    return Element.from_bounds("Save", "push button", left, 200, 80, 40,
                               "atspi", states=states, value=value)


class ScriptedResolver:
    def __init__(self, script):
        self._script = list(script)
        self.calls = 0
        self.last = None

    def find(self, description, fast_only=False):
        self.calls += 1
        self.last = self._script[min(self.calls - 1, len(self._script) - 1)]
        return self.last


def _wire(monkeypatch, script, clicks):
    """Point the click path at a scripted resolver and a recording mouse.

    The fake hit-test mirrors whatever the resolver last returned, which is
    what a real hit-test at an element's own centre does — anything else
    would fail `receives_events` for the wrong reason.
    """
    resolver = ScriptedResolver(script)
    monkeypatch.setattr(cc, "_grounding_deps", lambda: (
        resolver,
        lambda x, y: resolver.last,
        __import__("actions.grounding.verify", fromlist=["x"]).act_and_verify,
        __import__("actions.grounding.waiting", fromlist=["x"]).wait_for,
    ))
    monkeypatch.setattr(cc, "_click",
                        lambda **kw: clicks.append(kw) or "clicked")
    return resolver


# ---- the void-firing bug ------------------------------------------------

def test_does_not_click_a_disabled_element(monkeypatch):
    clicks = []
    disabled = _el(states=frozenset({"VISIBLE", "SHOWING"}))
    _wire(monkeypatch, [disabled], clicks)
    out = cc._screen_click("the Save button", timeout=0.2)
    assert clicks == [], "clicked a greyed-out button"
    assert "Did not click" in out
    assert "enabled" in out


def test_does_not_click_something_that_never_appears(monkeypatch):
    clicks = []
    _wire(monkeypatch, [None], clicks)
    out = cc._screen_click("the Ghost button", timeout=0.2)
    assert clicks == []
    assert "not_found" in out


def test_clicks_once_the_element_settles(monkeypatch):
    clicks = []
    _wire(monkeypatch, [_el(left=100), _el(left=140), _el(left=140),
                        _el(left=140)], clicks)
    out = cc._screen_click("the Save button", timeout=2.0)
    assert len(clicks) == 1
    assert "Clicked" in out


# ---- the honesty bug ----------------------------------------------------

def test_reports_when_nothing_changed_after_clicking(monkeypatch):
    """The old code returned "Clicked X" unconditionally. It must not."""
    clicks = []
    _wire(monkeypatch, [_el(), _el(), _el(), _el()], clicks)
    out = cc._screen_click("the Save button", timeout=2.0)
    assert len(clicks) == 1
    assert "may not have taken effect" in out


def test_reports_when_the_interface_did_change(monkeypatch):
    clicks = []
    _wire(monkeypatch, [_el(), _el(), _el(states=LIVE | {"CHECKED"})], clicks)
    out = cc._screen_click("the Save button", timeout=2.0)
    assert "the interface changed" in out


def test_force_skips_the_checks(monkeypatch):
    clicks = []
    _wire(monkeypatch, [_el(states=frozenset())], clicks)
    out = cc._screen_click("the Save button", force=True, timeout=0.2)
    assert len(clicks) == 1
    assert "Clicked" in out


# ---- robustness ---------------------------------------------------------

def test_missing_description_is_rejected_before_any_click(monkeypatch):
    clicks = []
    monkeypatch.setattr(cc, "_click", lambda **kw: clicks.append(kw))
    assert "needs a 'description'" in cc._screen_click("")
    assert clicks == []


def test_grounding_failure_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("no accessibility stack")
    monkeypatch.setattr(cc, "_grounding_deps", boom)
    assert "Grounding unavailable" in cc._screen_click("the Save button")
    assert "Grounding unavailable" in cc._wait_for_element("the Save button")


# ---- wait_for_element ---------------------------------------------------

def test_wait_for_element_reports_readiness(monkeypatch):
    _wire(monkeypatch, [_el(), _el(), _el()], [])
    out = cc._wait_for_element("the Save button", timeout=2.0)
    assert "is ready at" in out


def test_wait_for_element_names_the_blocking_check(monkeypatch):
    disabled = _el(states=frozenset({"VISIBLE", "SHOWING"}))
    _wire(monkeypatch, [disabled], [])
    out = cc._wait_for_element("the Save button", timeout=0.2)
    assert "blocked on: enabled" in out


# ---- the wiring itself --------------------------------------------------

def test_the_magic_sleep_is_gone():
    """A hardcoded 200ms guess is what wait_for replaced."""
    src = inspect.getsource(cc.computer_control)
    assert "time.sleep(0.2)" not in src


def test_screen_click_goes_through_the_actionability_layer():
    src = inspect.getsource(cc._screen_click)
    assert "act_and_verify" in src


def test_new_actions_are_dispatchable():
    src = inspect.getsource(cc.computer_control)
    for action in ("screen_click", "wait_for_element", "scroll_into_view"):
        assert f'"{action}"' in src
