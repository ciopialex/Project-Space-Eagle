"""The line the eagle does not cross by itself.

v1 has no fresh-explicit-yes gate for the web. Until it does, controls that
commit something are refused outright rather than clicked and apologised for.
The Constitution already requires that irreversible decisions be paused and
escalated to the human; this is that clause, made checkable.

The list is a heuristic and will be wrong in both directions. It errs toward
refusing, because the cost of one unnecessary question is a sentence and the
cost of one unnecessary payment is a payment.

Two mechanisms are deliberately absent from this file, on purpose rather than
by oversight:

- There is no exact-phrase blacklist for committing acts (e.g. "place
  order"). A blacklist is defeated by one inserted word ("place your
  order"), and every insertion that defeats it moves the wrong way — toward
  an unrefused click. Committing verbs live in `_COMMITTING` instead, so
  they catch the word wherever it appears in the label.
- There is no "reading word anywhere in the label exempts it" rule. That
  shape (tried in an earlier round) exempts "Confirm payments" for the same
  reason it exempts "Payment history" — it cannot tell which noun the
  reading word belongs to. Read-only labels are matched whole instead, in
  `_READ_ONLY_LABELS`: a whitelist that is too narrow only causes an
  unnecessary question, never an unrefused payment, so that is where exact
  matching belongs.
"""
from __future__ import annotations

import re
import unicodedata

#: Homoglyph -> Latin. The label text comes from the page being browsed, so
#: the site controls it: a single substituted character (Cyrillic О for
#: Latin O, say) must not be able to turn a refusal into an allow. There is
#: no confusables table in the standard library and Unicode's own
#: confusables.txt is thousands of entries with no crisp stopping point, so
#: this is a hand-rolled, deliberately partial table — the letters that
#: appear (lower-cased) in the guard's own vocabulary and have a
#: well-known, visually-indistinguishable Latin look-alike. It is folded
#: after `.lower()`, so only lower-case source characters are needed:
#: Python's Unicode-aware `.lower()` already turns e.g. Cyrillic capital О
#: into Cyrillic lower-case о before this table is consulted.
#:
#: Coverage, and where the line was drawn:
#: - Cyrillic: а е о р с у х і ѕ ј (the brief's list) plus т, which is not
#:   in the brief's list but is required to close the specific attack named
#:   in this task's brief ("Deleтe account").
#: - Greek: α ο ρ ν (the brief's list).
#: - Digit-lookalikes: the ASCII digits that are classic leetspeak stand-ins
#:   for a Latin letter (0/o, 1/l, 3/e, 4/a, 5/s, 7/t, 8/b) and also appear
#:   in Unicode's own confusables data as MA (mixed-script/ASCII) entries.
#:   Folding these can only make the guard refuse MORE, never less, because
#:   nothing in `_COMMITTING`/`_COMMITTING_PAIRS`/`_READ_ONLY_LABELS` turns
#:   a digit into part of a matched word — so this cannot create a new
#:   false-allow, only close one (a label spelled with a leetspeak digit in
#:   place of a letter).
#: This table is NOT exhaustive — no Latin-alphabet confusables table can
#: be, per the brief — and it does not attempt letters (n, g, u, r, h, b, d,
#: l, f, m, k, w, q, v, z) that have no single, unambiguous, widely-cited
#: look-alike. See the report for the neighbourhood probe this was checked
#: against.
_CONFUSABLES = {
    # Cyrillic
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "і": "i", "ѕ": "s", "ј": "j", "т": "t",
    # Greek
    "α": "a", "ο": "o", "ρ": "p", "ν": "v",
    # Digit-lookalikes (leetspeak / Unicode MA confusables)
    "0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b",
}

#: Unicode general category "Cf" ("Format") covers the zero-width space,
#: zero-width joiner/non-joiner, word joiner, soft hyphen, and the bidi
#: control characters — every character in this family is *invisible* and
#: exists only to affect rendering, never to be part of a word. They are
#: removed outright rather than replaced with a space: "Or<ZWSP>der" must
#: fold to the single token "order", not split into "or" + "der" (which
#: would neither match nor safely refuse — it would just silently allow a
#: label a human reads as one committing word).
_FORMAT_CATEGORY = "Cf"

#: Bare verbs whose presence anywhere in the label means the control commits
#: something. Matched on whole words, never substrings, so "Payment history"
#: does not trip on "pay". Because these match on the word itself rather than
#: a fixed phrase, no possessive or "your" inserted before the object can
#: defeat them the way it defeats an exact-phrase entry.
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
    "signing": "it signs something",
    "send": "it sends something",
    "publish": "it publishes something",
    "post": "it posts something publicly",
    "book": "it books something",
    "apply": "it submits an application",
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

