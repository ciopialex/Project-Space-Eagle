from actions.grounding.base import Element
from actions.grounding.actionability import (
    ACTION_REQUIREMENTS, check, is_editable, is_enabled, is_stable,
    is_visible, receives_events,
)

LIVE = frozenset({"ENABLED", "SENSITIVE", "VISIBLE", "SHOWING"})


def _el(states=LIVE, left=100, top=200, width=80, height=40, name="Save"):
    return Element.from_bounds(name, "push button", left, top, width, height,
                               "atspi", states=states)


def test_visible_requires_size_and_showing():
    assert is_visible(_el()) is True
    assert is_visible(_el(width=0)) is False
    assert is_visible(_el(states=frozenset({"ENABLED", "SENSITIVE"}))) is False


def test_enabled_requires_both_enabled_and_sensitive():
    assert is_enabled(_el()) is True
    assert is_enabled(_el(states=frozenset({"ENABLED"}))) is False
    assert is_enabled(_el(states=frozenset({"VISIBLE", "SHOWING"}))) is False


def test_editable_requires_enabled_plus_editable_state():
    assert is_editable(_el()) is False
    assert is_editable(_el(states=LIVE | {"EDITABLE"})) is True
    assert is_editable(_el(states=frozenset({"EDITABLE"}))) is False


def test_stable_compares_bounds_across_two_reads():
    a = _el()
    assert is_stable(a, _el()) is True
    assert is_stable(a, _el(left=105)) is False
    assert is_stable(None, a) is False
    assert is_stable(a, None) is False


def test_receives_events_when_hit_test_returns_the_same_element():
    assert receives_events(_el(), lambda x, y: _el()) is True


def test_receives_events_false_when_something_overlays_it():
    assert receives_events(_el(), lambda x, y: _el(name="Modal Dialog")) is False


def test_receives_events_false_when_hit_test_finds_nothing():
    assert receives_events(_el(), lambda x, y: None) is False


def test_receives_events_survives_an_exploding_hit_test():
    def boom(x, y):
        raise RuntimeError("no display")
    assert receives_events(_el(), boom) is False


def test_click_requires_the_playwright_four():
    assert ACTION_REQUIREMENTS["click"] == (
        "visible", "stable", "receives_events", "enabled")


def test_fill_does_not_require_stable_or_hit_test():
    assert ACTION_REQUIREMENTS["fill"] == ("visible", "enabled", "editable")


def test_press_requires_nothing():
    assert ACTION_REQUIREMENTS["press"] == ()


def test_check_reports_the_first_failing_check_by_name():
    disabled = _el(states=frozenset({"VISIBLE", "SHOWING"}))
    ok, failed = check("click", disabled, previous=disabled,
                       hit_test=lambda x, y: disabled)
    assert ok is False
    assert failed == "enabled"


def test_check_passes_when_everything_holds():
    el = _el()
    ok, failed = check("click", el, previous=el, hit_test=lambda x, y: _el())
    assert ok is True
    assert failed == ""


def test_check_on_unknown_action_requires_nothing():
    ok, failed = check("teleport", _el(width=0))
    assert ok is True
    assert failed == ""
