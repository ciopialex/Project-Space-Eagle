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


# --- Cookie / tracking consent walls ---------------------------------------
#
# Found live, on the user's own machine: the eagle opened YouTube from Romania,
# was redirected to consent.youtube.com, and stopped dead — then told the user
# it couldn't reach their liked videos "for security reasons", which was
# invented. Nothing was wrong with perception; it read 200 controls fine.
#
# It was deadlocked by its own safety gate. "Accept all" and "I agree" are
# refused by `consent.irreversible_reason` — correctly, they *do* commit
# something on the user's behalf. But those were the only buttons it
# considered, so every EU site became a dead end, and these walls sit in front
# of essentially the whole web for a European user.
#
# The way out was already there: "Reject all" and "Only necessary" are not
# committing, so the gate permits them. Rejecting is also the privacy-
# preserving answer and the one a careful person picks anyway — the safe move
# and the unblocking move are the same move, which is why this needs no
# exception to the gate and no new actuation path.

_COOKIE_MARKERS = {
    "cookie", "cookies", "consent", "consimtamant", "consimtamintul",
    "gdpr", "tracking", "personalizate", "personalised", "personalized",
}

#: Google's wall never says "cookie" in the part you can read — it says
#: "Before you continue to YouTube". Measured on the real page, which is how
#: this gap was found.
_COOKIE_PHRASES = (
    "before you continue", "inainte de a continua", "antes de continuar",
    "we use cookies", "folosim cookie", "usamos cookies",
    "your privacy choices", "manage your privacy",
)

#: The most reliable signal of all: Google, YouTube and many others move the
#: wall to a dedicated consent host, so the URL says it outright.
_CONSENT_HOSTS = ("consent.", "consent-", "/consent", "cookiewall", "gdpr")

#: Rejecting/minimising, in preference order. All are permitted by the gate.
_COOKIE_DECLINE = (
    "reject all", "reject", "refuse all", "decline", "decline all",
    "only necessary", "necessary only", "necessary cookies only",
    "essential only", "only essential", "strictly necessary",
    "manage options", "manage preferences", "more options",
    # Romanian
    "refuz tot", "refuza tot", "respinge tot", "doar necesare",
    "doar cele necesare", "gestioneaza optiunile", "mai multe optiuni",
    # Spanish
    "rechazar todo", "rechazar", "solo necesarias", "solo las necesarias",
    "gestionar opciones", "mas opciones",
)


def cookie_wall_choice(nodes: Iterable[object], url: str = "") -> str:
    """The name of the control that clears a cookie wall *without consenting*.

    Returns "" when this is not a cookie wall, or when it is one and nothing
    on it can be clicked without agreeing to tracking — in which case the
    honest move is to tell the user rather than accept on their behalf.
    """
    names = []
    for node in nodes or ():
        raw = str(getattr(node, "name", "") or "")
        if raw:
            names.append((raw, set(_label_words(raw))))

    lowered = (url or "").lower()
    looks_like_consent = (
        any(host in lowered for host in _CONSENT_HOSTS)
        or any(w & _COOKIE_MARKERS for _raw, w in names)
        or any(phrase in raw.lower()
               for raw, _w in names for phrase in _COOKIE_PHRASES)
    )
    if not looks_like_consent:
        return ""

    for wanted in _COOKIE_DECLINE:
        target = set(_label_words(wanted))
        for raw, words in names:
            # Whole-label match on the folded words, so "Reject all" matches
            # "Reject all" and "Reject All Cookies" but never "Reject" inside
            # some unrelated sentence.
            if target and target <= words and len(words) <= len(target) + 2:
                return raw
    return ""


#: Phrases where a site is telling the user that the CONTENT is gated, as
#: opposed to merely offering a sign-in link the way nearly every homepage
#: does. The distinction matters: treating any "Sign in" control as a wall
#: would fire on most of the web and train the user to ignore it.
#:
#: Found live: signed out, youtube.com/feed/liked renders 91 controls and
#: reads perfectly — it just says "Conectează-te pentru a aprecia
#: videoclipuri" (sign in to like videos) instead of showing them. Nothing
#: detected that, so the eagle had no honest way to say what was wrong.
_SIGNED_OUT_PHRASES = (
    "sign in to", "log in to", "sign in for", "please sign in", "please log in",
    "conecteaza-te pentru", "conectati-va pentru", "autentifica-te pentru",
    "inicia sesion para", "iniciar sesion para", "accede para",
)

