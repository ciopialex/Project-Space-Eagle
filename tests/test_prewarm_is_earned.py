"""Do not launch a browser because someone said the word "click".

The user watched pages spawn, sit there doing nothing, and close again. No
agent was working on them. His own log explains it:

    [Intent] web.click (conf=0.8, matched='click') → pre-warming: web.open
    [Intent] web.open  (conf=0.8, matched='go to') → pre-warming: web.open

Speculation fires on a substring. He said "why can't you click" and "I don't
know then it feels like" — and a browser started, was never used, and was
released. The word appearing in a sentence is not a request for the web.

I made this worse. Before the transcription fix, `_speculate` was matching
against "Sa ve word rea dy" and hitting nothing, so the false spawns were
rare. Fixing the transcript switched the speculation on properly, and the
mis-fires with it — a fix uncovering a bug that was already there.

The warm-up buys ~310ms of browser start. That is worth having, and it is not
worth a browser per conversational mention. So it must be EARNED: something
that actually looks like a destination — a domain, a url, or "go to <site>" —
rather than a lone verb.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.intent import worth_warming  # noqa: E402


# ── the sentences that wrongly spawned a browser ────────────────────────────

def test_the_word_click_alone_is_not_a_web_request():
    assert worth_warming("why can't you click on it") is False


def test_talking_about_clicking_is_not_a_web_request():
    assert worth_warming("you should have clicked on one of the results") is False


def test_a_bare_go_to_with_no_destination_is_not_enough():
    assert worth_warming("let's go to the next thing") is False


def test_ordinary_conversation_never_warms():
    for said in ("how are you doing", "I'm having a rough time honestly",
                 "thank you that was actually great",
                 "can you check my system stats"):
        assert worth_warming(said) is False, said


# ── the sentences that should ────────────────────────────────────────────────

def test_a_domain_is_a_destination():
    assert worth_warming("go on makerworld.com and find a phone stand") is True


def test_a_full_url_is_a_destination():
    assert worth_warming("open https://en.wikipedia.org/wiki/Motherboard") is True


def test_go_to_a_named_site_is_a_destination():
    assert worth_warming("go to makerworld and download a laptop stand") is True
    assert worth_warming("open youtube and play something") is True


def test_a_bare_well_known_site_counts():
    assert worth_warming("search wikipedia for motherboards") is True


# ── it must stay cheap; it runs inside the end-of-turn window ───────────────

def test_it_costs_almost_nothing():
    import time
    t = time.perf_counter()
    for _ in range(2000):
        worth_warming("go on makerworld.com and find a phone stand")
    per = (time.perf_counter() - t) / 2000 * 1e6
    assert per < 200, f"{per:.0f}us per call is too slow for the turn window"


def test_empty_input_is_safe():
    assert worth_warming("") is False
    assert worth_warming(None) is False
