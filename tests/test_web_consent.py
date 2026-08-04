"""What the eagle will not click on its own.

The motivating example — filing taxes — ends in an act that cannot be undone.
Every other guard in this system is about doing the right thing; this one is
about not being the thing that decides.

The list is deliberately name-based rather than structural. Basing it on "is
this a form submit" would refuse the search box on every site in the world,
which trains everyone to switch it off. Basing it on what the button SAYS
refuses the small number of controls that actually commit something.

Three prior rounds each fixed the case they were shown and broke a
neighbour. The two big parametrised lists below (`MUST_REFUSE`,
`MUST_ALLOW`) are the pinned, adversarially-checked behaviour from the round
that closed the `order`/`sign` holes — keep them in sync with any future
change instead of narrowing them to make a change pass.
"""
import pytest

from actions.grounding.web.consent import _CONFUSABLES, _words, irreversible_reason


# ---------------------------------------------------------------------------
# Pinned behaviour (fix round 3). Real button copy from checkout, e-signature
# and account-closure flows, plus the "sign in and <commit>" combinations
# that a benign login prefix must not shield.
# ---------------------------------------------------------------------------

MUST_REFUSE = [
    # "order" as a bare verb — defeats the insertion attack that broke the
    # exact-phrase whitelist ("place order" vs "place your order").
    "Place order", "Place your order", "Place my order",
    "Complete order", "Complete your order",
    "Order", "Order now", "Order this",
    # "sign" as a bare verb, including the gerund — DocuSign/Adobe Sign copy.
    "Sign agreement", "Sign contract", "Sign lease", "Sign here", "Sign this",
    "Sign now", "Click to sign", "Finish signing", "Review and sign",
    # "close" + "account"/"accounts" co-occurring anywhere in the label.
    "Close account", "Close my account", "Close your account",
    "Close all accounts",
    # A benign login prefix must not shield a committing remainder.
    "Sign in and pay", "Sign in and place your order",
    "Sign in and complete order", "Sign in and sign contract",
    "Sign in and close your account",
    # Verb + reading-word bigrams that the old "reading word anywhere"
    # exemption used to let through.
    "Confirm payments", "Delete history", "Erase history", "Delete settings",
    "Send payments", "Confirm purchases", "Submit details", "Delete orders",
    # Literal Klarna/Afterpay/Affirm checkout copy.
    "Pay in full", "Pay in 4 interest-free payments",
    # cancel/subscribe family.
    "Cancel subscription", "Unsubscribe",
    # Remaining committing verbs.
    "Withdraw funds", "Donate now", "Wipe device", "Place bid",
    "Deactivate account", "Terminate account", "Erase all data",
    "Authorize payment",
]

MUST_ALLOW = [
    # Bare "close" and ordinary dismiss controls — must survive close being
    # a committing word when paired with "account".
    "Close", "Close menu", "Close dialog", "Close window",
    "Close notification", "Close tab", "Close friends",
    # Login variants — the benign-prefix remainder must not be searched with
    # a narrower rule than a bare label would get.
    "Sign in", "Sign in with Google", "Sign in with Apple",
    "Sign in to your account", "Sign in to continue", "Sign in with email",
    "Log in with password",
    # Read-only record labels — "order" and "purchase" are committing verbs,
    # so these rely on the whole-label whitelist, not on a reading-word veto.
    "Payment history", "Order history", "Order details", "Order summary",
    "Order status", "Purchase history", "Purchase details",
    "Billing history", "Transfer history", "Your orders", "Purchases",
    "Saved payment methods",
    # Ordinary navigation.
    "Search", "Add to cart", "Settings", "Account settings", "Cancel",
    "Back", "Next", "Show password", "Load more", "Filter", "Sort by date",
    "Home",
]


@pytest.mark.parametrize("name", MUST_REFUSE)
def test_pinned_must_refuse(name):
    assert irreversible_reason(name, "button") != "", name


@pytest.mark.parametrize("name", MUST_ALLOW)
def test_pinned_must_allow(name):
    assert irreversible_reason(name, "button") == "", name


