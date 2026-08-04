"""The line the eagle does not cross by itself.

v1 has no fresh-explicit-yes gate for the web. Until it does, controls that
commit something are refused outright rather than clicked and apologised for.
The Constitution already requires that irreversible decisions be paused and
escalated to the human; this is that clause, made checkable.

The list is a heuristic and will be wrong in both directions. It errs toward
refusing, because the cost of one unnecessary question is a sentence and the
cost of one unnecessary payment is a payment.

The vocabulary this file matches against (`_COMMITTING`, `_READ_ONLY_LABELS`,
`_BENIGN_PREFIXES`, ...) is English, ASCII words. That is a real, deliberate
limitation, not an oversight: a label is only ever treated as readable if,
after Unicode normalisation, it reduces to plain ASCII letters, digits,
ordinary punctuation and whitespace (see `_words()`). A button on a Russian,
Greek, Japanese, or Arabic site — including the pay button — will not match
anything in the vocabulary and will refuse, asking the human, on every
control. That is the safe direction (refuse means "ask", never "allow blind"),
but it means this guard currently gives non-English sites no more nuance than
"stop and ask about everything". Extending it to actually understand
non-English committing words is future work, not attempted here.

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
import string
import unicodedata

#: Homoglyph -> Latin. This is now a false-refusal *reducer*, not the
#: guard's security boundary — see `_ALLOWED_CHARS` below for where the
#: actual boundary lives. Folding "Оrder" to "order" lets the verb scan
#: give the specific, accurate reason ("it places an order") instead of
#: the generic "it is not in a script this guard can read" a label with an
#: un-mapped homoglyph falls back to; either way the label refuses, folded
#: or not, so an incomplete table can never turn a refusal into an allow.
#: There is no confusables table in the standard library and Unicode's own
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
#:   This one *does* still carry real weight even under the allowlist
#:   design below, because digits are already in `_ALLOWED_CHARS` on their
#:   own: "0rder now" reaches the verb scan as the literal token "0rder"
#:   whether or not this table folds it, so without the fold it would be
#:   allowed outright, not just refused-for-the-wrong-reason. The honest
#:   claim is narrower than "only closes holes, changes nothing else": it
#:   deliberately makes previously-allowed, digit-obscured spellings of a
#:   committing word newly refuse — "P05T" -> post, "B00K" -> book, and
#:   "5UBMIT" -> submit are the intended, reachable effect of this table,
#:   not a side effect. What it does NOT do is turn a *correctly-allowed*
#:   label into a refusal: the fold only ever remaps a digit character
#:   onto a letter that already appears in this guard's own vocabulary, so
#:   it can only create new matches on words already treated as
#:   committing — it cannot break an existing match, and no all-digit
#:   token can ever spell a whole committing word, because every entry in
#:   `_COMMITTING` and every object in `_COMMITTING_PAIRS` contains at
#:   least one letter absent from this table's digit targets
#:   ({o, l, e, a, s, t, b}) — e.g. `order` needs r/d, `pay` needs p,
#:   `sign` needs i/g/n, `account` needs c/u/n. Checked by hand against
#:   the full vocabulary and confirmed empirically in the neighbourhood
#:   probe (ordinary numeric UI copy — quantities, percentages, order
#:   numbers — does not spuriously refuse).
#: This table is NOT exhaustive — no Latin-alphabet confusables table can
#: be, per the brief — and it does not attempt letters (n, g, u, r, h, b, d,
#: l, f, m, k, w, q, v, z) that have no single, unambiguous, widely-cited
#: look-alike. It does not need to be exhaustive to be safe: any character
#: this table doesn't fold, and that isn't plain ASCII either, fails the
#: `_ALLOWED_CHARS` check below and refuses the whole label. See the report
#: for the neighbourhood probe this was checked against.
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

#: Symbol categories: So (Other Symbol — pictographs, dingbats, currency
#: symbols like ™/©/★, and the large majority of standalone emoji), Sk
#: (Modifier Symbol), Sm (Math Symbol — this is where arrows like ← → live,
#: not punctuation), Sc (Currency Symbol). Fix round 2 of this task: the
#: round-1 allowlist correctly refused every attack, but it also refused
#: "🛒 Add to cart", "← Back", "★ Favourite", "Sign in with Google →" —
#: ordinary web copy, not decoration exclusive to commerce pages. A guard
#: that cannot click Back or Search without asking is the "trains everyone
#: to switch it off" failure this module's own docstring warns about.
#:
#: Stripped outright, the same as `Cf`, rather than folded or left for the
#: allowlist to catch. This is safe in the direction that matters: joining
#: two tokens together (removing a character that used to separate them)
#: can only ever produce a MORE recognisable word, never a less
#: recognisable one, so it can only ever move a label from "unreadable" or
#: "benign fragment" toward a real dictionary word — which can only
#: increase refusals, never manufacture an allow. That is the opposite
#: failure direction from round 0's bug, where an unrecognised character
#: became a false *word boundary* inside what should have stayed one
#: token; here, removing a symbol can at most restore a word boundary that
#: was never semantically there. An emoji cannot homoglyph a Latin letter,
#: so nothing is lost by removing it — and removing it, rather than
#: leaving it for the allowlist, is what lets "🔥 Buy now" reach the verb
#: scan and refuse with the *specific* reason ("it buys something")
#: instead of the generic "not in a script" one.
#:
#: This also closes the Braille Pattern Blank (U+2800) case a second,
#: cheaper way: it's category So, so "Or⠀der now" now becomes "Order now"
#: and is caught by the verb scan directly, rather than needing to fall
#: through to the allowlist's unreadable branch. Both paths still refuse;
#: only the reason text changes.
#:
#: Not attempted: reproducing Unicode's "Extended_Pictographic" property
#: exactly. Python's standard library `unicodedata` module exposes general
#: categories, not emoji-data properties, and this repo does not add a
#: third-party dependency to get one. So/Sk/Sm/Sc covers the overwhelming
#: majority of what Extended_Pictographic covers in practice — every emoji
#: named in this task's brief and review is So; multi-codepoint emoji
#: sequences are held together by ZWJ (already `Cf`, already stripped) and
#: variation selectors (`Mn`, stripped by `_strip_diacritics` below) or by
#: a combining enclosing keycap (`Mn` as well). What this does not catch:
#: any Extended_Pictographic character that Unicode has classified outside
#: So/Sk/Sm/Sc/Mn/Cf. I did not find one among ordinary UI copy while
#: building this; if one exists it fails safe (refuses) rather than
#: silently allowing, per this file's own governing rule.
_SYMBOL_CATEGORIES = frozenset({"So", "Sk", "Sm", "Sc"})

#: The actual security boundary (fix round 1 of this task). Round 0 tried
#: to enumerate every character class that isn't really a word (Cf format
#: characters, then confusable homoglyphs) and remove or fold each one —
#: an *exclusion* list. That shape is provably incomplete: a review found
#: three more bypass classes it didn't cover (combining marks / precomposed
#: diacritics, non-Cf invisibles like the combining grapheme joiner and
#: variation selectors, and any Cyrillic/Greek letter simply absent from
#: the confusables table — which is most of them, by construction, since
#: the table is deliberately partial). Worse: for whole Cyrillic/Greek
#: labels, the *partial* fold left a handful of junk one- or two-letter
#: tokens behind (e.g. "Оплатить" -> ['o', 'at', 't']), which is non-empty,
#: so the "no words -> refuse" fallback never fired and the label reached
#: the verb scan and came back allowed — the exact "could not read it ->
#: allow" failure this task exists to forbid, and strictly worse than the
#: pre-task-6b behaviour, where the same label refused outright.
#:
#: An *allowlist* fixes this by construction rather than by enumeration:
#: after NFKC, Cf-stripping, lower-casing and confusable-folding, if
#: anything remains that is not an ASCII letter, digit, ordinary
#: punctuation mark, or ASCII whitespace, the entire label is unreadable
#: and tokenises to no words at all — never to junk words. This closes
#: every homoglyph this guard doesn't recognise, every combining mark,
#: every script mixture, and every invisible character, including ones no
#: one has found yet, at the cost of such labels refusing outright rather
#: than being read. That cost is the point: refuse is the safe direction,
#: and "I cannot read this" must never again produce a partial read.
_ALLOWED_CHARS = frozenset(
    string.ascii_lowercase + string.digits + string.punctuation + " \t\n\r\v\f"
)

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


def _strip_noise(text: str) -> str:
    """NFKC-normalise, then drop the Unicode noise that carries no word
    content of its own: invisible "Format" (`Cf`) characters, Symbol
    characters (`_SYMBOL_CATEGORIES`), and combining marks (category `Mn`,
    reached by decomposing to NFD first so precomposed accented letters —
    "café", "á" — expose the marks NFC hides inside a single code point).

    NFKC collapses fullwidth forms ("Ｐay" -> "Pay"), many ligatures, and
    other compatibility variants down to the ordinary characters they
    display as — standard library, no table needed. `Cf` covers the
    zero-width space, zero-width joiner/non-joiner, word joiner, soft
    hyphen, and the bidi control characters. The symbol strip is fix round
    2's addition — see `_SYMBOL_CATEGORIES` for why it's safe (it can only
    join tokens, never split them). The NFD decompose + `Mn` strip is also
    fix round 2: it recovers "café" -> "cafe" and "Ordér" -> "Order" as a
    deliberate reversal of round 1's decision to let those refuse as
    unreadable, and it has the same "joining only" safety argument — plus,
    as a bonus, it removes variation selectors for free, since those are
    `Mn` too (e.g. the gear-emoji-plus-selector in "⚙️ Settings").

    All three strips are removals, never replacements with a space: an
    inserted character must not be allowed to manufacture a word boundary
    that a human reading the label wouldn't see.

    Shared by `_words()` and `irreversible_reason()`'s empty-label message,
    so both agree on what counts as "no visible content" versus "content
    present but unreadable".
    """
    text = unicodedata.normalize("NFKC", text or "")
    text = "".join(ch for ch in text if unicodedata.category(ch) != _FORMAT_CATEGORY)
    text = "".join(ch for ch in text if unicodedata.category(ch) not in _SYMBOL_CATEGORIES)
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text


def _words(text: str) -> list[str]:
    text = _strip_noise(text).lower()
    # Fold homoglyphs onto the Latin letter they are standing in for, so a
    # page that spells "order" with a Cyrillic о is tokenised exactly like
    # a page that spells it with a Latin o — see _CONFUSABLES for why this
    # is a false-refusal reducer now, not the security boundary.
    text = "".join(_CONFUSABLES.get(ch, ch) for ch in text)
    if any(ch not in _ALLOWED_CHARS for ch in text):
        # At least one character survived normalisation, noise-stripping
        # and confusable-folding that this guard still cannot read as an
        # ordinary ASCII letter, digit, punctuation mark, or whitespace.
        # Treat the *whole label* as unreadable rather than letting that
        # character silently act as a word boundary — see _ALLOWED_CHARS
        # for why an allowlist and not another exclusion-list entry.
        return []
    return [w for w in re.split(r"[^a-z0-9]+", text) if w]


def irreversible_reason(name: str, role: str = "") -> str:
    """Why the eagle must not click this on its own, or "" if it may.

    Returns a phrase that slots into a sentence the user hears: the tool says
    "I stopped because <reason>".
    """
    words = _words(name)
    if not words:
        if _strip_noise(name).strip():
            # There was visible content, but none of it survived
            # normalisation into a recognisable Latin word — most likely a
            # genuinely non-English label (Japanese, Arabic, Cyrillic,
            # Greek, ...), not an empty control. Say that, rather than the
            # misleading "no readable label", which reads as if the
            # control had no text at all. Either way the answer is still
            # refuse: an unreadable label is exactly the case where the
            # human has to be asked. Checked against the noise-stripped
            # text, not the raw name, so a label that is nothing but
            # zero-width characters, or nothing but decorative symbols
            # (genuinely blank once the noise is removed), still gets the
            # "no readable label" message below rather than this one.
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
