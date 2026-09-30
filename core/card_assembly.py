"""Assembling one island card out of several module answers.

The card asks for more than any single atrade tool returns. `quote` is fast and
carries price and the six-timeframe series; `analyze` costs about 35 seconds
against live SEC data and carries the seven layers, the verdict, the asymmetry
ratio and the invalidation level; `governance` and `insider` carry figures that
have never reached a card at all.

Before this module, main.py forwarded whichever single tool the model happened
to call. A price question calls `quote`, a quote payload has no layers, and the
drilldown into the seven-layer view therefore drew a card of em dashes — the
card telling the truth about an empty payload.

Each adapter is pure: it maps one tool's vocabulary onto the card's field names
and returns a flat dict. `merge_card` folds them together under one rule —
absent never overwrites present.
"""
from __future__ import annotations

import re
import time
from typing import Any

#: Fields quote and analyze already express in the card's own vocabulary. These
#: pass through unrenamed because web/pill.html reads them by these names;
#: renaming here would break a card that currently works.
#:
#: `_series` carries its underscore deliberately. Measured 2026-09-05, the chart
#: was 4,312 of a quote's 4,542 characters -- 487 numbers -- and `model_view`
#: strips only underscore-prefixed keys, so all of it went to Gemini on every
#: quote. The island draws the graph; the model cannot read one.
_PASSTHROUGH = (
    "ticker", "company_name", "price", "previous_close", "change",
    "change_pct", "day_high", "day_low", "volume", "market_cap", "_series",
    "_logo",
)

#: What only the deep answer carries. These are the slots the expanded card has
#: and the fast answer can never fill.
_ANALYZE_ONLY = (
    "verdict", "reason", "composite_score", "coverage", "asymmetry_ratio",
    "invalidation_level", "liar_filter_status", "liar_filter_detail", "layers",
)


def _take(payload: dict, keys) -> dict[str, Any]:
    """Copy the named keys that are actually present and not None."""
    return {k: payload[k] for k in keys if payload.get(k) is not None}


def adapt_quote(payload: dict) -> dict[str, Any]:
    """`atrade quote` — price, today's move, and the six-range series."""
    return _take(payload, _PASSTHROUGH)


def adapt_analyze(payload: dict) -> dict[str, Any]:
    """`atrade analyze` — everything quote has, plus the seven-layer scorecard."""
    return _take(payload, _PASSTHROUGH + _ANALYZE_ONLY)


def merge_card(existing: dict, incoming: dict) -> dict[str, Any]:
    """Fold one tool's contribution into the card.

    Absent never overwrites present. A quote arriving after an analyze updates
    the price and leaves the layers alone rather than blanking them.
    """
    merged = dict(existing)
    for key, value in incoming.items():
        if value is None:
            continue
        merged[key] = value
    return merged


#: ScrapeTrade's governance verdicts and the labels web/pill.html can draw an
#: icon for are two vocabularies with nothing in common. Measured 2026-09-03:
#: aethelark_trade/engine/governance.py:24-29 emits FOUNDER MODE / ALIGNED
#: PERFORMANCE / THE LEAK / TOTAL DRAIN / FAIR EXCHANGE / UNKNOWN, while
#: web/pill.html:745 keys its icons on FOUNDER GANG / CASHING OUT / FAIR DEAL /
#: MATERIAL CONTRACT. Transported unmapped, every governance icon renders empty.
_GOV_LABELS = {
    "FOUNDER MODE": "FOUNDER GANG",
    "ALIGNED PERFORMANCE": "FAIR DEAL",
    # The module files this under "Respect": management took a bigger cut
    # than the shareholders did. Aligned, in the card's vocabulary.
    "SHARED PAIN": "FAIR DEAL",
    "THE LEAK": "CASHING OUT",
    "TOTAL DRAIN": "CASHING OUT",
    # Not a failure. The tool read the proxy and found no pay-versus-performance
    # disclosure in it, which is a fact about the filing worth showing. The card
    # has no icon for it, and draws none.
    "UNKNOWN": "UNKNOWN",
}