# ---------------------------------------------------------------------------
# Regression tests for round 1 and round 2 fixes, which previously had no
# dedicated test of their own — a later round could silently re-break any of
# these with the rest of the suite green.
# ---------------------------------------------------------------------------

def test_regression_round1_klarna_style_pay_phrasing_is_refused():
    # Round 1 fix: "in" was in a global exemption set, allowing "Pay in
    # full" through. Also covered by MUST_REFUSE, pinned here by name.
    assert irreversible_reason("Confirm payments", "button") != ""


def test_regression_round2_close_menu_is_allowed():
    # Round 2 fix: "close" as a bare committing verb refused every dismiss
    # control on the web. Also covered by MUST_ALLOW, pinned here by name.
    assert irreversible_reason("Close menu", "button") == ""


def test_regression_round2_sign_in_with_google_is_allowed():
    # Round 2 fix: exact-match benign labels refused every "Sign in with
    # ..." variant. Also covered by MUST_ALLOW, pinned here by name.
    assert irreversible_reason("Sign in with Google", "link") == ""


# ---------------------------------------------------------------------------
# Coverage carried forward from the brief and earlier rounds.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "Pay now", "Complete purchase", "Buy it now", "Checkout",
    "Transfer funds", "Submit return", "File my taxes", "Confirm and pay",
    "Delete account", "Send payment", "Accept and continue", "Subscribe",
    "Confirm order details", "Submit payment details",
    "Pay with saved payment method", "Delete all history",
])
def test_controls_that_commit_something_are_refused(name):
    assert irreversible_reason(name, "push button") != ""


@pytest.mark.parametrize("name", [
    "Search", "Sign in", "Next", "Home", "Settings", "Play",
    "Load more", "Filter", "Sort by date", "Cancel", "Back",
    "Show password", "Add to cart",
])
def test_ordinary_navigation_is_not_refused(name):
    assert irreversible_reason(name, "push button") == ""


def test_the_reason_is_plain_language_the_user_can_act_on():
    reason = irreversible_reason("Confirm and pay", "push button")
    assert "pay" in reason.lower()
    assert reason == reason.strip() and len(reason) < 200


def test_matching_ignores_case_and_punctuation():
    assert irreversible_reason("  PAY   NOW!  ") != ""
    assert irreversible_reason("Place-Order") != ""


def test_a_word_inside_another_word_does_not_trip_it():
    # "payment history" is a page, not a payment.
    assert irreversible_reason("Payment history", "link") == ""
    assert irreversible_reason("Order history", "link") == ""
    assert irreversible_reason("Purchases", "link") == ""
    assert irreversible_reason("Confirm order details", "button") != ""
    assert irreversible_reason("Submit payment details", "button") != ""


def test_benign_prefix_remainder_is_checked_not_exempted():
    assert irreversible_reason("Sign in", "link") == ""
    assert irreversible_reason("Login", "link") == ""
    assert irreversible_reason("Sign out", "link") == ""
    assert irreversible_reason("Sign in and pay", "button") != ""


def test_an_empty_name_is_refused_because_we_cannot_tell_what_it_does():
    assert irreversible_reason("", "push button") != ""


def test_links_that_merely_read_are_allowed():
    assert irreversible_reason("Your orders", "link") == ""


@pytest.mark.parametrize("name", [
    "Unsubscribe", "Deactivate account", "Terminate service",
    "Erase data", "Wipe device", "Withdraw funds",
    "Authorize payment", "Authorise payment", "Donate now", "Place bid",
])
def test_each_new_verb_added_in_fix_round_is_caught(name):
    """Coverage for each verb added to catch vocabulary gaps (I2, Fix Round 2)."""
    assert irreversible_reason(name, "button") != ""


def test_confirm_verb_specifically_pinned():
    """Confirm must refuse even without another verb to hide behind (I2, Fix Round 2)."""
    assert irreversible_reason("Confirm", "button") != ""
    assert "confirm" in irreversible_reason("Confirm", "button").lower()


