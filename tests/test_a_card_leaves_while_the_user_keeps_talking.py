"""A card leaves the island even while the conversation goes on, and Python hears it.

Two defects in one session on 2026-09-24, both at the seam between the voice
loop and the island's state machine:

- The end of every voice turn reset the card's clock to full and its 22.5 s
  ceiling to "now". A turn is the user talking, about anything, so a card
  stayed up for as long as they kept talking. An a3d light card sat on the
  island through a beacon conversation that had nothing to do with it.

- Python holds ambient events back while a card the user asked for is up, and
  learned the card was gone only from `ttl_expire`. A card that left any other
  way left Python holding every ambient event ("the user is reading a a3d
  card they asked for", logged 33 s after the last card).

Driven through the real page in a real QWebEngineView, with the dwell
shortened so the clock runs in a second rather than eight.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import PILL_HTML, evaluate, load, pump  # noqa: E402

CARD = {"title": "Elegoo 7526B5", "printer": "ELEGOO_7526B5", "state": "IDLE"}


def _open_card(view):
    load(view, PILL_HTML)
    evaluate(view, """
        window.__collapsed = 0;
        window.pybridge = {island_collapsed: function(){ window.__collapsed++; },
                           island_trace: function(){}};
        window.pill.registerModule("a3d", "<div>{title} {printer}</div>", "", {});
        window.island.ttlMs = 0;
        window.island.DWELL_OPEN_MS = 1200;
        window.island.apply("a3d", %s);
        1""" % __import__("json").dumps(CARD))
    assert evaluate(view, "window.island.module") == "a3d"


def test_talking_about_something_else_does_not_keep_the_card_up(web_view):
    _open_card(web_view)
    # Six short turns, 0.4 s of talking and 0.4 s between them: 2.4 s of
    # silence in total against a 1.2 s card. The old resume refilled the clock
    # at the end of every turn, so the card was still up after the sixth.
    for _ in range(6):
        evaluate(web_view, "window.island.apply('speaking', {}), 1")
        pump(0.4)
        evaluate(web_view, "window.island.apply('idle', {}), 1")
        pump(0.4)
    pump(0.3)
    assert evaluate(web_view, "window.island.state") == "idle", (
        "the card stayed on the island through a conversation that never "
        "touched it: each turn reset its clock to full")
    assert evaluate(web_view, "window.__collapsed") == 1


def test_a_turn_pauses_the_card_rather_than_ending_it(web_view):
    _open_card(web_view)
    evaluate(web_view, "window.island.apply('speaking', {}), 1")
    pump(2.0)                      # longer than the whole card, but talking
    assert evaluate(web_view, "window.island.module") == "a3d", (
        "the card expired while the eagle was still answering")
    evaluate(web_view, "window.island.apply('idle', {}), 1")
    pump(1.6)
    assert evaluate(web_view, "window.island.state") == "idle"


def test_a_card_that_leaves_without_expiring_still_tells_python(web_view):
    _open_card(web_view)
    evaluate(web_view, "window.island.send('text_return', {}), 1")
    assert evaluate(web_view, "window.island.state") == "idle"
    assert evaluate(web_view, "window.__collapsed") == 1, (
        "the card left by a text answer and Python was never told, so it "
        "went on holding every ambient event for a card nobody could see")
