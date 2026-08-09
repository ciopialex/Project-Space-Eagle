"""A link named "Home" beats a navigation bar that contains the word Home.

Measured on youtube.com: asking for "Acasă" resolved to

    [navigation] "Acasă Shorts Abonamente Tu Istoric Conectează-te pentru a…"

instead of

    [link] "Acasă"

A container's accessible name is every child's text concatenated, so it
matches any query that any of its children would match — and it is bigger, so
it often wins. Navigation bars, banners and main regions exist on essentially
every site, which made this a reliability problem everywhere rather than a
YouTube quirk: the click then lands on a wrapper and does nothing.

The rule: when something is being CLICKED, a control you can actually click
beats a region that merely contains one.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.grounding.web.page import WebNode  # noqa: E402


def _node(name, role):
    return WebNode(name=name, role=role, left=0, top=0, width=90, height=20,
                   ref="e1", states=frozenset({"VISIBLE", "ENABLED"}))


def test_a_link_beats_the_navigation_that_contains_it():
    from actions.grounding.web.grounder import prefer_actionable
    nav = _node("Acasă Shorts Abonamente Tu Istoric Conectează-te", "navigation")
    link = _node("Acasă", "link")
    assert prefer_actionable(link) > prefer_actionable(nav)


def test_containers_are_ranked_below_every_real_control():
    from actions.grounding.web.grounder import prefer_actionable
    for container in ("navigation", "banner", "main", "region", "group",
                      "contentinfo", "complementary", "list"):
        for control in ("link", "button", "textbox", "checkbox", "combobox"):
            assert prefer_actionable(_node("x", control)) > \
                   prefer_actionable(_node("x", container)), (control, container)


def test_two_real_controls_are_not_reordered():
    """This must break ties, not invent a new ranking between genuine
    candidates — the name match already decides those."""
    from actions.grounding.web.grounder import prefer_actionable
    assert prefer_actionable(_node("Search", "button")) == \
           prefer_actionable(_node("Search", "link"))


def test_an_exact_name_beats_a_name_that_merely_contains_it():
    """The other half. A container is not the only thing that swallows a
    query — a long link can too."""
    from actions.grounding.web.grounder import prefer_exact
    assert prefer_exact("Acasă", _node("Acasă", "link")) > \
           prefer_exact("Acasă", _node("Acasă Shorts Abonamente", "link"))


def test_a_visible_control_beats_a_hidden_one_with_a_better_name():
    """The regression this caused. Preferring an exact name pulled hidden
    controls named exactly "Search" ahead of the visible button that had been
    working — click reliability fell from 66% to 33% across the benchmark in
    one commit. Nothing about a name matters if the thing cannot be clicked."""
    from actions.grounding.web.grounder import prefer_visible
    hidden = WebNode(name="Search", role="button", left=0, top=0, width=80,
                     height=20, ref="e1", states=frozenset({"ENABLED"}))
    shown = WebNode(name="Search the site", role="button", left=0, top=0,
                    width=80, height=20, ref="e2",
                    states=frozenset({"VISIBLE", "ENABLED"}))
    assert prefer_visible(shown) > prefer_visible(hidden)


def test_visibility_outranks_both_other_preferences():
    """Order matters: visible first, then a real control, then an exact name."""
    import inspect
    from actions.grounding.web import grounder
    src = inspect.getsource(grounder.WebGrounder._prefer_among_ties)
    assert "prefer_visible" in src
    assert src.index("prefer_visible") < src.index("prefer_actionable")