# ---------------------------------------------------------------------------
# Task 6b: Unicode hardening.
#
# The label text comes from the page being browsed, so the site controls it.
# Pre-fix, `_words()` split on `[^a-z0-9]+` after `.lower()`, which treats
# every non-ASCII character as a delimiter. A single homoglyph therefore
# shattered the token it appeared in and the label was allowed — e.g.
# "Оrder now" (Cyrillic О) tokenised to ["rder", "now"], neither of which is
# a committing word. Measured: all 46 MUST_REFUSE labels above become
# ALLOWED under whole-label Cyrillic substitution. This section is the
# discrimination evidence and the attack-form coverage for the fix
# (NFKC normalisation, Cf-category stripping, and a hand-rolled confusables
# fold — see consent.py for the design write-up).
# ---------------------------------------------------------------------------

#: Built from the implementation's own `_CONFUSABLES` table (Cyrillic/Greek
#: entries only — the digit-lookalikes are exercised separately below) so
#: this helper can never silently drift out of sync with the guard: if a
#: mapping is ever added to or removed from consent.py, this inverted table
#: picks up the change automatically instead of the test data going stale.
_LATIN_TO_HOMOGLYPH = {}
for _src, _tgt in _CONFUSABLES.items():
    if not _src.isdigit():
        _LATIN_TO_HOMOGLYPH.setdefault(_tgt, _src)


def _cyrillicize(label: str) -> str:
    """Replace every character in `label` that has a known Latin->homoglyph
    mapping with its look-alike, leaving spaces, punctuation, digits, and
    letters with no table entry untouched. This deliberately does not
    substitute every letter — a real attacker only needs to touch enough of
    the label to defeat naive matching, and a partial substitution is also
    the harder case for the guard, since more genuinely-Latin text survives
    to look benign.
    """
    return "".join(_LATIN_TO_HOMOGLYPH.get(ch, ch) for ch in label.lower())


# The four attack forms named in the task brief, using its own examples.

def test_single_character_cyrillic_substitution_still_refuses():
    assert irreversible_reason("Оrder now", "button") != ""       # Cyrillic О
    assert irreversible_reason("Рay now", "button") != ""          # Cyrillic Р
    assert irreversible_reason("Deleтe account", "button") != ""   # Cyrillic т


def test_whole_label_cyrillic_substitution_still_refuses():
    assert irreversible_reason(_cyrillicize("Order now"), "button") != ""
    assert irreversible_reason(_cyrillicize("Pay now"), "button") != ""
    assert irreversible_reason(_cyrillicize("Delete account"), "button") != ""


def test_zero_width_space_insertion_still_refuses():
    assert irreversible_reason("Or​der now", "button") != ""
    # Zero-width joiner and zero-width non-joiner, not just zero-width space.
    assert irreversible_reason("Pa‍y now", "button") != ""
    assert irreversible_reason("De‌lete account", "button") != ""


def test_fullwidth_form_still_refuses():
    assert irreversible_reason("Ｐay now", "button") != ""     # fullwidth P
    assert irreversible_reason("Ｏrder now", "button") != ""   # fullwidth O


def test_mixed_unicode_attack_still_refuses():
    # Fullwidth P, a zero-width space, and a Cyrillic о in the same label —
    # the "combine techniques" case, not just one mechanism at a time.
    assert irreversible_reason("Ｐay​ nоw", "button") != ""


@pytest.mark.parametrize("name", MUST_REFUSE)
def test_pinned_must_refuse_survives_whole_label_cyrillic_substitution(name):
    # Driven from the exact same MUST_REFUSE data used by
    # test_pinned_must_refuse above, so attack coverage cannot silently
    # drift away from the pinned behaviour table.
    assert irreversible_reason(_cyrillicize(name), "button") != "", name


def test_digit_lookalike_substitution_still_refuses():
    # Leetspeak digit-for-letter substitution, e.g. zero for O. Not one of
    # the four named attack forms, but part of the same confusables table
    # and the same failure class.
    assert irreversible_reason("0rder n0w", "button") != ""
    assert irreversible_reason("de1ete account", "button") != ""


