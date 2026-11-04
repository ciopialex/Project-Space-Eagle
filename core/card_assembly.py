"""Assembling one island card from several module answers. `quote` is fast (price,
six-timeframe series); `analyze` takes about 35 s (seven layers, verdict,
asymmetry, invalidation); `governance` and `insider` add more.

Each adapter is pure: it maps one tool's vocabulary onto the card's field names.
merge_card folds them together: absent never overwrites present.
"""
from __future__ import annotations

import re
import time
from typing import Any

# Fields already in the card's vocabulary; web/pill.html reads them by these names.
# `_series` is underscored so model_view keeps the chart (most of a quote) away from the
# model.
_PASSTHROUGH = (
    "ticker", "company_name", "price", "previous_close", "change",
    "change_pct", "day_high", "day_low", "volume", "market_cap", "_series",
    "_logo",
)

# What only the deep answer carries.
_ANALYZE_ONLY = (
    "verdict", "reason", "composite_score", "coverage", "asymmetry_ratio",
    "invalidation_level", "liar_filter_status", "liar_filter_detail", "layers",
)


def _take(payload: dict, keys) -> dict[str, Any]:
    """Copy the named keys that are actually present and not None."""
    return {k: payload[k] for k in keys if payload.get(k) is not None}


def adapt_quote(payload: dict) -> dict[str, Any]:
    """`atrade quote`: price, today's move, the six-range series."""
    return _take(payload, _PASSTHROUGH)


def adapt_analyze(payload: dict) -> dict[str, Any]:
    """`atrade analyze`: quote's fields plus the seven-layer scorecard."""
    return _take(payload, _PASSTHROUGH + _ANALYZE_ONLY)


def merge_card(existing: dict, incoming: dict) -> dict[str, Any]:
    """Fold one tool's contribution into the card. Absent never overwrites present."""
    merged = dict(existing)
    for key, value in incoming.items():
        if value is None:
            continue
        merged[key] = value
    return merged


# The module's governance verdicts -> the labels web/pill.html draws icons for. The two
# vocabularies share nothing.
_GOV_LABELS = {
    "FOUNDER MODE": "FOUNDER GANG",
    "ALIGNED PERFORMANCE": "FAIR DEAL",
    # Management took a bigger cut than shareholders: Aligned in the card's vocabulary.
    "SHARED PAIN": "FAIR DEAL",
    "THE LEAK": "CASHING OUT",
    "TOTAL DRAIN": "CASHING OUT",
    # No pay-versus-performance disclosure in the proxy: a fact, drawn without an icon.
    "UNKNOWN": "UNKNOWN",
}


# The module's sentence restates its own verdict; trimmed, since the card prints the
# label right before it.
def _finding(sentence: str, verdict: str) -> str:
    """The sentence with its restated verdict trimmed off the front."""
    if not verdict:
        return sentence
    prefix = re.compile(
        r"^[^A-Za-z]*" + re.escape(verdict) + r"\s*(?:\([^)]*\))?\s*:\s*",
        re.IGNORECASE)
    return prefix.sub("", sentence, count=1).strip() or sentence


def adapt_governance(payload: dict) -> dict[str, Any]:
    """`atrade governance`: is the CEO paid in line with what holders earned. Fields
    are namespaced gov_ because analyze also returns `verdict`.
    """
    out: dict[str, Any] = {}
    raw_verdict = str(payload.get("verdict") or "").strip()
    if raw_verdict:
        # A verdict with no icon still goes on the card in its own words.
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
    """`atrade insider`: the Form 4 ledger as the module summed it."""
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
    """`atrade compare`: two companies, one answer. Namespaced compare_; the one-
    company quote fields are not passed through.
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


# Which adapter handles which module tool; a tool with no entry is forwarded unchanged.
# These adapters are one module's vocabulary in the harness and belong in the module.
_MODULE = "atrade"

ADAPTERS = {
    f"{_MODULE}_quote": adapt_quote,
    f"{_MODULE}_analyze": adapt_analyze,
    f"{_MODULE}_governance": adapt_governance,
    f"{_MODULE}_insider": adapt_insider,
    f"{_MODULE}_compare": adapt_compare,
}


# The middle of the 0-100 layer scale.
_NEUTRAL = 50

# Layers in a full scorecard. Must match the engine's LAYER_WEIGHTS.
_LAYER_COUNT = 6


def _insider_line(card: dict) -> str:
    """The Form 4 ledger in one capsule-width row: net dollars, then the filing
    count.
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
            # A comparison: who won, and by how much.
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
            # An insider answer gets its own second row, or the card is about nothing.
            headline["detail"] = _insider_line(card)
        elif card.get("company_name"):
            headline["detail"] = str(card["company_name"])
        return headline

    out: dict[str, Any] = {"title": str(verdict)}

    # A reason never appears without a verdict, and one the module did not send is not
    # invented. `detail` is the capsule's second row; `why` is the same clause on the
    # expanded card.
    reason = card.get("reason")
    if reason:
        out["detail"] = reason
        out["why"] = reason

    return out


def needs_depth(card: dict) -> bool:
    """Whether the seven-layer view would still be empty. analyze costs about 35 s,
    so fetch only then.
    """
    layers = card.get("layers")
    return not (isinstance(layers, dict) and layers)


class CardStore:
    """The card on the island and what has been learned about it. Outlives the call
    that opened it; keyed by ticker, so another company starts a clean card.
    """

    def __init__(self, clock=time.monotonic) -> None:
        self._key: str | None = None
        self._card: dict[str, Any] = {}
        # When each field last arrived: a card holds parts of different ages.
        self._at: dict[str, float] = {}
        #: Injected so the behaviour is testable without sleeping.
        self._clock = clock

    def absorb(self, tool_name: str, payload: dict, args: dict) -> dict[str, Any]:
        """Fold one tool result into the current card and return the whole card."""
        adapter = ADAPTERS.get(tool_name)
        if adapter is None:
            # Other modules: the payload is forwarded as is.
            return dict(payload)

        # Adapt before deciding the key: some answers name their subject only after
        # adapting (compare has no `ticker`).
        contribution = adapter(payload)

        # The answer usually names the subject; otherwise the question did.
        ticker = (payload.get("ticker") or args.get("ticker")
                  or contribution.get("ticker"))
        ticker = str(ticker).upper() if ticker else None

        if ticker and ticker != self._key:
            # The clock belongs to the card and is cleared with it.
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
        """The stored card for `ticker`, without fetching. None when the store holds
        another company, or when the newest contribution is older than `fresh_for`
        seconds (0 means no bound). A field can reach 2 x fresh_for.
        """
        if not ticker or self._key != str(ticker).upper():
            return None
        if fresh_for > 0:
            age = self.age()
            if age is not None and age > fresh_for:
                return None
        return dict(self._card)
