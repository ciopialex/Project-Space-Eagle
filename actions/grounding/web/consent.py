"""The line the eagle does not cross by itself.

v1 has no fresh-explicit-yes gate for the web. Until it does, controls that
commit something are refused outright rather than clicked and apologised for.
The Constitution already requires that irreversible decisions be paused and
escalated to the human; this is that clause, made checkable.

The list is a heuristic and will be wrong in both directions. It errs toward
refusing, because the cost of one unnecessary question is a sentence and the
cost of one unnecessary payment is a payment.

The vocabulary this file matches against (`_COMMITTING`, `_COMMITTING_PAIRS`,
`_READ_ONLY_LABELS`, `_BENIGN_PREFIXES`, ...) is ASCII words, and — as of
Task 6d — it covers three languages: English, Romanian, and Spanish. English
is the most complete. Romanian and Spanish now cover imperative, infinitive,
and (Romanian) nominalised/supin registers for the acts named in the Task
6c/6d briefs, plus payment-noun phrasing, but neither is a claim of
completeness the way "first-class" would imply: both are heuristic
vocabularies built against named examples, not an exhaustive grammar. Every
non-Latin script (Russian, Greek, Japanese, Arabic, Hebrew, Chinese, Korean,
...) is refused outright *unless* every character in the label happens to
have an entry in `_CONFUSABLES` — in that case it folds to ASCII and is read
like any other label (see `_CONFUSABLES`'s docstring for why this is not, in
fact, an "an incomplete table can never turn a refusal into an allow"
guarantee; that specific sentence was wrong and has been corrected). Where no
full fold is available, the guard refuses via the "not in a script this
guard can read" branch, asking the human about every control on that site.

The gap this task closed, and the gap that remains, are different shapes.
Before Task 6c, a Latin-script language the guard had no vocabulary for was
*worse off* than a non-Latin one: Task 6b's diacritic folding (NFD + strip
category `Mn`) makes `Plătește`, `Cumpără`, `Pagar`, `Confirmar` etc. read as
clean ASCII words, which means they pass the "is this readable" check, match
nothing in an English-only `_COMMITTING`, and fall through to ALLOW — a
silent fail-open, not a refusal. Adding Romanian and Spanish vocabulary
closes that hole for those two languages specifically. It does **not**
close it in general: any other Latin-script language with no vocabulary
entry — Italian, French, Portuguese, German, Polish, Turkish, Vietnamese,
and every other language that folds to readable ASCII — still fails open the
same way a pay button in Romanian used to. `Plătește` refuses now because
`plateste` is in the table; `Paga` (Italian "pay") still does not, because
nothing put it there. This is a real, live limitation: it is not fixed by
this task, only narrowed to the two languages named in the brief. Extending
it further — Italian, French, Portuguese, German, or any other language — is
future work, not attempted here except where explicitly noted below.

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

Task 6d added a third mechanism to how the label text itself is read, not a
fifth matching mechanism: `irreversible_reason()` now tokenises the label
**twice** — once treating Unicode noise characters (invisible marks, symbols,
non-standard spaces) as removed, and once treating them as word separators —
and refuses if *either* reading matches. See `_words()` / `_words_split()`
and `irreversible_reason()` for why: the two readings disagree exactly when a
noise character sits in the middle of a committing word, and which reading
is "correct" depends on normalisation order in a way that turned out not to
be safe to reason about in advance for every current and future Unicode
character class. Checking both is the fix, not picking the "right" one.
"""
from __future__ import annotations

import re
import string
import unicodedata

#: Homoglyph -> Latin. This is a false-refusal *reducer*, not the guard's
#: security boundary — see `_ALLOWED_CHARS` below for where the actual
#: boundary lives. Folding "Оrder" to "order" lets the verb scan give the
#: specific, accurate reason ("it places an order") instead of the generic
#: "it is not in a script this guard can read" a label with an un-mapped
#: homoglyph falls back to.
#:
#: What this table does NOT guarantee, corrected in Task 6d after review
#: found the earlier claim false: it is not true that "an incomplete table
#: can never turn a refusal into an allow." A whole Cyrillic or Greek word
#: made *only* of characters this table happens to map — not the committing
#: words in this guard's vocabulary, just words that happen to be spelled
#: entirely from this table's letters — folds to a legible ASCII string and
#: is read like ordinary text: `Театр` (theatre) -> `teatp`, `Тост` (toast)
#: -> `toct`, `Νόρα` (a name) -> `vopa`. None of those are committing words,
#: so they still ALLOW correctly, but a *bigger* table would have covered
#: `Театр` too and produced the exact same (correct) outcome, while a
#: *smaller* table (missing one of т/е/а/р) would have left a character
#: unfolded, failed `_ALLOWED_CHARS`, and refused the whole label instead.
#: So table size does change the refuse/allow line for non-Latin labels —
#: it is simply that, for the realistic committing vocabulary this guard
#: actually watches for, no such coincidental full fold onto a committing
#: word has been found (checked by hand, see the task report). The digit
#: half of the table is the one part of this file where an incomplete table
#: is a live, provable safety gap rather than a documentation nuance: see
#: below.
#:
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
#: look-alike. It does not need to be exhaustive to be safe in the sense
#: that matters most: any character this table doesn't fold, and that
#: isn't plain ASCII either, fails the `_ALLOWED_CHARS` check below and
#: refuses the whole label. See the report for the neighbourhood probe
#: this was checked against.
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
#: exists only to affect rendering, never to be part of a word.
_FORMAT_CATEGORY = "Cf"

