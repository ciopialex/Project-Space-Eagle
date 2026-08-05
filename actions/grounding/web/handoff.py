"""Hand the keyboard back when the site is asking for a person.

An auth wall is not a perception problem, and no amount of better sensing
solves it. The honest move is the one a human assistant makes: stop, say what
is being asked for, and wait.

Two things the plan's original brief got wrong, fixed here:

1. Matching the bare token "code" against every control name is a
   false-positive storm. "Promo code", "Discount code", "Coupon code",
   "Referral code", "Gift code", "Voucher code" are ordinary commerce-page
   fields, not two-factor prompts. `code` alone means nothing; it needs
   either a positive qualifier ("verification", "security", "sms", ...) or
   is ruled out by a negative one ("promo", "discount", "coupon", ...). See
   `_is_code_wall` below. This is the same defect class `consent.py` solved
   for a different guard — bare token matching with no phrase context — so
   this file follows its shape (bare-word sets, co-occurrence pairs, an
   exact-phrase whitelist for the ambiguous cases) rather than inventing a
   different answer to the same question.

2. English-only is a correctness bug, not a gap, for a user whose primary
   languages are Romanian and English, with Spanish close behind. The label
   normaliser is *reused* from `consent.py` (`_words`) rather than
   duplicated: NFKC, invisible/symbol stripping, NFD diacritic folding,
   lower-case, confusables folding, then an ASCII allowlist. That pipeline
   is what turns `Parolă` into `parola` and `Código` into `codigo`, so the
   vocabulary below is stored pre-folded (no diacritics) and matched against
   its output. Two normalisers that drift apart is a bug waiting to happen;
   there is exactly one here.
"""
from __future__ import annotations

import time
from typing import Callable, Iterable

from actions.grounding.web.consent import _words as _label_words

# --- Human-verification challenge -------------------------------------------
#
# Unambiguous on their own in any of the three target languages — none of
# these words show up in ordinary commerce or navigation copy, so a bare
# co-occurrence-free match is safe.
_HUMAN_WORDS = {"robot", "captcha", "human", "recaptcha", "hcaptcha"}

#: "Security verification" / "Verificare de securitate" / "Verificación de
#: seguridad" name a human/bot check without the word "robot" or "captcha"
#: anywhere in them, and their component words ("verificare", "securitate",
#: "seguridad", ...) are too general to add to `_HUMAN_WORDS` bare — a
#: "Security settings" page would falsely trip. Matched as an exact,
#: normalised whole label instead, the same way `consent.py` whitelists
#: exact read-only labels rather than exempting a bare reading word.
_HUMAN_CHECK_PHRASES = {
    "verificare de securitate",
    "verificacion de seguridad",
}

_HUMAN_REASON = ("this page is asking for a human check, which the eagle "
                  "will not answer on the user's behalf")

# --- Verification code vs. promo/discount code ------------------------------
#
# Words that alone are already unambiguous: nobody calls a promo code an
# "OTP" or a "passcode" or "2FA".
_CODE_SELF_SUFFICIENT = {"otp", "authenticator", "passcode", "2fa"}

#: The bare word for "code" in each language. Needs a qualifier (positive or
#: negative) to mean anything on its own — see `_is_code_wall`.
_CODE_WORDS = {"code", "cod", "codul", "codigo"}

#: Qualifiers that turn a bare code-word into a real verification prompt.
#: "primit" (Romanian: "received") covers "Codul primit" (the code you were
#: sent) — it has no other qualifier word but is unambiguous in the same way
#: "SMS code" is: nobody calls a promo code "the code you received".
_CODE_QUALIFIERS = {
    "verification", "security", "authentication", "sms", "onetime",
    "verificare", "securitate", "autentificare", "primit",
    "verificacion", "seguridad", "autenticacion",
}

#: Negative qualifiers that rule a code-word OUT even without checking for a
#: positive one — a discount/coupon/gift/etc. code is never a 2FA prompt.
_CODE_NEGATIVE = {
    "promo", "promotional", "discount", "coupon", "gift", "referral",
    "voucher", "invite", "invitation", "postal", "zip", "country", "area",
    "reducere", "cupon", "invitatie",
    "promocional", "descuento", "invitacion",
}

#: "Two-step" / "two-factor" markers. A bare qualifier word like
#: "autentificare" is also just the Romanian button label "Sign in" (see
#: `_words` fold of "Autentificare"), so it only counts as a verification
#: signal when it co-occurs with one of these — mirroring `consent.py`'s
#: `_COMMITTING_PAIRS` (verb + object co-occurrence) rather than a bare-word
#: match.
_TWO_FACTOR_MARKERS = {
    "two", "step", "steps", "factor", "factors",
    "doi", "pasi", "factori",
    "dos", "pasos", "factores",
}

_CODE_REASON = ("this page is asking for a verification code, which only "
                 "the user has")


def _is_code_wall(words: set[str]) -> bool:
    if words & _CODE_SELF_SUFFICIENT:
        return True
    has_code_word = bool(words & _CODE_WORDS)
    if has_code_word:
        if words & _CODE_NEGATIVE:
            return False               # ruled out: promo/discount/etc.
        return bool(words & _CODE_QUALIFIERS)  # bare "code" alone is not enough
    # No code-word at all: a qualifier word only counts alongside a
    # two-step/two-factor marker (e.g. "Autentificare în doi pași"),
    # otherwise it is indistinguishable from an ordinary sign-in button.
    return bool(words & _CODE_QUALIFIERS) and bool(words & _TWO_FACTOR_MARKERS)


# --- Password / login wall ---------------------------------------------------
#
# `role == "password"` is the primary detector and is language-independent —
# it comes from `<input type=password>`, not from reading the label. These
# names are the backup, for pages where a password field is rendered without
# that role (or as a defence-in-depth second signal).
_PASSWORD_WORDS = {"parola", "contrasena", "clave"}

_LOGIN_REASON = ("this site needs the user to sign in once; after that the "
                  "eagle stays signed in")


def wall_reason(nodes: Iterable[object], url: str = "") -> str:
    """Why this page needs the user, or "" if it does not.

    Ordered most specific first: a human-verification challenge is reported
    as such even when it sits on a login page, because it is the part the
    eagle genuinely cannot do. Never raises — the page is hostile input.
    """
    try:
        labels: list[tuple[list[str], str]] = []
        for node in nodes or ():
            name = getattr(node, "name", "") or ""
            role = str(getattr(node, "role", "") or "").lower()
            labels.append((_label_words(name), role))
    except Exception:
        return ""

    try:
        for words, _role in labels:
            wset = set(words)
            if wset & _HUMAN_WORDS:
                return _HUMAN_REASON
            if " ".join(words) in _HUMAN_CHECK_PHRASES:
                return _HUMAN_REASON

        for words, _role in labels:
            if _is_code_wall(set(words)):
                return _CODE_REASON

        for words, role in labels:
            if role == "password" or (set(words) & _PASSWORD_WORDS):
                return _LOGIN_REASON
    except Exception:
        return ""

    return ""


def await_human(check: Callable[[], str], *,
                timeout: float = 300.0,
                poll: float = 2.0,
                clock: Callable[[], float] = time.monotonic,
                sleep: Callable[[float], None] = time.sleep) -> bool:
    """Wait for `check()` to stop reporting a reason. True if it cleared.

    Five minutes by default — long enough to find a phone, short enough that
    a forgotten handoff does not pin a browser thread forever.
    """
    start = clock()
    while True:
        try:
            if not check():
                return True
        except Exception:
            pass                      # mid-navigation; look again shortly
        if clock() - start >= timeout:
            return False
        sleep(poll)
