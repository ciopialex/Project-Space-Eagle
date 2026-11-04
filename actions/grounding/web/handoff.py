"""Hand the keyboard back when the site is asking for a person: stop, say what is
being asked for, and wait.

A bare "code" is not a 2FA prompt ("Promo code"): it needs a positive qualifier
and no negative one (_is_code_wall), the same shape as consent.py's vocabulary.

Labels go through consent.py's own normaliser (_words), so the English, Romanian
and Spanish vocabulary is stored pre-folded.
"""
from __future__ import annotations

import time
from typing import Callable, Iterable

from actions.grounding.web.consent import _words as _label_words

# Human-verification challenge: words unambiguous on their own in all three languages.
_HUMAN_WORDS = {"robot", "captcha", "human", "recaptcha", "hcaptcha"}

# Exact whole labels that name a bot check without "robot" or "captcha"; their words
# alone are too general.
_HUMAN_CHECK_PHRASES = {
    "verificare de securitate",
    "verificacion de seguridad",
}

_HUMAN_REASON = ("this page is asking for a human check, which the eagle "
                  "will not answer on the user's behalf")

# Verification code vs promo code. These alone are unambiguous.
_CODE_SELF_SUFFICIENT = {"otp", "authenticator", "passcode", "2fa"}

# The bare word for "code": needs a qualifier (_is_code_wall).
_CODE_WORDS = {"code", "cod", "codul", "codigo"}

# Qualifiers that make a code-word a verification prompt. "primit" ("Codul primit") is
# the code you were sent.
_CODE_QUALIFIERS = {
    "verification", "security", "authentication", "sms", "onetime",
    "verificare", "securitate", "autentificare", "primit",
    "verificacion", "seguridad", "autenticacion",
}

# Qualifiers that rule a code-word out.
_CODE_NEGATIVE = {
    "promo", "promotional", "discount", "coupon", "gift", "referral",
    "voucher", "invite", "invitation", "postal", "zip", "country", "area",
    "reducere", "cupon", "invitatie",
    "promocional", "descuento", "invitacion",
}

# Two-step markers. "Autentificare" is also the Romanian "Sign in" button, so it counts
# only alongside one of these.
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
    # No code-word: a qualifier counts only alongside a two-step marker.
    return bool(words & _CODE_QUALIFIERS) and bool(words & _TWO_FACTOR_MARKERS)


# Password and login wall. role == "password" (from <input type=password>) is the
# primary, language-independent detector; these names are the backup.
_PASSWORD_WORDS = {"parola", "contrasena", "clave"}

_LOGIN_REASON = ("this site needs the user to sign in once; after that the "
                  "eagle stays signed in")


def wall_reason(nodes: Iterable[object], url: str = "") -> str:
    """Why this page needs the user, or "". Most specific first: a human check is
    reported even on a login page. Never raises.
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
    """Wait for check() to stop reporting a reason. True if it cleared."""
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


# Cookie and tracking consent walls. "Accept all" is refused by the consent gate, so the
# way through is the decline control ("Reject all", "Only necessary"), which the gate
# permits and which is also the private choice.

_COOKIE_MARKERS = {
    "cookie", "cookies", "consent", "consimtamant", "consimtamintul",
    "gdpr", "tracking", "personalizate", "personalised", "personalized",
}

# Google's wall says "Before you continue to YouTube", not "cookie".
_COOKIE_PHRASES = (
    "before you continue", "inainte de a continua", "antes de continuar",
    "we use cookies", "folosim cookie", "usamos cookies",
    "your privacy choices", "manage your privacy",
)

# A dedicated consent host says it outright.
_CONSENT_HOSTS = ("consent.", "consent-", "/consent", "cookiewall", "gdpr")

# Rejecting or minimising, in preference order. All permitted by the gate.
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
    """The control that clears a cookie wall without consenting, or "" when this is
    not one or nothing on it declines (then tell the user).
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
            # Whole-label match on the folded words.
            if target and target <= words and len(words) <= len(target) + 2:
                return raw
    return ""


