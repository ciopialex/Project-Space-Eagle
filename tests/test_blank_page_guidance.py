"""The eagle's browser is NOT the user's browser, and it has to say so.

From a live session. The model opened a store page with browser_control:

    [Browser] Opened in chrome: https://us.store.bambulab.com/collections/p-series

then asked web_agency to click a product on it:

    [Tool] ✗ web_agency  why: No control on this page matches 'P1S link'.
           next: Call action='look' to see what is actually on the page.

It called look. It got:

    why: Could not read any controls on about:blank.

Those are two different browsers. browser_control drives the USER'S Chrome
through `webbrowser`; web_agency drives its own Playwright browser, which had
never been navigated anywhere. The eagle spent the rest of the session trying
to click a page it was not looking at, then fell back to screenshots and
vision, and the user concluded the whole feature was broken.

The guidance was the failure. "Look to see what is on the page" is useless
advice when the problem is that the eagle is on a blank page in a different
browser. It has to name that, precisely, and say what to do.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actions.web_agency as W  # noqa: E402


def _blank(monkeypatch, url="about:blank", nodes=()):
    monkeypatch.setattr(W, "_current_url", lambda _p: url)
    monkeypatch.setattr(W, "_current_nodes", lambda _p: list(nodes))


def test_clicking_on_a_blank_page_says_the_browser_was_never_navigated(monkeypatch):
    _blank(monkeypatch)
    result = W._no_such_control("P1S link", object())
    assert result.ok is False
    low = (result.message + " " + result.guidance).lower()
    assert "about:blank" in low or "not on any page" in low
    assert "action='open'" in result.guidance, "did not say how to fix it"


def test_it_names_the_other_browser_as_the_likely_cause(monkeypatch):
    """The model's actual mistake. Saying only "open a page" invites it to
    call browser_control again, which is what already failed."""
    _blank(monkeypatch)
    guidance = W._no_such_control("P1S link", object()).guidance.lower()
    assert "browser_control" in guidance
    assert "own browser" in guidance or "different browser" in guidance


def test_a_real_page_gets_advice_about_the_page_not_the_browser(monkeypatch):
    """When the eagle IS on the page, the blank-browser explanation would be
    a lie. It should talk about the description instead — and it now offers
    the real names rather than costing a round trip on `look`."""
    class N:
        name = "Bambu Lab P1S 3D Printer"
    _blank(monkeypatch, url="https://eu.store.bambulab.com/collections/p-series",
           nodes=(N(), N(), N()))
    result = W._no_such_control("P1S link", object())
    guidance = result.guidance.lower()
    assert "about:blank" not in guidance and "browser_control" not in guidance
    assert "p1s 3d printer" in guidance or "look" in guidance


def test_a_real_page_offers_the_names_it_actually_has(monkeypatch):
    """Telling the model to "call look" costs a whole extra round trip when
    the names are already in hand."""
    class N:
        def __init__(self, name): self.name = name
    _blank(monkeypatch, url="https://eu.store.bambulab.com/collections/p-series",
           nodes=(N("Bambu Lab P1S 3D Printer"), N("Bambu Lab P2S 3D Printer")))
    result = W._no_such_control("P1S link", object())
    assert "P2S" in result.guidance or "P1S 3D Printer" in result.guidance


def test_a_failed_click_returns_guidance_instead_of_crashing():
    """`page` was referenced before it was assigned, so EVERY failed click
    raised UnboundLocalError and surfaced as "The web tool hit an unexpected
    error" — losing the guidance the whole fix exists to deliver. Caught by
    running a click that finds nothing, which is the commonest failure there
    is."""
    import actions.grounding.web.browser as B
    import actions.web_agency as WA

    class Grounder:
        def available(self): return True
        def find_node(self, description): return None
        def resolve(self, description, prefer=None): return None, ()
        def hit_test(self, x, y): return None

    class Browser:
        def page(self): return None

    result = WA._click(Browser(), Grounder(), "something that is not there")
    assert result is not None
    assert "unexpected error" not in result.message.lower()
    assert result.guidance, "the failure carried no next step"
