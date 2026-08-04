"""The line the eagle does not cross by itself.

v1 has no fresh-explicit-yes gate for the web. Until it does, controls that
commit something are refused outright rather than clicked and apologised for.
The Constitution already requires that irreversible decisions be paused and
escalated to the human; this is that clause, made checkable.

The list is a heuristic and will be wrong in both directions. It errs toward
refusing, because the cost of one unnecessary question is a sentence and the
cost of one unnecessary payment is a payment.
"""
from __future__ import annotations

import re

#: Phrases whose presence means the control commits something. Matched on whole
#: words, so "Payment history" is a page and "Pay now" is a payment.
_COMMITTING = {
    "pay": "it pays something",
    "paying": "it pays something",
    "purchase": "it makes a purchase",
    "buy": "it buys something",
    "checkout": "it starts a checkout",
    "order": "it places an order",
    "transfer": "it transfers something",
    "submit": "it submits something",
    "file": "it files something",
    "confirm": "it confirms something that may not be undoable",
    "delete": "it deletes something",
    "remove": "it removes something",
    "cancel-subscription": "it cancels a subscription",
    "subscribe": "it starts a subscription",
    "agree": "it agrees to something on the user's behalf",
    "accept": "it accepts something on the user's behalf",
    "sign": "it signs something",
    "send": "it sends something",
    "publish": "it publishes something",
    "post": "it posts something publicly",
    "book": "it books something",
    "apply": "it submits an application",
}

#: Words that turn a committing verb back into a noun — a page you read rather
#: than an act you take.
_READING = {"history", "histories", "details", "summary", "list", "settings",
            "preferences", "methods", "method", "receipts", "receipt",
            "status", "orders", "purchases", "payments", "in"}


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", (text or "").lower()) if w]


def irreversible_reason(name: str, role: str = "") -> str:
    """Why the eagle must not click this on its own, or "" if it may.

    Returns a phrase that slots into a sentence the user hears: the tool says
    "I stopped because <reason>".
    """
    words = _words(name)
    if not words:
        return ("it has no readable label, so there is no way to tell what it "
                "does")

    if any(w in _READING for w in words):
        return ""

    for word in reversed(words):
        reason = _COMMITTING.get(word)
        if reason:
            return reason
    return ""
