import pytest

from actions.grounding.base import Element, match_score


def test_element_center_is_midpoint_of_bounds():
    el = Element.from_bounds("Save", "push button", 100, 200, 80, 40, "atspi")
    assert el.center == (140, 220)
    assert el.x == 140
    assert el.y == 220


def test_element_is_frozen():
    el = Element.from_bounds("Save", "push button", 0, 0, 10, 10, "atspi")
    with pytest.raises(Exception):
        el.name = "Cancel"


def test_element_bounds_tuple():
    el = Element.from_bounds("Save", "push button", 100, 200, 80, 40, "atspi")
    assert el.bounds == (100, 200, 80, 40)


def test_element_states_default_empty_and_has_reports_membership():
    plain = Element.from_bounds("Save", "push button", 0, 0, 10, 10, "atspi")
    assert plain.states == frozenset()
    assert plain.has("ENABLED") is False

    live = Element.from_bounds("Save", "push button", 0, 0, 10, 10, "atspi",
                               states=frozenset({"ENABLED", "SHOWING"}),
                               value="hello")
    assert live.has("ENABLED") is True
    assert live.has("EDITABLE") is False
    assert live.value == "hello"


def test_match_score_exact_name_and_role():
    assert match_score("the Save button", "Save", "push button") == pytest.approx(1.0)


def test_match_score_name_only_without_role_hint():
    assert match_score("Save", "Save", "push button") == pytest.approx(0.8)


def test_match_score_rejects_wrong_name():
    assert match_score("the Save button", "Cancel", "push button") == 0.0


def test_match_score_partial_name_overlap():
    # "sign in" vs a button named "Sign In Now" -> both description tokens present
    assert match_score("sign in button", "Sign In Now", "push button") == pytest.approx(1.0)


def test_match_score_empty_inputs_are_zero():
    assert match_score("", "Save", "push button") == 0.0
    assert match_score("Save", "", "push button") == 0.0


def test_match_score_stopwords_alone_do_not_match():
    assert match_score("the button", "Save", "push button") == 0.0


def test_match_score_role_hint_disambiguates():
    # Same name, different roles - the spoken role word decides.
    assert match_score("the Save menu", "Save", "menu item") == pytest.approx(1.0)
    assert match_score("the Save menu", "Save", "push button") == pytest.approx(0.8)


def test_match_score_when_the_name_is_itself_a_role_noun():
    """"the Menu button", "the Search field" - the name IS the role word.

    Stripping role nouns left nothing to match on, so these scored 0.0 and
    fell through to a 7.9s vision lookup. Found by running it live.
    """
    assert match_score("the Menu button", "Menu", "toggle button") > 0.5
    assert match_score("the Search field", "Search", "text") > 0.5
    assert match_score("the Files tab", "Files", "page tab") > 0.5


def test_role_noun_fallback_still_rejects_wrong_names():
    assert match_score("the Menu button", "Cancel", "toggle button") == 0.0
