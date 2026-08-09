"""A bot challenge is not a page with one control on it.

From a live session:

    [Tool] ✓ web_agency (1861ms)
      result: 1 controls on https://bambulab.com/?__cf_chl_rt_tk=…
      - <div class="h2"><span id="challenge-error-text">Enable JavaScript and
        cookies to continue</span></div> (main)

Reported as SUCCESS. The model had no way to know it had been blocked, so it
spent the next two turns trying to click things that were not there, then fell
back to clicking the user's physical screen.

Measured: the challenge does not clear. Sixteen seconds, still two controls,
still "Performing security verification". Headless Chrome is hard-blocked on
that domain — while eu.store.bambulab.com serves 69 controls happily. So the
honest answer exists and is useful: this site refuses automated browsers, here
is what to do instead.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.grounding.web.handoff import bot_wall_reason  # noqa: E402


class N:
    def __init__(self, name, role="main"):
        self.name, self.role = name, role


CHALLENGES = [
    ([N("Enable JavaScript and cookies to continue")], "https://bambulab.com/?__cf_chl_rt_tk=abc"),
    ([N("Performing security verification")], "https://example.com/"),
    ([N("Just a moment…")], "https://example.com/"),
    ([N("Checking your browser before accessing")], "https://example.com/"),
    ([N("Verify you are human")], "https://example.com/"),
]


def test_a_challenge_page_is_recognised():
    for nodes, url in CHALLENGES:
        assert bot_wall_reason(nodes, url), f"missed: {nodes[0].name}"


def test_the_url_alone_is_enough_when_the_page_is_unreadable():
    """The challenge often renders nothing at all. The cf_chl parameter is
    still there and still conclusive."""
    assert bot_wall_reason([], "https://bambulab.com/?__cf_chl_rt_tk=xyz")


def test_an_ordinary_page_is_not_a_challenge():
    ordinary = [N("Bambu Lab P2S 3D Printer", "link"),
                N("Add to cart", "button"), N("Search", "textbox")]
    assert bot_wall_reason(ordinary, "https://eu.store.bambulab.com/collections/p-series") == ""


def test_a_page_that_merely_mentions_security_is_not_a_challenge():
    """A shop selling security cameras, an article about verification. The
    check must key on the challenge's own shape, not on a scary word."""
    nodes = [N("Security cameras", "link"), N("Verify your order", "button"),
             N("Add to cart", "button"), N("Checkout", "button"),
             N("Search", "textbox"), N("Account", "link")]
    assert bot_wall_reason(nodes, "https://shop.example.com/security") == ""


def test_the_reason_says_what_to_do_instead():
    reason = bot_wall_reason([N("Enable JavaScript and cookies to continue")],
                             "https://bambulab.com/")
    low = reason.lower()
    assert "automated" in low or "bot" in low