def test_non_latin_label_still_refuses_with_an_accurate_reason():
    # A genuinely non-English label ("Pay" in Japanese) is not an attack,
    # but the guard still cannot read it, so it must still refuse — that is
    # the safe direction, unchanged by this fix. What does change: the
    # reason string no longer claims the control "has no readable label"
    # (misleading — there plainly is one) when there is visible text that
    # simply isn't in a script this guard folds to Latin.
    reason = irreversible_reason("支払う", "button")
    assert reason != ""
    assert "script" in reason.lower()


def test_a_genuinely_empty_label_keeps_its_original_reason():
    reason = irreversible_reason("", "button")
    assert reason != ""
    assert "no readable label" in reason.lower()


def test_a_label_that_is_only_invisible_characters_gets_the_empty_label_reason():
    # A lone zero-width space has visible-looking text under a raw
    # `.strip()` (Cf characters aren't whitespace), but there is nothing
    # there once the invisible noise is removed. The reason-message check
    # must run on the Cf-stripped text, not the raw name, so this gets "no
    # readable label" (there is nothing) rather than "not in a script"
    # (there is something, just foreign) — the distinction fix round 1 was
    # asked to make.
    reason = irreversible_reason("​", "button")
    assert reason != ""
    assert "no readable label" in reason.lower()


# ---------------------------------------------------------------------------
# Task 6b, fix round 1: the exclusion-list design above (strip known-Cf
# invisibles, fold a partial confusables table, treat anything left over as
# a word) was reviewed and found to open a hole bigger than the one it
# closed. Root cause: any character neither stripped nor folded still fell
# through to the final `[^a-z0-9]+` split as an ordinary delimiter, so a
# *partial* fold of a whole-Cyrillic/Greek label left junk tokens behind
# instead of an empty list — which meant "I cannot read most of this" never
# hit the "no words -> refuse" fallback and fell through to the verb scan
# instead. Fixed by inverting the design to an allowlist (`_ALLOWED_CHARS`
# in consent.py): after normalisation, stripping and folding, ANY leftover
# character outside ASCII letters/digits/punctuation/whitespace makes the
# whole label unreadable — refuse, not junk-tokenise. This section pins the
# three bypass classes the review found, plus the confusables table itself
# (which the review also found was 77% deletable with the suite green,
# because the attack strings were generated by inverting the very table
# they were meant to catch tampering with).
# ---------------------------------------------------------------------------

# --- Critical 1: whole Cyrillic/Greek committing labels must refuse -------
# Reproduces the review's own measurement: real Russian/Greek button copy
# for "Pay", "Buy", "Delete account", "Place order", "Sign contract",
# "Withdraw funds" — words this guard cannot translate, but which must
# never come back allowed just because they don't parse as English.

@pytest.mark.parametrize("name", [
    "Оплатить",           # Pay (Russian)
    "Купить",             # Buy (Russian)
    "Удалить аккаунт",    # Delete account (Russian)
    "Оформить заказ",     # Place order (Russian)
    "Подписать договор",  # Sign contract (Russian)
    "Вывести средства",   # Withdraw funds (Russian)
    "Πληρωμή",            # Pay (Greek)
])
def test_whole_cyrillic_or_greek_committing_labels_refuse(name):
    assert irreversible_reason(name, "button") != "", name


# --- Critical 2: non-Cf invisible characters must not bypass the guard ----
# `_FORMAT_CATEGORY` only ever covered Unicode category Cf. These are
# invisible but NOT Cf, so the round-0 exclusion list let each of them
# shatter "order" the same way a zero-width space used to.

@pytest.mark.parametrize("label", [
    "Or͏der now",   # U+034F COMBINING GRAPHEME JOINER (Mn)
    "Or︎der now",   # U+FE0E VARIATION SELECTOR-15 (Mn)
    "Or️der now",   # U+FE0F VARIATION SELECTOR-16 (Mn)
    "Orᅟder now",   # U+115F HANGUL CHOSEONG FILLER (Lo)
    "Orㅤder now",   # U+3164 HANGUL FILLER (Lo)
    "Or⠀der now",   # U+2800 BRAILLE PATTERN BLANK (So)
])
def test_non_cf_invisible_characters_do_not_bypass_the_guard(label):
    assert irreversible_reason(label, "button") != "", label