# Phrases where a site says the content is gated, not merely offering a sign-in link.
_SIGNED_OUT_PHRASES = (
    "sign in to", "log in to", "sign in for", "please sign in", "please log in",
    "conecteaza-te pentru", "conectati-va pentru", "autentifica-te pentru",
    "inicia sesion para", "iniciar sesion para", "accede para",
    "scan this qr code", "link with phone number", "log in with phone number",
    "log in by phone number", "scan to log in",
)

_SIGNED_OUT_REASON = (
    "this page is only showing signed-out content — the site wants the user "
    "signed in before it will show what they asked for")


def signed_out_reason(nodes: Iterable[object]) -> str:
    """Why this page withholds content until someone signs in, or "". Phrase-based: a
    "Sign in" button is evidence of a website, not a wall.
    """
    for node in nodes or ():
        raw_name = str(getattr(node, "name", "") or "").lower()
        folded = " ".join(_label_words(raw_name))
        if any(phrase.replace("-", " ") in folded or phrase in raw_name
               for phrase in _SIGNED_OUT_PHRASES):
            return _SIGNED_OUT_REASON
    return ""


# Turning a detected wall into a remedy.

# Sites whose sign-in lives on another domain: YouTube's session belongs to google.com.
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
    # Two-part public suffixes: co.uk, com.br, com.au.
    if len(parts) > 2 and parts[-2] in {"co", "com", "org", "net", "gov", "ac"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) > 2 else host


def auth_domains_for(url: str) -> list[str]:
    """Every domain whose cookies are needed to be signed in at `url`: the site plus
    wherever its sign-in lives.
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

# Bot challenges: a site refusing automated browsers is a wall, not a page with one
# control, and waiting does not clear it.

# Phrases challenge pages use, matched against control names.
_BOT_WALL_PHRASES = (
    "enable javascript and cookies",
    "performing security verification",
    "checking your browser",
    "just a moment",
    "verify you are human",
    "verifying you are human",
    "ddos protection by",
    "attention required",
    "unusual traffic from your computer",
)

# Query parameters and paths only a challenge issues. Conclusive alone.
_BOT_WALL_URL_MARKS = ("__cf_chl", "cdn-cgi/challenge", "/challenge-platform")

# Above this many controls the page is working, whatever words are on it. A Cloudflare
# interstitial renders eight nodes.
_BOT_WALL_MAX_CONTROLS = 12

# Signals a challenge page carries in every language: Cloudflare's "ray id" footer and
# cf- widgets. Paired with the small-page ceiling so Cloudflare's own docs do not trip
# it.
_BOT_WALL_MARKERS = ("ray id", "cf-chl", "cf_chl", "turnstile",
                     "challenge-platform", "cf-browser-verification",
                     "__cf_bm", "cloudflare ray")


def bot_wall_reason(nodes, url: str = "") -> str:
    """Why this page cannot be read at all, or "". Keyed on the challenge's shape (a
    near-empty page with challenge wording, or a challenge URL), not on a word
    appearing.
    """
    lowered = (url or "").lower()
    if any(mark in lowered for mark in _BOT_WALL_URL_MARKS):
        return _BOT_WALL_REASON

    items = list(nodes or ())
    if not items or len(items) > _BOT_WALL_MAX_CONTROLS:
        # An empty read is "could not see", not "was blocked".
        return ""
    for node in items:
        name = str(getattr(node, "name", "") or "").lower()
        # Language-independent markers first.
        if any(mark in name for mark in _BOT_WALL_MARKERS):
            return _BOT_WALL_REASON
        if any(phrase in name for phrase in _BOT_WALL_PHRASES):
            return _BOT_WALL_REASON
    return ""


_BOT_WALL_REASON = (
    "this site is blocking automated browsers - it served a security check "
    "instead of the page, and it does not clear on its own"
)
