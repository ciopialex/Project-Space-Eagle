"""A card kept between turns must not be handed back as though it just arrived.

`CardStore` outlives the turn that filled it. That is the point of it: the
capsule prefetch fetches while a capsule is on screen, and a click seconds
later reads the answer back instead of paying ~35s for `analyze` again.

It had no clock. Not a short one — none at all. `card_for` returned whatever
was in the store, and `fetch_card_depth` painted it at a 30s dwell, so a price
fetched ten minutes earlier was drawn as the current one with nothing anywhere
asking how old it was. That is not a stale cache; a stale cache serves an old
answer to an old question. This served an old number as the answer to a new
one, which is the failure mode the whole card exists to avoid.

Why this earns a test when "anything a person would notice in one run" does
not: nobody can notice it. A price that is ten minutes old looks exactly like a
price that is two seconds old — that is what makes it dangerous — and the only
way to see it is to know what the market did in between. There is no run in
which a person catches this by looking.

The clock is injected, so none of this sleeps.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.card_assembly import CardStore  # noqa: E402


class _Clock:
    """A hand-wound monotonic clock."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _quote(ticker: str = "NVDA", price: float = 230.36) -> dict:
    return {"ticker": ticker, "company_name": "NVIDIA Corp.", "price": price,
            "previous_close": 229.62, "change": 0.74, "change_pct": 0.32}


def _store() -> tuple[CardStore, _Clock]:
    clock = _Clock()
    return CardStore(clock=clock), clock


# ── the defect ───────────────────────────────────────────────────────────────

def test_a_price_older_than_the_module_allows_is_not_served():
    """The regression itself, at the boundary the module declares."""
    store, clock = _store()
    store.absorb("atrade_quote", _quote(), {"ticker": "NVDA"})

    clock.advance(61)                       # atrade declares fresh_for = 60
    assert store.card_for("NVDA", fresh_for=60) is None, (
        "a 61-second-old quote was handed back to be painted as the current "
        "price")


def test_a_fresh_price_is_still_served():
    """The bound must not cost the prefetch its whole reason to exist."""
    store, clock = _store()
    store.absorb("atrade_quote", _quote(), {"ticker": "NVDA"})

    clock.advance(5)
    card = store.card_for("NVDA", fresh_for=60)
    assert card is not None, "a five-second-old card was rejected"
    assert card["price"] == 230.36


def test_no_bound_is_the_default_and_keeps_the_old_behaviour():
    """A module that declares nothing must be unaffected.

    `[island].fresh_for` defaults to 0, and a3d declares none: a downloaded
    model file is the same file an hour later. Adding a clock must not start
    expiring answers for modules whose answers do not rot.
    """
    store, clock = _store()
    store.absorb("atrade_quote", _quote(), {"ticker": "NVDA"})

    clock.advance(86_400)
    assert store.card_for("NVDA") is not None
    assert store.card_for("NVDA", fresh_for=0) is not None


# ── the clock has to move when the card does ─────────────────────────────────

def test_a_second_answer_makes_the_card_current_again():
    """Absorbing is what freshness means; the card was just refetched."""
    store, clock = _store()
    store.absorb("atrade_quote", _quote(price=230.36), {"ticker": "NVDA"})

    clock.advance(59)
    store.absorb("atrade_quote", _quote(price=231.10), {"ticker": "NVDA"})
    clock.advance(30)                       # 89s after the first, 30 after the second

    card = store.card_for("NVDA", fresh_for=60)
    assert card is not None, "a card refetched 30 seconds ago was called stale"
    assert card["price"] == 231.10


def test_the_two_window_worst_case_is_real_and_bounded():
    """The documented cost of bounding on the newest contribution.

    `card_for` asks "has anything touched this card recently", not "is every
    field recent". Bounding on the oldest field would refetch a 35-second
    `analyze` every time a 2-second `quote` aged out, so the cheaper rule is
    the right trade — but it means a field can reach twice the window if a
    second answer lands just before the first one expires.

    Asserted rather than merely written down, because a limit nobody measured
    is a limit nobody knows they crossed. If this ever fails, the rule changed
    and the docstring on `card_for` is now wrong.
    """
    store, clock = _store()
    store.absorb("atrade_quote", _quote(), {"ticker": "NVDA"})   # price at t=0

    clock.advance(59)
    # A different tool touches the card without refreshing the price.
    store.absorb("atrade_governance",
                 {"ticker": "NVDA", "available": True, "verdict": "ALIGNED PERFORMANCE",
                  "sentence": "pay tracks performance"},
                 {"ticker": "NVDA"})
    clock.advance(59)                        # the price is now 118 seconds old

    card = store.card_for("NVDA", fresh_for=60)
    assert card is not None, (
        "the worst case is no longer reachable — the rule changed and "
        "card_for's docstring should say so")
    assert card["price"] == 230.36
    assert store.age() < 60


# ── the older guarantee still holds ──────────────────────────────────────────

def test_another_company_is_never_returned_however_fresh():
    """The mistake the class was built to prevent, still prevented.

    Freshness is a second gate, not a replacement for the first one.
    """
    store, _clock = _store()
    store.absorb("atrade_quote", _quote(ticker="NVDA"), {"ticker": "NVDA"})

    assert store.card_for("AMD", fresh_for=60) is None
    assert store.card_for("AMD") is None


def test_an_empty_store_has_no_age_and_serves_nothing():
    store, _clock = _store()
    assert store.age() is None
    assert store.touched_at() is None
    assert store.card_for("NVDA", fresh_for=60) is None


def test_a_new_company_resets_the_clock_with_the_card():
    """A different ticker starts a clean card, so it starts a clean clock."""
    store, clock = _store()
    store.absorb("atrade_quote", _quote(ticker="NVDA"), {"ticker": "NVDA"})

    clock.advance(300)
    store.absorb("atrade_quote", _quote(ticker="AMD", price=140.0),
                 {"ticker": "AMD"})

    card = store.card_for("AMD", fresh_for=60)
    assert card is not None, "a brand new card was born stale"
    assert card["price"] == 140.0

    # And nothing of NVDA's is still being timed. `age()` reads the newest
    # arrival, so a leftover entry could not change the answer above — which is
    # exactly why it would have gone unnoticed until something read a single
    # field's age and got a timestamp belonging to another company.
    assert "layers" not in store._at
    assert set(store._at) <= set(card)