# --- Critical 3: any unmapped letter must fail safe, not fall through -----
# A homoglyph the confusables table doesn't happen to name, a combining or
# precomposed diacritic, a script this guard has no vocabulary for at all —
# none of these are in `_CONFUSABLES`, so under the round-0 design they were
# just more delimiters. Under the allowlist, any one of them makes the
# whole label unreadable instead.

@pytest.mark.parametrize("label", [
    "Páy now",     # combining acute accent (Mn) on "a"
    "Páy now",      # precomposed a-with-acute (Latin-1)
    "Sıgn contract",     # Turkish dotless i (U+0131)
    "Sİgn contract",     # Turkish capital I-with-dot (U+0130)
    "ᴘay now",      # ᴘ LATIN LETTER SMALL CAPITAL P — no NFKC decomposition
    "ⲟrder now",    # ⲟ COPTIC SMALL LETTER O — visually identical to Latin o
    "Sigը contract",  # ը ARMENIAN SMALL LETTER YECH
    "Siɡn contract",  # ɡ LATIN SMALL LETTER SCRIPT G
])
def test_unmapped_letters_fail_safe_instead_of_falling_through(label):
    assert irreversible_reason(label, "button") != "", label


# --- Confusables table pinning, by hand, not by inverting the table -------
# The review found the table 77% unpinned: `_LATIN_TO_HOMOGLYPH` above is
# built by inverting `_CONFUSABLES`, so deleting a table entry also deletes
# the attack that would have caught the deletion — a tautology. These are
# typed out by hand instead, one per table entry, each attacking a real
# `_COMMITTING` word through exactly one substituted character, and each
# asserting the *specific* reason that entry is responsible for — not just
# non-empty. That distinction matters post-redesign: for the Cyrillic/Greek
# entries, deleting the mapping is now safe (the label still refuses, just
# with the generic "not in a script" reason, verified by hand against this
# exact data before committing); for the digit entries it is not — digits
# are already in `_ALLOWED_CHARS`, so an un-folded "0rder" reaches the verb
# scan as the literal token "0rder" and comes back ALLOWED. Both properties
# are pinned by the same assertion shape (the specific reason disappears
# without the entry); only the digit half is also a live safety boundary.
_CONFUSABLE_PINS = [
    # Cyrillic
    ("Pаy now", "pay"),                 # а U+0430
    ("Dеlete history", "delete"),       # е U+0435
    ("Оrder now", "order"),             # о U+043E
    ("Рurchase this", "purchase"),      # р U+0440
    ("acсept terms", "accept"),         # с U+0441
    ("Buу now", "buy"),                 # у U+0443
    ("Sіgn contract", "sign"),          # і U+0456
    ("ѕubmit details", "submit"),       # ѕ U+0455
    ("Тerminate account", "terminate"),  # т U+0442
    # Greek
    ("Pαy now", "pay"),                 # α U+03B1
    ("Οrder now", "order"),             # ο U+03BF
    ("Ρurchase this", "purchase"),      # ρ U+03C1
    # Digit-lookalikes
    ("0rder now", "order"),
    ("App1y now", "application"),
    ("D3lete account", "delete"),
    ("P4y now", "pay"),
    ("5ubmit details", "submit"),
    ("Submi7 details", "submit"),
    ("Su8scribe now", "subscri"),
]


@pytest.mark.parametrize("label, expected_substring", _CONFUSABLE_PINS)
def test_each_confusable_table_entry_produces_its_specific_reason(label, expected_substring):
    reason = irreversible_reason(label, "button")
    assert expected_substring in reason.lower(), (label, reason)


def test_confusable_letters_with_no_reachable_vocabulary_word_still_fold():
    # х (U+0445), ј (U+0458) and ν (U+03BD) don't appear in any current
    # _COMMITTING/_COMMITTING_PAIRS word, so there is no live label that
    # can pin them through irreversible_reason()'s reason text the way the
    # entries above are pinned. Pinned directly against the fold function
    # instead — still hand-typed, still not derived from _CONFUSABLES.
    assert _words("х") == ["x"]
    assert _words("ј") == ["j"]
    assert _words("ν") == ["v"]


