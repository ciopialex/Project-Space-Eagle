"""If the step says "search for X", find the search box yourself.

From a real session, on makerworld:

    Stuck on: Search for 'phone stand'.
    Tried web_type   (No field on this page matches 'Search fo)
         user_type   (Error: Page.fill: Element is not ...)
         screen_type (refusing to type: nothing identifiable has focus)
         press_keys  (refusing to type: nothing identifiable has focus)

The user's reaction was the correct one: how hard is it to see the search bar?
It is not hard — the page was open and its controls were readable. Nothing
looked for a text field. `web_type` asked the grounder for a control NAMED
"Search for 'phone stand'", which does not exist; `user_type` matched
something that was not an input and Playwright refused to fill it.

"Search for X" is two actions wearing one step: focus a field, then type. A
person does not need to be told which field — they look for the one you can
type in. So when a typing step names no target and nothing has focus, pick
the text field off the page.

Preference order matters: a searchbox beats a plain textbox, and both beat
anything not editable. Picking wrong here types someone's query into a
newsletter signup.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.mission_runners import best_text_field  # noqa: E402
from actions.grounding.web.page import WebNode  # noqa: E402

TYPABLE = frozenset({"VISIBLE", "SHOWING", "ENABLED", "EDITABLE"})
SHOWN = frozenset({"VISIBLE", "SHOWING", "ENABLED"})


def _n(name, role, states=TYPABLE, w=200):
    return WebNode(name=name, role=role, left=0, top=0, width=w, height=30,
                   ref=f"e{abs(hash(name)) % 999}", states=states)


def test_a_searchbox_is_chosen():
    page = [_n("Home", "link", SHOWN), _n("Search models", "searchbox")]
    assert best_text_field(page).name == "Search models"


def test_a_searchbox_beats_a_plain_textbox():
    """Typing a search query into a newsletter box is the failure to avoid."""
    page = [_n("Email for newsletter", "textbox"), _n("Search", "searchbox")]
    assert best_text_field(page).name == "Search"


def test_a_textbox_is_used_when_there_is_no_searchbox():
    page = [_n("Home", "link", SHOWN), _n("Your query", "textbox")]
    assert best_text_field(page).name == "Your query"


def test_a_field_that_is_not_editable_is_never_chosen():
    """A disabled or read-only input accepts nothing and would report success
    for text that went nowhere."""
    page = [_n("Locked", "textbox", SHOWN)]
    assert best_text_field(page) is None


def test_a_hidden_field_is_never_chosen():
    page = [_n("Hidden search", "searchbox", frozenset({"ENABLED", "EDITABLE"}))]
    assert best_text_field(page) is None


def test_a_page_with_no_fields_yields_nothing_rather_than_a_guess():
    page = [_n("Home", "link", SHOWN), _n("Download", "button", SHOWN)]
    assert best_text_field(page) is None


def test_an_empty_page_is_safe():
    assert best_text_field([]) is None


def test_a_named_search_field_wins_over_an_unnamed_one():
    """Two searchboxes happen — a header one and a filter one. The one that
    says 'search' is the one a person would use."""
    page = [_n("", "searchbox"), _n("Search models", "searchbox")]
    assert best_text_field(page).name == "Search models"


def test_the_wider_field_wins_when_nothing_else_separates_them():
    """A 40px filter box is not the main search bar."""
    page = [_n("", "searchbox", w=40), _n("", "searchbox", w=600)]
    assert best_text_field(page).width == 600
