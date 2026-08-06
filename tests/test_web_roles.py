"""ARIA is a fourth accent, not a fourth language.

`match_score` understands AT-SPI role names. Windows and macOS already
normalize into them. The web now does too, so "the Save button" means the same
thing whether Save is a GTK widget or a <button>.
"""
from actions.grounding.base import UINode
from actions.grounding.roles import WEB, best_match, normalize


def test_aria_button_becomes_the_canonical_push_button():
    assert normalize("button", WEB) == "push button"


def test_the_input_family_maps_onto_text_roles():
    assert normalize("textbox", WEB) == "text"
    assert normalize("searchbox", WEB) == "text"
    assert normalize("password", WEB) == "password text"


def test_selection_controls_map_onto_their_atspi_names():
    assert normalize("checkbox", WEB) == "check box"
    assert normalize("radio", WEB) == "radio button"
    assert normalize("combobox", WEB) == "combo box"
    assert normalize("switch", WEB) == "toggle button"


def test_structure_roles_map_too():
    assert normalize("link", WEB) == "link"
    assert normalize("tab", WEB) == "page tab"
    assert normalize("menuitem", WEB) == "menu item"
    assert normalize("img", WEB) == "image"


def test_role_matching_is_case_and_whitespace_insensitive():
    assert normalize("  BUTTON ", WEB) == "push button"


def test_an_unknown_aria_role_passes_through_lowercased():
    # It still matches on its name; it just earns no role bonus.
    assert normalize("feed", WEB) == "feed"
    assert normalize("", WEB) == ""


def test_best_match_picks_the_button_over_the_link_with_the_same_name():
    nodes = [
        UINode(name="Sign in", role="link", left=0, top=0, width=60, height=20),
        UINode(name="Sign in", role="button", left=0, top=40, width=60, height=20),
    ]
    hit = best_match(nodes, "the Sign in button", platform=WEB)
    assert hit is not None and hit.role == "button"