#: Symbol categories: So (Other Symbol — pictographs, dingbats, currency
#: symbols like ™/©/★, and the large majority of standalone emoji), Sk
#: (Modifier Symbol), Sm (Math Symbol — this is where arrows like ← → live,
#: not punctuation), Sc (Currency Symbol). An emoji cannot homoglyph a
#: Latin letter, so nothing is lost by treating these as noise rather than
#: real word content — see `_SYMBOL_EXCEPTIONS` immediately below for the
#: nine characters carved back out of this set, and `_words()` /
#: `_words_split()` for how "noise" is now handled two ways rather than one.
_SYMBOL_CATEGORIES = frozenset({"So", "Sk", "Sm", "Sc"})

#: Task 6d, critical fix. Nine ASCII characters — `$ + < = > ^ \` | ~` — are
#: classified by Unicode as symbols (Sc/Sm/Sk) despite being ordinary ASCII
#: punctuation that is *already* in `string.punctuation` and therefore
#: already in `_ALLOWED_CHARS`. Before this fix, `_SYMBOL_CATEGORIES`
#: caught them too, so instead of falling through to the final
#: `[^a-z0-9]+` split as the harmless separators they have always been
#: (the same as `-`, `.`, `!`, or any other punctuation mark), they were
#: silently *removed* — fusing the words on either side of them into one
#: unrecognisable token: "Delete=all" -> "deleteall" (was two words,
#: became a fused non-word — an ALLOW that should have been a REFUSE, since
#: "delete" no longer appears as its own token). These nine were never the
#: false-refusal problem the symbol strip was built to solve (that was
#: emoji, arrows, and dingbats — none of which are ASCII, all of which are
#: still stripped); excluding them here restores their pre-existing
#: behaviour as ordinary punctuation/separators.
_SYMBOL_EXCEPTIONS = frozenset("$+<=>^`|~")

#: Unicode general category "Zs" ("Space Separator") — every
#: whitespace-like character that is *not* the ordinary ASCII space
#: (U+0020, deliberately excluded below): non-breaking space, en/em space,
#: hair space, and the rest of the U+2000 block's space variants. Task 6d,
#: critical fix: `P<hair space>ay now` reads as "Pay now" to a human — the
#: character is barely visible — but before this fix it survived
#: `unicodedata.normalize("NFKC", ...)`, which maps most Zs variants onto
#: U+0020, and then acted as an ordinary word-splitting space in the final
#: tokeniser: `_words()` returned `['p', 'ay', 'now']`, and "pay" never
#: appeared as its own token. Treating non-ASCII Zs the same way `Cf` is
#: treated (noise, not real word content) closes this the same way the
#: rest of `_strip_noise()` closes zero-width and symbol insertion: it is
#: removed in the "join" reading and kept as a separator in the "split"
#: reading (see `_words()` / `_words_split()`), so a label a human reads as
#: one word is tokenised as one word by at least one of the two readings.
_SPACE_CATEGORY = "Zs"