# ---------------------------------------------------------------------------
# Task 6b, fix round 2: the round-1 allowlist correctly closed every attack,
# but it also refused ordinary English UI copy that merely carries an icon
# or arrow — "🛒 Add to cart", "← Back", "Sign in with Google →" — which is
# not decoration exclusive to commerce pages, it is how buttons are written
# across the modern web. `_words()` now also strips Symbol characters
# (`_SYMBOL_CATEGORIES`: So/Sk/Sm/Sc) and, via NFD + dropping category `Mn`,
# combining marks — recovering "café"/"Ordér" as their base Latin spelling
# and incidentally cleaning up variation selectors (also `Mn`) attached to
# emoji. Both strips are safe in the same direction: removing a character
# can only ever *join* two fragments into a more recognisable word, never
# split one recognisable word into less recognisable pieces — the opposite
# of round 0's bug, where an unrecognised character became a false word
# boundary. See `_SYMBOL_CATEGORIES` and `_strip_noise` in consent.py for
# the full reasoning.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "🛒 Add to cart",
    "🔍 Search",
    "⚙️ Settings",
    "← Back",
    "🏠 Home",
    "Add to cart 🛒",
    "★ Favourite",
    "Sign in with Google →",
])
def test_symbol_decorated_ordinary_labels_are_allowed(name):
    # The exact eight labels measured, by hand, as wrongly-refused on the
    # round-1 code.
    assert irreversible_reason(name, "button") == "", name


def test_diacritic_recovery_allows_an_ordinary_accented_label():
    # Round 1 left this refusing and documented the choice as acceptable
    # under the instruction at the time. Reversed this round: an accented
    # Latin label that isn't a committing word must read as ordinary text,
    # not as unreadable.
    assert irreversible_reason("Café menu", "button") == ""


@pytest.mark.parametrize("name, expected_substring", [
    # Diacritic recovery makes these refuse via the SPECIFIC verb reason
    # now (the label reconstructs to the real word), which is the more
    # robust and more informative path than the generic "not in a script"
    # fallback round 1 left them with.
    ("Páy now", "pay"),
    ("Ordér now", "order"),
])
def test_diacritic_recovery_gives_a_specific_reason_not_a_generic_one(name, expected_substring):
    reason = irreversible_reason(name, "button")
    assert reason != ""
    assert expected_substring in reason.lower()


def test_turkish_dotless_i_still_refuses_generically():
    # U+0131 LATIN SMALL LETTER DOTLESS I is a base letter, not a
    # precomposed-with-mark character, so NFD does nothing to it: no
    # combining mark to strip, no recovery possible. Must still refuse —
    # via the generic "not in a script" branch, since nothing reconstructs
    # it to "sign".
    reason = irreversible_reason("Sıgn contract", "button")
    assert reason != ""
    assert "script" in reason.lower()


@pytest.mark.parametrize("name, expected_substring", [
    # A symbol-decorated label that IS committing must still refuse, and
    # with the specific reason — proof that stripping the decoration lets
    # the verb scan see the real word, rather than merely making the
    # refuse/allow outcome accidentally line up.
    ("🔥 Buy now", "buy"),
    ("💳 Pay now", "pay"),
    ("✅ Confirm and pay", "pay"),
])
def test_symbol_decorated_committing_labels_refuse_with_the_specific_reason(name, expected_substring):
    reason = irreversible_reason(name, "button")
    assert reason != ""
    assert expected_substring in reason.lower(), (name, reason)


def test_braille_blank_still_refuses_now_via_the_specific_reason():
    # U+2800 BRAILLE PATTERN BLANK is category So, so the symbol strip
    # closes this case a second, cheaper way: "Or<blank>der now" becomes
    # "Order now" and is caught by the verb scan directly, rather than by
    # falling through to the unreadable branch the round-1 allowlist used.
    # Both paths refuse; this pins that the specific one now fires.
    reason = irreversible_reason("Or⠀der now", "button")
    assert reason != ""
    assert "order" in reason.lower()

