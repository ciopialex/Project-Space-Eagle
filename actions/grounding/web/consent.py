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
    "subscribe": "it starts a subscription",
    "unsubscribe": "it cancels a subscription",
    "agree": "it agrees to something on the user's behalf",
    "accept": "it accepts something on the user's behalf",
    "sign": "it signs something",
    "send": "it sends something",
    "publish": "it publishes something",
    "post": "it posts something publicly",
    "book": "it books something",
    "apply": "it submits an application",
    "close": "it closes an account or service",
    "deactivate": "it deactivates an account",
    "terminate": "it terminates a service",
    "erase": "it erases data",
    "wipe": "it wipes data permanently",
    "withdraw": "it withdraws funds",
    "authorize": "it authorizes a payment",
    "authorise": "it authorises a payment",
    "donate": "it makes a donation",
    "bid": "it places a bid",
}

#: Whole labels that are benign despite containing a committing verb. Matched
#: as complete normalised labels (space-joined words), never token-by-token, so
#: "Sign in" is exempt while "Sign in and pay" is not.
_BENIGN_LABELS = {"sign in", "log in", "sign out", "log out", "sign up",
                  "signin", "login", "logout"}

#: Multi-word phrases that commit something. Matched against consecutive
#: words, so "Cancel subscription" is caught but "Cancel" alone is allowed.
_COMMITTING_PHRASES = {
    "cancel subscription": "it cancels a subscription",
}

#: Words that turn a committing verb back into a noun — a page you read rather
#: than an act you take. Exempts a label only when it is a short noun phrase:
#: at most two words, ending in the reading word. This prevents "Payment
#: history" (2 words) from allowing "Confirm order details" (3 words).
_READING = {"history", "histories", "details", "summary", "list", "settings",
            "preferences", "methods", "method", "receipts", "receipt",
            "status", "orders", "purchases", "payments"}


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

    # Check for benign whole labels (e.g., "sign in" is allowed despite "sign"
    # being a committing verb).
    label = " ".join(words)
    if label in _BENIGN_LABELS:
        return ""

    # Reading words exempt only short noun phrases (at most 2 words, ending in
    # the reading word). This prevents "Confirm order details" from being
    # allowed just because it contains "details".
    if len(words) <= 2 and words[-1] in _READING:
        return ""

    # Check for multi-word phrases that commit something. This catches cases
    # like "Cancel subscription" where the words don't match individually but
    # the phrase does (since "Cancel" alone is navigation).
    label = " ".join(words)
    for phrase, reason in _COMMITTING_PHRASES.items():
        if phrase in label:
            return reason

    # Iterate in reverse to prioritize the final (most important) verb: in
    # "Confirm and pay", that is "pay", not "confirm". This is structurally
    # safety-neutral (verified against all two- and three-word permutations of
    # the vocabulary) and semantically correct for multi-verb actions.
    for word in reversed(words):
        reason = _COMMITTING.get(word)
        if reason:
            return reason
    return ""