#: The actual security boundary. After all noise-handling below, if
#: anything remains that is not an ASCII letter, digit, ordinary
#: punctuation mark, or ASCII whitespace, the entire label is unreadable
#: and tokenises to no words at all — never to junk words. This closes
#: every homoglyph this guard doesn't recognise, every combining mark, and
#: every script mixture, at the cost of such labels refusing outright rather
#: than being read. That cost is the point: refuse is the safe direction,
#: and "I cannot read this" must never again produce a partial read.
#:
#: What it does NOT close, despite an earlier version of this comment
#: claiming it closed "every invisible character, including ones no one has
#: found yet": noise characters are *stripped* by `_strip_noise()` before
#: this check ever runs, so an invisible character never reaches the
#: allowlist at all. Review demonstrated the gap — a hair space inside
#: "Pay now" survived both the join and the split reading. That is closed by
#: `_mixed_readings()`, not here. The two mechanisms are separate and both
#: are load-bearing; neither is a superset of the other.
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

    # --- Romanian (Task 6c, extended Task 6d) --------------------------
    # Stored ASCII-folded, because `_words()` runs NFD + strips category
    # `Mn` before matching, so a page's "Plătește" and "Cumpără" arrive
    # here as "plateste" and "cumpara" — an entry containing ă, â, î, ș or
    # ț would never match anything. Verified against `_words()` directly
    # (see the task report), not assumed.
    "plateste": "it pays something",
    "platiti": "it pays something",
    # "plata" (payment, noun) and "achita"/"achită" (pay, verb) — Task 6d.
    # "Realizează plata" / "Efectuează plata" / "Achită acum" contain no
    # confirm verb at all, so relying on "confirma" alone (Task 6c's
    # reasoning) left the whole payment-noun/payment-verb register open.
    "plata": "it pays something",
    "achita": "it pays something",
    "cumpara": "it buys something",
    "cumparati": "it buys something",
    # "comanda" is Romanian for "order" and is exactly as ambiguous —
    # both the imperative verb ("Comandă acum" / order now) and the noun
    # ("Detalii comandă" / order details). Treated as bare-committing the
    # same way English "order" is, with the noun uses whitelisted in
    # `_READ_ONLY_LABELS` rather than exempted here — same mechanism,
    # same reasoning as the English order/order-details split.
    "comanda": "it places an order",
    "trimite": "it sends something",
    "sterge": "it deletes something",
    # "stergere" (deletion) — the nominalised/supin form of "șterge",
    # Task 6d: the dominant register for destructive settings copy
    # ("Ștergere cont"), mirroring why the imperative alone wasn't enough.
    "stergere": "it deletes something",
    "elimina": "it removes something",
    "confirma": "it confirms something that may not be undoable",
    "semneaza": "it signs something",
    "aboneaza": "it starts a subscription",
    # "abonare" (subscribing, noun/supin form) — Task 6d, same
    # nominalised-register reasoning as "stergere".
    "abonare": "it starts a subscription",
    "dezaboneaza": "it cancels a subscription",
    "retrage": "it withdraws funds",
    "transfera": "it transfers something",
    "doneaza": "it makes a donation",
    "liciteaza": "it places a bid",
    "rezerva": "it books something",
    "publica": "it publishes something",
    "posteaza": "it posts something publicly",
    "aplica": "it submits an application",
    # NOTE: "dezactiveaza" (deactivate) and its supin "dezactivare" are
    # intentionally NOT here — Task 6d moved them to `_COMMITTING_PAIRS`,
    # paired with cont/contul. See the pairs table for why.

    # --- Spanish (Task 6c, extended Task 6d) ----------------------------
    "pagar": "it pays something",
    "pague": "it pays something",
    # "paga" (imperative tú: "Paga ahora") and "pago" (payment, noun) —
    # Task 6d. Consumer-site imperative-tú copy ("Paga ahora", "Suscríbete",
    # "Borra mi cuenta", ...) is a different register from the infinitive
    # forms Task 6c covered, and was mostly failing open.
    "paga": "it pays something",
    "pago": "it pays something",
    "comprar": "it buys something",
    "compre": "it buys something",
    # "pedido" is Spanish for "order" (the noun) and carries the same
    # ambiguity "comanda" and English "order" do: "Realizar pedido" (place
    # order) commits, "Detalles del pedido" (order details) only reads.
    # Whitelisted in `_READ_ONLY_LABELS`, not exempted here — same
    # mechanism as the other two languages' order-word.
    "pedido": "it places an order",
    "confirmar": "it confirms something that may not be undoable",
    # "compra" (purchase, noun) — "Finalizar compra" (finalize/complete
    # purchase) has no other committing word in it; mirrors English
    # "purchase" being a bare noun/verb entry rather than needing
    # "finalizar" added as its own verb.
    "compra": "it makes a purchase",
    "enviar": "it sends something",
    "envia": "it sends something",  # imperative tú: "Envía el formulario"
    "eliminar": "it deletes something",
    "borrar": "it deletes something",
    "borra": "it deletes something",  # imperative tú: "Borra mi cuenta"
    "suscribirse": "it starts a subscription",
    "suscribete": "it starts a subscription",  # imperative tú: "Suscríbete"
    "retirar": "it withdraws funds",
    "retira": "it withdraws funds",  # imperative tú: "Retira fondos"
    "transferir": "it transfers something",
    "transfiere": "it transfers something",  # imperative tú
    "donar": "it makes a donation",
    "dona": "it makes a donation",  # imperative tú: "Dona ahora"
    "pujar": "it places a bid",
    "firmar": "it signs something",
    "firma": "it signs something",  # imperative tú: "Firma aquí"
    "publicar": "it publishes something",
    "solicitar": "it submits an application",
    # "aceptar"/"acepto" — Task 6d. English "accept" is bare-committing;
    # Spanish had no equivalent at all, so "Aceptar" and the very common
    # first-person checkbox copy "Acepto los términos" both failed open.
    "aceptar": "it accepts something on the user's behalf",
    "acepto": "it accepts something on the user's behalf",
    # NOTE: "desactivar" (deactivate) is intentionally NOT here — Task 6d
    # moved it to `_COMMITTING_PAIRS`, paired with cuenta/cuentas.
}