#: Verbs that are ordinary dismiss/undo actions on their own ("Close",
#: "Cancel") but commit something when their object co-occurs anywhere in
#: the label. Co-occurrence rather than adjacency, so "Close your account"
#: and "Close all accounts" are caught the same as "Close account", while
#: "Close menu" and "Cancel" alone are untouched.
_COMMITTING_PAIRS = {
    "close": ({"account", "accounts"}, "it closes an account"),
    "cancel": ({"subscription", "subscriptions"}, "it cancels a subscription"),
}

#: Whole labels that read a record rather than commit an act. Matched against
#: the entire normalised label, never as a substring, so "Order history" is a
#: page while "Place your order" and "Order now" are not. Some entries here
#: (e.g. "payment history") would already be allowed by the verb scan below
#: because "payment" is not a verb; they are listed anyway for clarity and in
#: case a future verb addition would otherwise catch them.
_READ_ONLY_LABELS = {
    "order history", "order details", "order summary", "order status",
    "purchase history", "purchase details", "purchase summary",
    "purchase receipt", "purchase receipts",
    "payment history", "payment details", "payment method",
    "payment methods", "saved payment methods",
    "transfer history", "transfer details", "transfer status",
    "billing history", "post history", "your orders", "your purchases",
}

#: Login/navigation prefixes that are benign on their own. Stripped as a
#: prefix (not matched as a whole label) so "Sign in with Google" is
#: allowed. The remainder is then run back through the full check below —
#: not a narrower, verb-only check — so "Sign in and place your order"
#: still hits the "order" verb and "Sign in and close your account" still
#: hits the close/account pair.
_BENIGN_PREFIXES = ["sign in", "log in", "sign out", "log out", "sign up"]


def _words(text: str) -> list[str]:
    text = text or ""
    # NFKC first: it is what collapses fullwidth forms ("Ｐay" -> "Pay"),
    # many ligatures, and other compatibility variants down to the
    # ordinary characters they display as. Standard library, no table
    # needed for this part.
    text = unicodedata.normalize("NFKC", text)
    # Strip invisible format characters before anything else touches the
    # text, so they can never masquerade as a word boundary.
    text = "".join(ch for ch in text if unicodedata.category(ch) != _FORMAT_CATEGORY)
    text = text.lower()
    # Fold homoglyphs onto the Latin letter they are standing in for, so a
    # page that spells "order" with a Cyrillic о is tokenised exactly like
    # a page that spells it with a Latin o.
    text = "".join(_CONFUSABLES.get(ch, ch) for ch in text)
    return [w for w in re.split(r"[^a-z0-9]+", text) if w]


def irreversible_reason(name: str, role: str = "") -> str:
    """Why the eagle must not click this on its own, or "" if it may.

    Returns a phrase that slots into a sentence the user hears: the tool says
    "I stopped because <reason>".
    """
    words = _words(name)
    if not words:
        if (name or "").strip():
            # There was visible text, but none of it survived
            # normalisation into a recognisable Latin word — most likely a
            # genuinely non-English label (Japanese, Arabic, ...), not an
            # empty control. Say that, rather than the misleading "no
            # readable label", which reads as if the control had no text
            # at all. Either way the answer is still refuse: an unreadable
            # label is exactly the case where the human has to be asked.
            return ("it is not in a script this guard can read, so there is "
                    "no way to tell what it does")
        return ("it has no readable label, so there is no way to tell what it "
                "does")

    label = " ".join(words)

    for prefix in _BENIGN_PREFIXES:
        if label == prefix:
            return ""
        if label.startswith(prefix + " "):
            # Re-run the complete check on what's left of the label, rather
            # than a narrower verb-only scan. A benign login prefix should
            # not shield the remainder from anything a bare label would be
            # caught by (read-only whitelist, pairs, verbs).
            return irreversible_reason(label[len(prefix):].strip())

    if label in _READ_ONLY_LABELS:
        return ""

    word_set = set(words)
    for verb, (objects, reason) in _COMMITTING_PAIRS.items():
        if verb in word_set and word_set & objects:
            return reason

    # Reverse iteration prioritises the final verb in multi-verb labels: in
    # "Confirm and pay", that is "pay", not "confirm".
    for word in reversed(words):
        reason = _COMMITTING.get(word)
        if reason:
            return reason
    return ""