#: The module's sentence opens by restating its own verdict:
#: `aethelark_trade/xbrl.py:100` builds "🔥 ALIGNED PERFORMANCE (Bullish): Stock
#: growth ... outpaces pay growth ...", and `strip_markup` only removes the
#: colour tags. The card prints the mapped label in bold immediately before it,
#: so the row read "FAIR DEAL — 🔥 ALIGNED PERFORMANCE (Bullish): Stock grow…":
#: the same finding named twice in two vocabularies, and then cut off before it
#: said anything. Measured 2026-09-07: 708px of sentence in a 338px row, so 52%
#: of it was thrown away and the surviving half was the redundant half.
#:
#: Anchored on the verdict the payload itself reported, so a sentence that does
#: not open with its own verdict is never touched.
def _finding(sentence: str, verdict: str) -> str:
    """The sentence with its own restated verdict trimmed off the front."""
    if not verdict:
        return sentence
    prefix = re.compile(
        r"^[^A-Za-z]*" + re.escape(verdict) + r"\s*(?:\([^)]*\))?\s*:\s*",
        re.IGNORECASE)
    return prefix.sub("", sentence, count=1).strip() or sentence


def adapt_governance(payload: dict) -> dict[str, Any]:
    """`atrade governance` — is the CEO paid in line with what holders earned.

    Every field is namespaced `gov_`: this tool returns a key called `verdict`
    and so does `analyze`, and they mean different things. Merged flat, a
    pay-alignment finding would silently replace the scorecard's own call.
    """
    out: dict[str, Any] = {}
    raw_verdict = str(payload.get("verdict") or "").strip()
    if raw_verdict:
        # A verdict the card has no icon for is still a verdict, so it goes
        # on the card in its own words, with no icon, instead of an em dash.
        # Measured 2026-09-23: the module says eight verdicts and this map
        # knew six, one of which ("FAIR EXCHANGE") it no longer says --
        # RISING TIDE SKEPTICISM and INEFFICIENT GROWTH drew a dash.
        out["gov_label"] = _GOV_LABELS.get(raw_verdict.upper()) or raw_verdict.upper()
    for src, dest in (("sentence", "gov_text"), ("ceo_name", "gov_ceo"),
                      ("pay_change_pct", "gov_pay_change_pct"),
                      ("tsr_change_pct", "gov_tsr_change_pct")):
        if payload.get(src) is not None:
            out[dest] = payload[src]
    if isinstance(out.get("gov_text"), str):
        out["gov_text"] = _finding(out["gov_text"], raw_verdict)
    return out


def adapt_insider(payload: dict) -> dict[str, Any]:
    """`atrade insider` — the Form 4 ledger, as the module already summed it."""
    out: dict[str, Any] = {}
    for src, dest in (("filings", "insider_trade_count"),
                      ("net_usd", "insider_net_usd")):
        if payload.get(src) is not None:
            out[dest] = payload[src]

    latest = payload.get("latest")
    if isinstance(latest, dict) and latest.get("insider"):
        title = latest.get("title")
        who = f"{latest['insider']} ({title})" if title else str(latest["insider"])
        usd = latest.get("usd")
        amount = f" ${usd:,.0f}" if isinstance(usd, (int, float)) else ""
        out["insider_latest"] = (
            f"{who} {latest.get('action') or 'filed'}{amount} "
            f"on {latest.get('date')}")
    return out


def adapt_compare(payload: dict) -> dict[str, Any]:
    """`atrade compare` — two companies, one answer.

    This had no adapter, and "forwarded unchanged" is not neutral here. The
    module bundles ticker_a's quote into the comparison, so `price`,
    `company_name` and `market_cap` are all present -- which is enough for
    `_card_is_drawable` to say yes -- while `write_headline` never runs,
    because that only happens on the adapter path. The capsule therefore went
    up with no title and no detail and rendered

        —  —  —   −$3.77 (−1.64%)

    three em dashes and a delta belonging to only one of the two companies.
    The same shape as the watchlist and portfolio answers already fixed, and
    it survived because a comparison has a `price` in it by accident.

    Namespaced `compare_` for the reason `gov_` is: `winner` and `margin` are
    generic words and a later tool is free to mean something else by them.
    The one-company quote fields are deliberately NOT passed through -- a
    comparison drawn on a card built for one company is what the dashes were.
    """
    a, b = payload.get("ticker_a"), payload.get("ticker_b")
    if not (a and b):
        return {}
    out: dict[str, Any] = {"ticker": str(a).upper(),
                           "compare_against": str(b).upper()}
    winner = payload.get("winner")
    if winner:
        out["compare_winner"] = str(winner).upper()

    said: list[str] = []
    margin = payload.get("margin")
    if isinstance(margin, (int, float)) and margin:
        said.append(f"by {abs(margin):g} point" + ("s" if abs(margin) != 1 else ""))
    won_a, won_b = payload.get("layers_won_a"), payload.get("layers_won_b")
    if isinstance(won_a, int) and isinstance(won_b, int):
        said.append(f"{won_a}-{won_b} on layers")
    if said:
        out["compare_detail"] = " · ".join(said)
    return out