#: Verbs that are ordinary dismiss/undo actions on their own ("Close",
#: "Cancel") but commit something when their object co-occurs anywhere in
#: the label. Co-occurrence rather than adjacency, so "Close your account"
#: and "Close all accounts" are caught the same as "Close account", while
#: "Close menu" and "Cancel" alone are untouched.
#: Objects that make an otherwise-dismissive English verb committing. Shared
#: by every verb below that can mean either "get me out of this dialog" or
#: "end my relationship with this company", so the two senses cannot drift
#: apart per-verb the way they did when only `close` carried the account set.
_ACCOUNT_OBJECTS = {"account", "accounts", "membership", "profile"}
_SUBSCRIPTION_OBJECTS = {"subscription", "subscriptions", "plan", "membership"}

_COMMITTING_PAIRS = {
    # `close`, `cancel`, `disable`, `end` and `terminate` are all plausible
    # bare dismiss words ("Close", "Cancel", "End call"), so none is
    # bare-committing — but every one of them ends an account or a
    # subscription when it is pointed at one. Review found `Cancel account`,
    # `Disable account` and `End subscription` all allowed while
    # `Close account` refused, purely because only `close` had been given the
    # account set. Common English copy, so the sets are shared, not per-verb.
    "close": (_ACCOUNT_OBJECTS, "it closes an account"),
    "cancel": (_ACCOUNT_OBJECTS | _SUBSCRIPTION_OBJECTS,
               "it cancels an account or subscription"),
    "disable": (_ACCOUNT_OBJECTS, "it disables an account"),
    "end": (_ACCOUNT_OBJECTS | _SUBSCRIPTION_OBJECTS,
            "it ends an account or subscription"),
    # `terminate` is deliberately NOT here — it is already a bare committing
    # verb, and left that way. "Terminate process" / "Terminate instance"
    # would become benign if it were paired, and a word that strong is worth
    # a question even when its object is something other than an account.

    # --- Romanian (Task 6c, extended Task 6d) ---------------------------
    # "Închide" (close) is a dismiss word on its own — plausibly a bare
    # "Close" button on a Romanian site the same way English "Close" is —
    # so it is paired with its object rather than made bare-committing,
    # exactly mirroring why English "close" isn't in `_COMMITTING` either.
    "inchide": ({"cont", "contul"}, "it closes an account"),
    # "închidere" (closing, noun/supin) — Task 6d, same shape as "inchide"
    # but the nominalised register ("Închidere cont").
    "inchidere": ({"cont", "contul"}, "it closes an account"),
    # "Anulează" (cancel) has the same dismiss-word-on-its-own shape as
    # English "cancel" ("Anulează" alone is plausibly a Cancel button).
    "anuleaza": ({"abonament", "abonamentul"}, "it cancels a subscription"),
    # "Renunță" (give up / cancel) — Task 6d. "Renunță la abonament" is a
    # common Romanian phrasing for cancelling a subscription, but "Renunță"
    # alone is a plausible generic "never mind" dismiss word the same way
    # "cancel" and "close" are, so it is paired rather than bare.
    "renunta": ({"abonament", "abonamentul"}, "it cancels a subscription"),
    # "Dezactivează" (deactivate) — Task 6d, moved out of `_COMMITTING`.
    # The previous round made this bare, mirroring English "deactivate"
    # being bare; review found that wrong, because Romanian (like Spanish)
    # uses the same verb for account deactivation *and* ordinary toggles
    # ("Dezactivează notificările" / disable notifications), a distinction
    # English keeps separate ("disable" vs "deactivate"). Paired with the
    # account object instead, the same shape as close/cancel.
    "dezactiveaza": ({"cont", "contul"}, "it deactivates an account"),
    # "dezactivare" (deactivation, noun/supin) — same pairing, nominalised
    # register ("Dezactivare cont").
    "dezactivare": ({"cont", "contul"}, "it deactivates an account"),

    # --- Spanish (Task 6c, extended Task 6d) -----------------------------
    # "Cerrar" is both "close" (a dismiss word, and also "Cerrar sesión" /
    # sign out — benign) and, paired with "cuenta", "close account". A
    # bare "cerrar" entry would refuse "Cerrar sesión"; the pair does not.
    "cerrar": ({"cuenta", "cuentas"}, "it closes an account"),
    "cancelar": ({"suscripcion", "suscripciones"}, "it cancels a subscription"),
    # "Darse de baja" (unsubscribe) is an idiom with no single word that
    # unambiguously means "unsubscribe" on its own; "darse" bare would be
    # too broad ("to give oneself" / reflexive marker in many phrases), so
    # it is paired with "baja" the same way close/cancel are paired with
    # their objects, rather than either word being made bare-committing.
    "darse": ({"baja"}, "it cancels a subscription"),
    # "Dar de baja" (the non-reflexive form) — Task 6d. Review found this
    # is the *more* common phrasing on real sites than the reflexive
    # "Darse de baja" that Task 6c covered; "dar" (to give) is far too
    # broad to be bare-committing on its own, so paired the same way.
    "dar": ({"baja"}, "it cancels a subscription"),
    # "Desactivar" (deactivate) — Task 6d, moved out of `_COMMITTING` for
    # the same reason as Romanian "dezactiveaza" above: Spanish uses one
    # verb for both "disable" (toggles) and "deactivate" (accounts).
    "desactivar": ({"cuenta", "cuentas"}, "it deactivates an account"),
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

    # --- Romanian ---------------------------------------------------------
    "detalii comanda",       # Detalii comandă (order details)
    # Task 6d, Fix 6: parity with English's four "order ..." entries.
    "sumar comanda",          # Sumar comandă (order summary)
    "comanda mea",            # Comanda mea (my order)
    # Task 6d, Fix 7: "publica"/"aplica"/"rezerva" homographs that are not
    # the committing sense in these specific, exact phrases.
    "informatie publica",     # Informație publică (public information)
    "oferta publica",         # Ofertă publică (public offer)
    "aplica filtrul",         # Aplică filtrul (apply the filter)
    "aplica filtre",          # Aplică filtre (apply filters)
    "piese de rezerva",       # Piese de rezervă (spare parts)
    "rezerva de energie",     # Rezervă de energie (energy reserve)

    # --- Spanish ------------------------------------------------------
    "detalles del pedido",   # Detalles del pedido (order details)
    # Task 6d, Fix 6: parity with English's order/purchase whitelist.
    "estado del pedido",      # Estado del pedido (order status)
    "resumen del pedido",     # Resumen del pedido (order summary)
    "seguimiento del pedido",  # Seguimiento del pedido (order tracking)
    "confirmacion de pedido",  # Confirmación de pedido (order confirmation)
    "mi pedido",               # Mi pedido (my order)
    "detalles de la compra",   # Detalles de la compra (purchase details)
    "resumen de compra",       # Resumen de compra (purchase summary)
    "recibo de compra",        # Recibo de compra (purchase receipt)
    "mi compra",                # Mi compra (my purchase)
    # Task 6d, Fix 7: homographs that are not the committing sense here.
    "informacion publica",     # Información pública (public information)
    "retirar en tienda",       # Retirar en tienda (pick up in store)
    "solicitar informacion",   # Solicitar información (request information)
    "solicitar presupuesto",   # Solicitar presupuesto (request a quote)
}