# The full attack set (MUST_REFUSE / MUST_ALLOW, both Criticals sections,
# the confusables pins, the digit-lookalike and mixed-attack tests above)
# is re-asserted by simply running the existing suite, not duplicated here
# under new names: none of that data contains a symbol or a diacritic, so
# round 1's `test_pinned_must_refuse`/`test_pinned_must_allow` and every
# Critical-1/2/3 test already re-exercise it against this round's code on
# every run — a second copy would be a no-op tautology of exactly the kind
# flagged and removed last round. See the report for the full-suite output.


# ---------------------------------------------------------------------------
# Task 6c: Romanian and Spanish vocabulary.
#
# Task 6b's diacritic folding (NFD + strip category Mn) made Latin-script
# labels the guard has no vocabulary for *readable* without making them
# *understood*: "Plătește" folds cleanly to "plateste", passes the
# readability check, matches nothing in an English-only _COMMITTING, and
# falls through to ALLOW — a measured fail-open landing on the primary
# user's own language (Romanian) and, second, Spanish. This section adds
# Romanian and Spanish entries to the same four mechanisms the file already
# has (_COMMITTING bare verbs, _COMMITTING_PAIRS co-occurrence, the
# _READ_ONLY_LABELS whole-label whitelist, and — unchanged — benign
# prefixes) rather than inventing a fifth. Every entry is stored
# ASCII-folded, matching what `_words()` actually produces after Task 6b's
# pipeline runs — verified directly against `_words()`, not assumed (see
# the task report for the probe).
# ---------------------------------------------------------------------------

RO_MUST_REFUSE = [
    "Plătește", "Plătiți",
    "Cumpără", "Cumpără acum", "Cumpărați",
    "Șterge contul",
    "Confirmă plata",
    "Trimite banii",
    "Plasează comanda",
    "Semnează contractul",
    "Abonează-te",
    "Dezabonează-te", "Anulează abonamentul",
    "Retrage",
    "Transferă",
    "Donează",
    "Licitează",
    "Publică",
    "Închide contul",
    "Dezactivează contul",
    "Aplică",
]

RO_MUST_ALLOW = [
    "Caută", "Acasă", "Înapoi", "Următorul", "Setări", "Coș",
    "Adaugă în coș",
    "Contul meu",
    "Autentificare", "Conectare", "Deconectare",
    "Vezi mai multe", "Filtrează", "Sortează",
    "Istoricul plăților",
]

ES_MUST_REFUSE = [
    "Pagar", "Pague",
    "Comprar ahora", "Compre",
    "Eliminar cuenta",
    "Realizar pedido", "Tramitar pedido", "Confirmar pedido",
    "Confirmar pago",
    "Finalizar compra",
    "Enviar dinero",
    "Borrar",
    "Cancelar suscripción", "Suscribirse",
    "Darse de baja",
    "Retirar",
    "Transferir",
    "Donar",
    "Pujar",
    "Firmar contrato",
    "Publicar",
    "Solicitar",
    "Cerrar cuenta",
    "Desactivar cuenta",
]

ES_MUST_ALLOW = [
    "Buscar", "Inicio", "Atrás", "Volver", "Siguiente",
    "Ajustes", "Configuración",
    "Añadir al carrito",
    "Historial de pedidos", "Detalles del pedido",
    "Mi cuenta",
    "Iniciar sesión", "Cerrar sesión",
    "Ver más", "Filtrar", "Ordenar por fecha",
]


@pytest.mark.parametrize("name", RO_MUST_REFUSE)
def test_romanian_committing_labels_refuse(name):
    assert irreversible_reason(name, "button") != "", name


@pytest.mark.parametrize("name", RO_MUST_ALLOW)
def test_romanian_benign_labels_allow(name):
    assert irreversible_reason(name, "button") == "", name


