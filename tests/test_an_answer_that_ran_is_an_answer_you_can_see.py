"""A tool that returned real figures puts something real on the island.

Two defects, both silent, both found by running the binaries and asking what
the card would do with what came back.

`atrade insider` returns the whole Form 4 ledger — 46 filings, $1.06bn net —
and `adapt_insider` turned it into three good card fields. Nothing declared
them in the manifest and nothing drew them, so `_card_is_drawable` counted the
answer as carrying nothing beyond the ticker the question already supplied, and
refused it the screen. Asked after a quote it inherited that card's drawability
and looked fine; asked FIRST, the tool ran, the model spoke, and the island
stayed empty.

`atrade compare` had no adapter at all, and "forwarded unchanged" is not
neutral: the module bundles ticker_a's quote into the comparison, so `price`
and `company_name` are present and the drawable check says yes — while
`write_headline` never runs, because that only happens on the adapter path. The
capsule went up as

    —  —  —   −$3.77 (−1.64%)

three em dashes and a delta belonging to one of the two companies. With no
`ticker` in the payload it also never re-keyed the store, so the comparison
merged into whoever was on the card before it.

Both are the failure the em-dash commits keep addressing, and neither could be
caught by the placeholder contract test: the renderer FILLS these slots, with
dashes. A filled dash and a real figure are the same shape to anything that
only asks whether a slot was filled.

Nothing here needs a page. What is being measured is whether the answer earns
the screen and whether the two capsule rows say anything, which is decided in
Python before a pixel is drawn.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.card_assembly import CardStore  # noqa: E402

DASH = "—"


def _drawable(tool: str, card: dict) -> bool:
    import main
    return main.AethelarkLive._card_is_drawable(tool, card)


def _says_something(card: dict) -> bool:
    """The capsule's two rows carry a real claim, not a placeholder.

    `title` alone is the ticker, which came from the question. `detail` is
    where an answer says what it found.
    """
    detail = str(card.get("detail") or "").strip()
    return bool(detail) and detail not in {DASH, "-", "--", DASH * 2}


#: A real `atrade insider NVDA`, trimmed to the keys the adapter reads.
INSIDER = {
    "ticker": "NVDA", "lookback_days": 180, "filings": 46,
    "sold": {"count": 23, "usd": 1063463454.4}, "bought": {"count": 0, "usd": 0},
    "net_usd": -1063463454.4,
    "latest": {"date": "2026-06-16", "insider": "Musk Elon", "title": "CEO",
               "action": "exercises", "usd": 7094441104.2, "is_10b5_1": False},
}

#: A real `atrade compare NVDA TSLA`. The one-company quote fields are in the
#: payload exactly as the module sends them — they are what fooled the check.
COMPARE = {
    "ticker_a": "NVDA", "ticker_b": "TSLA",
    "composite_a": 53, "composite_b": 49, "winner": "NVDA", "margin": 4,
    "layers_won_a": 2, "layers_won_b": 2,
    "price": 230.36, "previous_close": 234.13, "change": -3.77,
    "change_pct": -1.64, "company_name": "NVIDIA Corp.",
    "day_high": 234.76, "day_low": 229.63, "market_cap": 5562502934738,
}

QUOTE = {
    "ticker": "PLTR", "company_name": "Palantir Technologies Inc.",
    "price": 174.33, "previous_close": 174.43, "change": -0.1,
    "change_pct": -0.06,
}


# ── the insider ledger ───────────────────────────────────────────────────────

def test_an_insider_answer_asked_first_reaches_the_screen():
    """The regression. Nothing else on the card, which is the whole point."""
    card = CardStore().absorb("atrade_insider", INSIDER, {"ticker": "NVDA"})

    assert _drawable("atrade_insider", card), (
        "an insider answer carrying 46 filings and $1.06bn was refused the "
        "screen, so the tool ran and the user saw nothing")
    assert _says_something(card), (
        f"the capsule's second row is {card.get('detail')!r}; the ticker came "
        f"from the question, so a card with only that says nothing")


def test_the_insider_figures_survive_into_the_card():
    card = CardStore().absorb("atrade_insider", INSIDER, {"ticker": "NVDA"})
    assert card["insider_trade_count"] == 46
    assert card["insider_net_usd"] == pytest.approx(-1063463454.4)
    assert "Musk Elon" in card["insider_latest"]


# ── the comparison ───────────────────────────────────────────────────────────

def test_a_comparison_does_not_draw_itself_as_three_em_dashes():
    """The defect exactly: drawable on borrowed fields, headline on none."""
    card = CardStore().absorb("atrade_compare", COMPARE,
                              {"ticker_a": "NVDA", "ticker_b": "TSLA"})

    assert _drawable("atrade_compare", card)
    assert _says_something(card), (
        f"detail is {card.get('detail')!r} — the capsule fell back to dashes")
    title = str(card.get("title") or "")
    assert "NVDA" in title and "TSLA" in title, (
        f"a comparison's first row is {title!r} and names neither company")


def test_a_comparison_does_not_borrow_one_companys_price():
    """Half a comparison drawn as a quote is what the dashes were hiding.

    The module bundles ticker_a's quote into the payload. Passing it through
    would put NVDA's price and day range on a card headed "NVDA vs TSLA", which
    is a card that looks right and is answering a different question.
    """
    card = CardStore().absorb("atrade_compare", COMPARE,
                              {"ticker_a": "NVDA", "ticker_b": "TSLA"})
    for borrowed in ("price", "day_high", "day_low", "market_cap",
                     "previous_close", "change_pct"):
        assert borrowed not in card, (
            f"{borrowed!r} from one side of the comparison reached the card")


def test_a_comparison_does_not_merge_into_the_previous_company():
    """`compare` carries ticker_a and ticker_b and no `ticker`.

    The store resolved its key from the raw payload, found nothing, and left
    itself pointing at whoever was there before — so a comparison folded into
    that company's card. The key is resolved after the adapter now, because
    some answers only name their subject once adapted.
    """
    store = CardStore()
    store.absorb("atrade_quote", QUOTE, {"ticker": "PLTR"})
    assert store._key == "PLTR"

    card = store.absorb("atrade_compare", COMPARE,
                        {"ticker_a": "NVDA", "ticker_b": "TSLA"})

    assert store._key == "NVDA", (
        f"the store is still keyed to {store._key!r}; a comparison merged into "
        f"the company that happened to be on the card before it")
    assert card.get("price") is None, (
        "Palantir's price survived into a card about NVDA versus TSLA")


# ── the rule the two share ───────────────────────────────────────────────────

def test_no_adapted_answer_produces_a_capsule_of_dashes():
    """The property both defects violated, stated once.

    Every tool with an adapter is a tool whose answer is meant to be seen. If
    one of them can be drawable and still say nothing, that is the same bug
    again under a different tool name.
    """
    import core.card_assembly as CA

    payloads = {
        "atrade_quote": (QUOTE, {"ticker": "PLTR"}),
        "atrade_insider": (INSIDER, {"ticker": "NVDA"}),
        "atrade_compare": (COMPARE, {"ticker_a": "NVDA", "ticker_b": "TSLA"}),
    }
    for tool, (payload, args) in payloads.items():
        card = CardStore().absorb(tool, payload, args)
        if not _drawable(tool, card):
            continue                    # allowed: some answers are not cards
        assert _says_something(card), (
            f"{tool} earned the screen and its capsule says "
            f"{card.get('detail')!r}")

    uncovered = sorted(set(CA.ADAPTERS) - set(payloads))
    assert uncovered == ["atrade_analyze", "atrade_governance"], (
        f"an adapter appeared with no payload here: {uncovered}. Add one "
        f"shaped like its real --json output; an adapter nothing exercises is "
        f"how both of these shipped.")