#: Login/navigation prefixes that are benign on their own. Stripped as a
#: prefix (not matched as a whole label) so "Sign in with Google" is
#: allowed. The remainder is then run back through the full check below —
#: not a narrower, verb-only check — so "Sign in and place your order"
#: still hits the "order" verb and "Sign in and close your account" still
#: hits the close/account pair.
_BENIGN_PREFIXES = ["sign in", "log in", "sign out", "log out", "sign up"]


def _is_standalone_noise(ch: str) -> bool:
    """True for a character that is its own code point, carries no word
    content, and could plausibly sit *between* two otherwise-unrelated
    letters the way a real separator (space, hyphen) does: invisible
    "Format" (`Cf`) characters, decorative Symbol characters
    (`_SYMBOL_CATEGORIES`, minus the nine ASCII exceptions in
    `_SYMBOL_EXCEPTIONS`), and non-standard space separators
    (`_SPACE_CATEGORY`, minus the ordinary ASCII space itself).

    This is the set of characters for which "join" (remove) versus "split"
    (replace with a space) is a real, disputed question — see
    `_strip_noise()` for why both readings are checked. Deliberately
    excludes combining marks (`Mn`); see `_is_mark_noise()` for why those
    are handled separately rather than folded into this same set.
    """
    if ch == " ":
        return False
    category = unicodedata.category(ch)
    if category == _FORMAT_CATEGORY:
        return True
    if category in _SYMBOL_CATEGORIES:
        return ch not in _SYMBOL_EXCEPTIONS
    return category == _SPACE_CATEGORY