#: Which adapter handles which module tool. main.py dispatches through this;
#: a tool with no entry keeps the old behaviour and is forwarded unchanged.
#: The module every adapter in this file belongs to, named ONCE.
#:
#: This whole file is one module's vocabulary living in the harness, and
#: test_harness_is_module_agnostic measures exactly that. Spelling the key out
#: per entry made the harness name it five more times for no benefit -- the
#: tool names are `<module>_<tool>` by construction, from the manifest. The
#: real repair is for these adapters to live in the module; this is the
#: boundary staying where it was while a fifth adapter was added.
_MODULE = "atrade"

ADAPTERS = {
    f"{_MODULE}_quote": adapt_quote,
    f"{_MODULE}_analyze": adapt_analyze,
    f"{_MODULE}_governance": adapt_governance,
    f"{_MODULE}_insider": adapt_insider,
    f"{_MODULE}_compare": adapt_compare,
}


#: The middle of the 0-100 layer scale. A layer sitting here says nothing.
_NEUTRAL = 50

#: How many layers a full scorecard has. Six since 2026-09-04: layer 6 scored
#: identically for every company, separated nothing, and was retired from the
#: composite with its weight redistributed across the rest. The card counts the
#: measured layers itself, from the values it draws; this is the total it counts
#: against, and it must not drift from the engine's LAYER_WEIGHTS.
_LAYER_COUNT = 6


def _insider_line(card: dict) -> str:
    """The Form 4 ledger in one capsule-width row.

    Net dollars first because it is the answer to the question people actually
    ask, then how many filings it came from so the number has a size behind it.
    """
    net = card.get("insider_net_usd")
    bits: list[str] = []
    if isinstance(net, (int, float)):
        amount = abs(net)
        for cut, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
            if amount >= cut:
                scaled = amount / cut
                bits.append(f"{'-' if net < 0 else '+'}$"
                            f"{scaled:.1f}{suffix}" if scaled < 10 else
                            f"{'-' if net < 0 else '+'}${scaled:.0f}{suffix}")
                break
        else:
            bits.append(f"{'-' if net < 0 else '+'}${amount:,.0f}")
    count = card.get("insider_trade_count")
    if isinstance(count, int):
        bits.append(f"{count} filing" + ("s" if count != 1 else ""))
    return "  ".join(bits)


def write_headline(card: dict) -> dict[str, Any]:
    """The capsule's two rows, and the line under the verdict."""
    verdict = card.get("verdict")
    if not verdict:
        ticker = card.get("ticker")
        if not ticker:
            return {}
        headline: dict[str, Any] = {"title": str(ticker)}
        price, pct = card.get("price"), card.get("change_pct")
        if price is not None:
            said = f"${price:,.2f}" if isinstance(price, (int, float)) else str(price)
            company = str(card.get("company_name") or "").strip()
            if company:
                said += f" · {company}"
            elif isinstance(pct, (int, float)):
                said += f"  {pct:+.2f}%"
            headline["detail"] = said
        elif card.get("compare_winner") or card.get("compare_against"):
            # A comparison is its own kind of answer and the capsule can carry
            # it in two rows: who won, and by how much.
            other = card.get("compare_against")
            won = card.get("compare_winner")
            if won and other:
                headline["title"] = (f"{won} \u25b8 {other}" if won != other
                                     else f"{ticker} \u25b8 {other}")
            elif other:
                headline["title"] = f"{ticker} vs {other}"
            detail = card.get("compare_detail")
            if detail:
                headline["detail"] = str(detail)
        elif card.get("insider_net_usd") is not None:
            # An insider answer is not a price answer and had no second row at
            # all, so the capsule said the ticker and nothing else -- which is
            # what `_card_is_drawable` reads as "a card about nothing" and
            # refuses. Asked after a quote it inherited that card's price and
            # looked fine; asked FIRST, "who has been selling Nvidia" ran the
            # tool, came back with 46 filings and $1.06bn, and put nothing on
            # screen at all.
            headline["detail"] = _insider_line(card)
        elif card.get("company_name"):
            headline["detail"] = str(card["company_name"])
        return headline

    out: dict[str, Any] = {"title": str(verdict)}

    # §30.3 — a reason never appears without a verdict, and one the module did
    # not send is not invented. `detail` is the capsule's second row; `why` is
    # the same clause under the verdict on the expanded card.
    reason = card.get("reason")
    if reason:
        out["detail"] = reason
        out["why"] = reason

    return out