@pytest.mark.parametrize("name", ES_MUST_REFUSE)
def test_spanish_committing_labels_refuse(name):
    assert irreversible_reason(name, "button") != "", name


@pytest.mark.parametrize("name", ES_MUST_ALLOW)
def test_spanish_benign_labels_allow(name):
    assert irreversible_reason(name, "button") == "", name


# --- The three named traps -------------------------------------------------

def test_romanian_add_to_cart_allows_while_place_order_refuses():
    # "Adaugă în coș" (add to cart) shares no vocabulary with the
    # committing "comanda" (order); "Finalizează comanda" (place order)
    # does.
    assert irreversible_reason("Adaugă în coș", "button") == ""
    assert irreversible_reason("Finalizează comanda", "button") != ""


def test_romanian_order_history_allows_while_order_now_refuses():
    # "Istoric comenzi" uses the plural "comenzi", a different token from
    # the singular committing "comanda" — it already allows without a
    # whitelist entry. "Comandă acum" uses the singular imperative and
    # refuses via the bare verb.
    assert irreversible_reason("Istoric comenzi", "button") == ""
    assert irreversible_reason("Comandă acum", "button") != ""


def test_spanish_sign_out_allows_while_close_account_refuses():
    # "Cerrar" is only committing when paired with "cuenta"/"cuentas"
    # (_COMMITTING_PAIRS) — never bare — so "Cerrar sesión" (sign out)
    # allows exactly the way English "Close" (dismiss) does, while
    # "Cerrar cuenta" (close account) refuses via the pair.
    assert irreversible_reason("Cerrar sesión", "button") == ""
    assert irreversible_reason("Cerrar cuenta", "button") != ""


def test_spanish_sort_by_date_allows_while_place_order_refuses():
    # "Ordenar" (to sort) is never added to the vocabulary — it must not
    # be conflated with the English "order" or the Spanish noun "pedido".
    # "Realizar pedido" (place order) refuses via "pedido".
    assert irreversible_reason("Ordenar por fecha", "button") == ""
    assert irreversible_reason("Realizar pedido", "button") != ""


# --- Diacritic-form / stripped-form equivalence -----------------------------
# A user's page may render either form (server-rendered accented copy, or a
# JS framework/CMS that strips diacritics for URLs/IDs and sometimes for
# display too). Both must refuse identically.

def test_diacritic_and_stripped_romanian_forms_both_refuse():
    assert irreversible_reason("Plătește", "button") != ""
    assert irreversible_reason("Plateste", "button") != ""
    assert irreversible_reason("Cumpără", "button") != ""
    assert irreversible_reason("Cumpara", "button") != ""


# --- Cross-language and cross-vocabulary collision checks -------------------

def test_new_romanian_and_spanish_words_do_not_collide_with_english_vocabulary():
    # None of the Romanian/Spanish tokens added this task are also English
    # committing words — spelled out explicitly (per the task brief's own
    # named shapes: "Comanda", "Plata"/"Pago", "Firma", "Postează"/"post",
    # "Aplica"/"apply") rather than only inferred from the pass/fail tables
    # above.
    assert irreversible_reason("Comanda", "button") != ""  # RO: refuses (it's the order verb)
    assert irreversible_reason("Firm offer", "button") == ""       # EN "firm" unaffected by ES "firmar"
    assert irreversible_reason("Confirmation email", "button") == ""  # EN unaffected by RO/ES "confirma(r)"
    assert irreversible_reason("Solicitation policy", "button") == ""  # EN "solicit-" unaffected by ES "solicitar"
    assert irreversible_reason("Cont.", "button") == ""            # RO pair-object "cont" alone never refuses


def test_pinned_english_tables_still_hold_after_new_vocabulary():
    # Direct re-assertion that the pinned English tables were not moved by
    # adding Romanian/Spanish vocabulary — belt-and-braces alongside
    # test_pinned_must_refuse/test_pinned_must_allow, which already run
    # this data on every collection of this module.
    for name in MUST_REFUSE:
        assert irreversible_reason(name, "button") != "", name
    for name in MUST_ALLOW:
        assert irreversible_reason(name, "button") == "", name