def _is_mark_noise(ch: str) -> bool:
    """True for a combining mark (`Mn`): an accent, cedilla, breve, or
    similar diacritic exposed by NFD-decomposing a precomposed letter
    ("café" -> "cafe" + a combining acute; "informație" -> "informatie" +
    a combining comma-below on the "t").

    Unlike `_is_standalone_noise()`, a combining mark is never treated as a
    separator in either tokenisation reading — it is always removed. A
    combining mark rides on the base character immediately before it; a
    human reading "informație" never perceives the comma-below on the "ț"
    as splitting the word into two, so a "split" reading that turned it
    into a space would manufacture a word boundary a human doesn't see —
    exactly the failure this file exists to prevent, just introduced by
    this task's own fix instead of by the site being browsed. Checked by
    hand: no named attack in this task's brief or the two before it
    depends on treating Mn as a separator (the Sk-diacritic bug is fixed
    by stripping the *standalone* Sk character before NFKC has a chance to
    decompose it into space + Mn — see `_strip_noise()` — not by splitting
    on the Mn afterward), so this costs nothing against the attacks this
    task closes.
    """
    return unicodedata.category(ch) == "Mn"


def _replace_noise(text: str, replacement: str) -> str:
    out = []
    for ch in text:
        if _is_mark_noise(ch):
            continue
        out.append(replacement if _is_standalone_noise(ch) else ch)
    return "".join(out)


def _strip_noise(text: str, *, replacement: str = "") -> str:
    """Normalise `text` and neutralise every noise character in it, either
    by removing it (`replacement=""`, the "join" reading) or by turning it
    into a literal space (`replacement=" "`, the "split" reading).

    Task 6d, critical fix. The previous pipeline ran
    `unicodedata.normalize("NFKC", text)` *first* and stripped noise
    categories afterward. That ordering has a bug a prior round's safety
    argument missed: NFKC's compatibility decomposition of the
    spacing-diacritic family (Unicode category `Sk` — dead-key accents
    like ´ ACUTE ACCENT, ¨ DIAERESIS, ˜ SMALL TILDE, and 17 Greek
    equivalents) is `SPACE + combining mark`, not the character itself.
    By the time the old pipeline's symbol-strip ran, the `Sk` character
    had already been replaced by a *space* the strip never sees — the
    combining mark that's left gets removed by the later `Mn` strip, and
    the space it was riding on is left behind as a brand-new word
    boundary. `"P´ay now"` tokenised to `['p', 'ay', 'now']` — the
    pipeline manufactured a word boundary out of a character it believed
    it was deleting.

    Fixed two ways, per the review, rather than by re-ordering and hoping
    no other category has the same problem:

    1. Noise is now stripped on the *raw* text first, before `NFKC` runs
       at all — an `Sk` character removed before normalisation never gets
       the chance to decompose into `SPACE + Mn` in the first place. This
       closes the specific bug above.
    2. `_words()` and `_words_split()` below tokenise the label *twice* —
       once with noise removed (this function's default), once with noise
       replaced by an explicit separator — and `irreversible_reason()`
       refuses if *either* reading matches a committing word. This is the
       robust fix: it does not depend on correctly reasoning about every
       Unicode normalisation quirk, current or future, that could turn a
       "noise" character into an accidental word-joiner or a missed word
       boundary. It is provably safe in the direction that matters: the
       split reading can only ever add refusals (a word a human reads as
       one token gets counted as two, so it stops matching a bare verb it
       used to match) or leave the outcome unchanged; it can never turn an
       already-correct refusal into an allow, because the join reading is
       still checked too and still refuses whenever it did before.

    NFKC still runs (it collapses fullwidth forms and other compatibility
    variants down to the characters they display as), and the noise pass
    still runs again after NFKC and after NFD, in case normalisation itself
    introduces new noise characters (a documented, if rare, possibility) or
    exposes new combining marks (precomposed "café" decomposing to "cafe" +
    a combining acute under NFD, the diacritic-recovery behaviour from an
    earlier round). Running the pass three times is idempotent and cheap;
    it is the *first* pass, on unnormalised text, that closes the ordering
    bug — the later passes are unchanged safety nets from before.
    """
    text = text or ""
    text = _replace_noise(text, replacement)
    text = unicodedata.normalize("NFKC", text)
    text = _replace_noise(text, replacement)
    text = unicodedata.normalize("NFD", text)
    text = _replace_noise(text, replacement)
    return text