def needs_depth(card: dict) -> bool:
    """Whether the seven-layer view would still be empty for this card.

    `analyze` costs about 35 seconds against live SEC data, so a drilldown pays
    for it only when the layers are genuinely missing.
    """
    layers = card.get("layers")
    return not (isinstance(layers, dict) and layers)


class CardStore:
    """The card currently on the island, and what has been learned about it.

    A card outlives the tool call that opened it: the user asks for a price,
    then drills into the seven-layer view, and the second view needs figures
    the first call never fetched. Keyed by ticker, so a different company
    starts a clean card rather than drawing NVDA's layers under AMD's title.
    """

    def __init__(self, clock=time.monotonic) -> None:
        self._key: str | None = None
        self._card: dict[str, Any] = {}
        #: When each field last arrived. Per field rather than per card,
        #: because a card is assembled from several answers and its parts are
        #: genuinely different ages: `quote` returns in about two seconds and
        #: `analyze` in about thirty-five, so a card routinely holds a price
        #: and a set of layers taken half a minute apart.
        self._at: dict[str, float] = {}
        #: Injected so the behaviour is testable without sleeping.
        self._clock = clock

    def absorb(self, tool_name: str, payload: dict, args: dict) -> dict[str, Any]:
        """Fold one tool result into the current card and return the whole card."""
        adapter = ADAPTERS.get(tool_name)
        if adapter is None:
            # a3d, alaw and anything installed later keep the old behaviour:
            # the module's own payload, forwarded exactly as it always was.
            return dict(payload)

        # The adapter runs BEFORE the key is decided, because some answers only
        # name their subject after being adapted. `compare` carries ticker_a
        # and ticker_b and no `ticker` at all, so resolving the key from the
        # raw payload found nothing, left the store keyed to whoever was there
        # before, and merged a comparison into that company's card — a card
        # headed "NVDA ▸ TSLA" over Palantir's price.
        contribution = adapter(payload)

        # The answer usually names the subject; when it does not, the question
        # did — the model was asked about a particular company.
        ticker = (payload.get("ticker") or args.get("ticker")
                  or contribution.get("ticker"))
        ticker = str(ticker).upper() if ticker else None

        if ticker and ticker != self._key:
            # The clock belongs to the card, so it is cleared with it. Leaving
            # `_at` behind kept arrival times for fields the new company's card
            # does not have — harmless while `age()` reads the newest of them,
            # and a lie the moment anything reads a field's age directly.
            self._key, self._card, self._at = ticker, {}, {}

        if ticker:
            contribution.setdefault("ticker", ticker)
        now = self._clock()
        for field, value in contribution.items():
            if value is not None:
                self._at[field] = now
        self._card = merge_card(self._card, contribution)
        self._card.update(write_headline(self._card))
        return dict(self._card)

    def touched_at(self) -> float | None:
        """When any part of this card last arrived, or None if empty."""
        return max(self._at.values()) if self._at else None

    def age(self) -> float | None:
        """Seconds since anything was last folded in. None when empty."""
        newest = self.touched_at()
        return None if newest is None else self._clock() - newest

    def card_for(self, ticker: str,
                 fresh_for: float = 0.0) -> dict[str, Any] | None:
        """Read the stored card for `ticker` without fetching or absorbing.

        None when the store holds a different company or nothing at all —
        keyed the same way `absorb` keys the store, so a prefetch for one
        ticker can never be read back as another's card. That mistake is the
        one this class was built to prevent in the first place.

        Also None when the card is older than `fresh_for` seconds. This class
        had no clock at all, and it outlives the turn that filled it: the
        capsule prefetch writes a card here and a later click reads it back and
        paints it, with nothing anywhere asking how long ago that was. A price
        fetched ten minutes earlier was served as though it had just arrived,
        which is not a stale cache -- it is a wrong number presented as a
        current one.

        The bound is on the NEWEST contribution: "has anything touched this
        card recently". Bounding on the oldest field instead would refetch a
        thirty-five-second `analyze` every time a two-second `quote` aged out.
        The cost of the cheaper rule is bounded and worth naming: a field can
        reach 2 x `fresh_for` if a second answer lands just before the window
        closes.

        `fresh_for` of 0 means no bound, which is the default and is right for
        a module whose answers do not rot -- a downloaded model file is the
        same file an hour later.
        """
        if not ticker or self._key != str(ticker).upper():
            return None
        if fresh_for > 0:
            age = self.age()
            if age is not None and age > fresh_for:
                return None
        return dict(self._card)