_SIGNED_OUT_REASON = (
    "this page is only showing signed-out content — the site wants the user "
    "signed in before it will show what they asked for")


def signed_out_reason(nodes: Iterable[object]) -> str:
    """Why this page is withholding content until someone signs in, or "".

    Deliberately phrase-based, never role- or name-based: a "Sign in" button
    is not evidence of a wall, it is evidence of a website.
    """
    for node in nodes or ():
        folded = " ".join(_label_words(str(getattr(node, "name", "") or "")))
        if any(phrase.replace("-", " ") in folded
               for phrase in _SIGNED_OUT_PHRASES):
            return _SIGNED_OUT_REASON
    return ""


# --- Turning a detected wall into an actionable remedy --------------------
#
# Detecting that a page wants a sign-in is only half the job. Live testing
# showed the other half is what actually costs the user: the eagle correctly
# reported "this site wants you signed in" and then stopped, leaving them to
# work out which command to run and which domains to name. A wall the eagle
# can describe but not resolve is barely better than one it invents.
#
# So the remedy is derived here — including the part a user would not guess.

#: Sites whose sign-in state lives on a *different* domain than the one being
#: browsed. Importing youtube.com alone brings across nothing useful, because
#: the session cookies that make YouTube signed-in belong to google.com. This
#: is the single most likely reason an import "works" and the page is still
#: signed out, so it is encoded rather than left as a docstring note.
_AUTH_COMPANIONS = {
    "youtube.com": ("google.com", "accounts.google.com"),
    "gmail.com": ("google.com", "accounts.google.com"),
    "google.com": ("accounts.google.com",),
    "drive.google.com": ("google.com", "accounts.google.com"),
    "docs.google.com": ("google.com", "accounts.google.com"),
    "outlook.com": ("login.microsoftonline.com", "live.com"),
    "office.com": ("login.microsoftonline.com", "live.com"),
    "instagram.com": ("facebook.com",),
    "linkedin.com": ("www.linkedin.com",),
}


def _registrable(host: str) -> str:
    """`www.youtube.com` -> `youtube.com`. Good enough for common TLDs."""
    host = (host or "").lower().strip().strip(".")
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    # Two-part public suffixes we actually meet: co.uk, com.br, com.au, ...
    if len(parts) > 2 and parts[-2] in {"co", "com", "org", "net", "gov", "ac"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) > 2 else host


def auth_domains_for(url: str) -> list[str]:
    """Every domain whose cookies are needed to be signed in at `url`.

    The site itself, plus wherever its sign-in actually lives.
    """
    host = (url or "").split("://")[-1].split("/")[0].split("?")[0]
    site = _registrable(host)
    if not site or "." not in site:
        return []
    domains = [site]
    for companion in _AUTH_COMPANIONS.get(site, ()):  # keyed on registrable
        if companion not in domains:
            domains.append(companion)
    return domains


def login_remedy(url: str) -> str:
    """The exact next step for a page that wants the user signed in.

    Written as an instruction to the model rather than prose for the user,
    because the failure this replaces was the eagle describing the problem and
    handing the task back. It should be able to act on this without the user
    having to know a command exists.
    """
    domains = auth_domains_for(url)
    if not domains:
        return ("Call web_agency action='sign_in' with this page's url so the "
                "user can log in once.")
    named = " ".join(domains)
    extra = ""
    if len(domains) > 1:
        extra = (f" ({domains[1]} is included because that is where "
                 f"{domains[0]}'s sign-in actually lives.)")
    return (
        f"Offer to fix this: call web_agency action='import_login' with "
        f"domains='{named}' to copy the user's existing login across from "
        f"their Chrome — tell them Chrome has to be closed for a moment "
        f"first.{extra} If they would rather not, call action='sign_in' with "
        f"this page's url instead and they can log in directly.")