#: Punctuation that can sit inside a word without a reader noticing much:
#: "De.lete", "B-u-y", "Pur_chase". Every one of these is a word boundary to
#: `re.split`, which is exactly why they hid a committing word from every
#: reading this guard had.
_INWORD_PUNCT = r".\-_'’·*~^|/\\,;:+="

#: Only *between* two word characters. A leading or trailing mark is ordinary
#: punctuation ("Delete." / "-Delete"), already handled by the normal split;
#: removing those too would merge genuinely separate words across a space.
_DEPUNCT_RE = re.compile(rf"(?<=\w)[{_INWORD_PUNCT}]+(?=\w)")


def _tokenise(text: str, *, replacement: str, depunct: bool = False) -> list[str]:
    text = _strip_noise(text, replacement=replacement).lower()
    # Fold homoglyphs onto the Latin letter they are standing in for, so a
    # page that spells "order" with a Cyrillic о is tokenised exactly like
    # a page that spells it with a Latin o — see _CONFUSABLES for why this
    # is a false-refusal reducer now, not the security boundary.
    text = "".join(_CONFUSABLES.get(ch, ch) for ch in text)
    if any(ch not in _ALLOWED_CHARS for ch in text):
        # At least one character survived normalisation, noise-handling
        # and confusable-folding that this guard still cannot read as an
        # ordinary ASCII letter, digit, punctuation mark, or whitespace.
        # Treat the *whole label* as unreadable rather than letting that
        # character silently act as a word boundary — see _ALLOWED_CHARS
        # for why an allowlist and not another exclusion-list entry.
        return []
    if depunct:
        # The third reading: punctuation *inside* a word is deleted rather
        # than treated as a boundary, so "De.lete" reads as "delete". Kept as
        # one extra tokenisation instead of adding these marks to the noise
        # set, because each noise character doubles the mixed readings and
        # trips the _MAX_NOISE_CHARS bound - an ordinary hyphenated label
        # would start being refused as "too decorative to read".
        text = _DEPUNCT_RE.sub("", text)
    return [w for w in re.split(r"[^a-z0-9]+", text) if w]


def _words(text: str) -> list[str]:
    """Tokenise `text` treating noise characters as removed — the "join"
    reading. See `_strip_noise()` and `irreversible_reason()` for why this
    is checked alongside, never instead of, `_words_split()`.
    """
    return _tokenise(text, replacement="")


def _words_depunct(text: str) -> list[str]:
    """Tokenise `text` with in-word punctuation removed - the "depunctuated"
    reading. See `_DEPUNCT_RE` and `irreversible_reason()`.
    """
    return _tokenise(text, replacement="", depunct=True)


def _words_split(text: str) -> list[str]:
    """Tokenise `text` treating noise characters as word separators — the
    "split" reading. See `_strip_noise()` and `irreversible_reason()`.
    """
    return _tokenise(text, replacement=" ")


#: How many noise characters a label may contain before this guard stops
#: trying to read it. Each one doubles the number of readings below, so the
#: cost is bounded at 2**8 = 256 cheap tokenisations. A label carrying nine
#: or more invisible or decorative characters is not ordinary button copy,
#: and at that point the noise is itself the signal — see `_mixed_readings`.
_MAX_NOISE_CHARS = 8


