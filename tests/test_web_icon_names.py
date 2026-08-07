"""Controls whose name lives on a descendant, against a real browser.

The rest of the collector's naming is tested by asserting on the *text* of
COLLECT_JS, which proves the string contains what we think and proves nothing
about what Chromium does with it. Name resolution is the one part where that
gap matters most: an element the collector cannot name is dropped entirely, so
a missing fallback does not degrade the eagle's perception, it deletes it.

Measured on digi24.ro before this fallback: 74 of 309 interactive controls had
no accessible name. Every one was invisible to the eagle while being perfectly
clickable by a human, which is the exact gap the project's premise is about —
"whatever a human with a pair of eyes can do".
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("playwright", reason="playwright not installed")

from actions.grounding.web.browser import EagleBrowser        # noqa: E402
from actions.grounding.web.page import nodes_from_records     # noqa: E402

PAGE_URL = (Path(__file__).parent / "fixtures" / "web"
            / "icon_controls.html").resolve().as_uri()


@pytest.fixture(scope="module")
def names(tmp_path_factory):
    b = EagleBrowser(headless=True,
                     profile_dir=tmp_path_factory.mktemp("icon-profile"))
    b.start()
    if not b.running:
        pytest.skip(f"could not launch chromium: {b.last_error}")
    try:
        b.goto(PAGE_URL)
        page = b.page()
        assert page is not None
        # (name, role) pairs, not names alone. An <img alt="Home"> inside a
        # link is collected as its own `img` node, so asserting only that
        # "Home" appears somewhere passes while the LINK - the thing that has
        # to be clicked - is still nameless. That near-miss was live here.
        yield [(n.name, n.role) for n in nodes_from_records(page.collect())]
    finally:
        b.close()


@pytest.mark.parametrize("expected,role", [
    ("Home", "link"),             # <a><img alt>
    ("Shopping cart", "link"),    # <a><img title>
    ("Your profile", "link"),     # <a><span aria-label>
    ("Search", "button"),         # <button><svg aria-label>
    ("Close dialog", "button"),   # <button><svg><title>
    ("RSS feed", "link"),         # <a><i title>
])
def test_an_icon_control_is_named_from_its_descendant(names, expected, role):
    assert (expected, role) in names, (
        f"({expected!r}, {role!r}) not among {names}")


@pytest.mark.parametrize("winner", ["Outer wins", "Own title wins", "Real text"])
def test_the_elements_own_name_still_wins(names, winner):
    """The fallback is a last resort. If it outranked the element's own label
    the collector would start renaming controls that were already correct.

    Asserted on the LINK specifically: a descendant <img> may legitimately be
    collected under its own name, so the question is never "does this string
    appear" but "is the clickable thing called the right thing"."""
    assert (winner, "link") in names, f"{winner!r} is not the link's name: {names}"


def test_nothing_is_invented_for_a_genuinely_nameless_control(names):
    """The other direction, and the one that makes this fallback safe to have.
    A decorative `alt=""` is a deliberate statement that the image is not a
    label; honouring it is the difference between reading a page and guessing
    at one. A fabricated name would let the grounder match a description to a
    control that does not do what the name implies."""
    for junk in ("i.png", "icon", "nameless1", "nameless2", "nameless3"):
        assert not any(junk in n for n, _r in names), (
            f"invented a name containing {junk!r}: {names}")


def test_whitespace_only_labels_do_not_count(names):
    assert not any(n.strip() == "" for n, _r in names)