def _mixed_readings(text: str):
    """Every *mixed* join/split reading of `text`, or None if it is too noisy.

    `_words()` and `_words_split()` between them cover only the two extremes:
    every noise character joins, or every noise character splits. A label can
    defeat both at once by needing *different* answers for different
    characters. Review demonstrated it on the final code:

        "P ay now"   renders as "Pay now"
          join  -> ['paynow']        no committing word
          split -> ['p','ay','now']  no committing word
          -> ALLOWED

    The reading a human actually gets is mixed: the first hair space joins
    (`P` + `ay` = "pay"), the second splits ("pay" | "now"). That reading is
    `['pay','now']`, which refuses. Since which characters join and which
    split is exactly what the attacker chooses, the only honest answer is to
    try every combination rather than guess.

    Only the *raw* noise characters are enumerated — that is where an
    interleaving attack lives, and it keeps the count bounded and stable.
    Noise that normalisation introduces later is still handled by the two
    extreme readings, which are checked separately and unchanged.

    Returns an iterator of word lists, or None when the label carries more
    than `_MAX_NOISE_CHARS` noise characters — the caller refuses in that
    case rather than either enumerating 2**n readings or silently checking
    only some of them.
    """
    raw = text or ""
    # Only noise *between two alphanumeric characters* can change the reading.
    # A noise character at either end of the label, or next to a space or
    # another noise character, is either a separator or a no-op in every
    # reading — it can never fuse two word fragments into a word that was not
    # already there. Enumerating those would be pure cost, and it is what made
    # ordinary decorated copy hit the bound: "⭐⭐⭐⭐⭐⭐⭐⭐⭐ Reviews" has nine
    # noise characters and not one of them is interior, so it needs no
    # enumeration at all and must not be refused for being "too noisy".
    positions = [i for i, ch in enumerate(raw)
                 if _is_standalone_noise(ch)
                 and 0 < i < len(raw) - 1
                 and raw[i - 1].isalnum() and raw[i + 1].isalnum()]
    if len(positions) > _MAX_NOISE_CHARS:
        return None
    if len(positions) < 2:
        # Nothing to mix: zero or one noise character is fully covered by the
        # join and split readings the caller already checked.
        return iter(())

    def _readings():
        # 0 is the all-join reading and the final mask is all-split; both are
        # already checked by the caller, so only the genuinely mixed masks
        # between them are generated here.
        for mask in range(1, 2 ** len(positions) - 1):
            chars = list(raw)
            for bit, pos in enumerate(positions):
                chars[pos] = " " if (mask >> bit) & 1 else ""
            yield _tokenise("".join(chars), replacement="")

    return _readings()


def _reason_for_words(words: list[str]) -> str:
    """The committing-or-not decision for an already-tokenised label. Pulled
    out of `irreversible_reason()` so it can be run once per tokenisation
    reading (see that function) without re-tokenising the label each time a
    benign prefix is stripped — the prefix recursion below operates on the
    word list directly rather than reconstructing and re-splitting a string.
    """
    if not words:
        return ""

    for prefix in _BENIGN_PREFIXES:
        prefix_words = prefix.split()
        n = len(prefix_words)
        if words[:n] == prefix_words:
            # Re-run the complete check on what's left of the label, rather
            # than a narrower verb-only scan. A benign login prefix should
            # not shield the remainder from anything a bare label would be
            # caught by (read-only whitelist, pairs, verbs).
            return _reason_for_words(words[n:])

    label = " ".join(words)
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


def irreversible_reason(name: str, role: str = "") -> str:
    """Why the eagle must not click this on its own, or "" if it may.

    Returns a phrase that slots into a sentence the user hears: the tool says
    "I stopped because <reason>".

    Tokenises the label two ways (see `_strip_noise()`) and refuses if
    either reading matches a committing word — join-then-split, never
    split-then-join, so the more specific reason (from the reading that
    doesn't fragment an intact committing word) is preferred when both
    readings happen to match.
    """
    words_join = _words(name)
    words_split = _words_split(name)

    if not words_join and not words_split:
        if _strip_noise(name).strip():
            # There was visible content, but none of it survived
            # normalisation into a recognisable Latin word — most likely a
            # genuinely non-English label (Japanese, Arabic, Cyrillic,
            # Greek, ...), not an empty control. Say that, rather than the
            # misleading "no readable label", which reads as if the
            # control had no text at all.
            return ("it is not in a script this guard can read, so there is "
                    "no way to tell what it does")
        return ("it has no readable label, so there is no way to tell what it "
                "does")

    reason = (_reason_for_words(words_join)
              or _reason_for_words(words_split)
              or _reason_for_words(_words_depunct(name)))
    if reason:
        return reason

    # Neither extreme reading matched. The label may still read as committing
    # if some noise characters join and others split — see `_mixed_readings`.
    mixed = _mixed_readings(name)
    if mixed is None:
        return ("it carries too many invisible or decorative characters to "
                "read reliably")
    for words in mixed:
        reason = _reason_for_words(words)
        if reason:
            return reason
    return ""
