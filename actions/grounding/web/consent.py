"""The line the eagle does not cross by itself: controls that commit something are
refused rather than clicked.

A heuristic, wrong in both directions, erring toward refusing: an unnecessary
question costs a sentence, an unnecessary payment costs a payment.

Vocabulary covers English, Romanian and Spanish, ASCII-folded. Any other Latin-
script language with no entry folds to readable words, matches nothing and is
allowed: a known gap (Italian "Paga" is not refused). Non-Latin labels are refused
unless every character folds through _CONFUSABLES.

No exact-phrase blacklist (one inserted word defeats it) and no "reading word
anywhere exempts it" rule (it exempts "Confirm payments"). Read-only labels are
matched whole in _READ_ONLY_LABELS.

irreversible_reason() tokenises twice, with noise characters removed and with them
as separators, and refuses if either reading matches.
"""
from __future__ import annotations

import re
import string
import unicodedata

# Homoglyph -> Latin, folded after .lower(). Reduces false refusals; the security
# boundary is _ALLOWED_CHARS. Hand-rolled and partial: Cyrillic а е о р с у х і ѕ ј т,
# Greek α ο ρ ν, and digit look-alikes 0 1 3 4 5 7 8. The digit fold makes "P05T" and
# "5UBMIT" refuse; it cannot break an existing match, and no all-digit token spells a
# committing word. A whole non-Latin word made only of mapped letters folds to readable
# ASCII and is read like any label.
_CONFUSABLES = {
    # Cyrillic
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "і": "i", "ѕ": "s", "ј": "j", "т": "t",
    # Greek
    "α": "a", "ο": "o", "ρ": "p", "ν": "v",
    # Digit look-alikes
    "0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b",
}

# Category Cf: zero-width and bidi control characters, invisible by design.
_FORMAT_CATEGORY = "Cf"

# Symbol categories So, Sk, Sm, Sc: emoji, dingbats, arrows. Treated as noise.
_SYMBOL_CATEGORIES = frozenset({"So", "Sk", "Sm", "Sc"})

# ASCII $ + < = > ^ ` | ~ are Unicode symbols but stay ordinary separators: removing
# them fuses "Delete=all" into one non-word.
_SYMBOL_EXCEPTIONS = frozenset("$+<=>^`|~")

# Category Zs except U+0020 (non-breaking, hair space...): noise, so "P<hair space>ay
# now" reads as "Pay now" in at least one of the two readings.
_SPACE_CATEGORY = "Zs"

# The security boundary: after noise handling, a label with anything outside ASCII
# letters, digits, punctuation and whitespace tokenises to no words and is refused.
# Invisible characters are stripped before this check; _mixed_readings() covers them.
_ALLOWED_CHARS = frozenset(
    string.ascii_lowercase + string.digits + string.punctuation + " \t\n\r\v\f"
)

# Bare verbs whose presence anywhere in the label commits something. Whole words, never
# substrings.
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
    # `reserve` is `book`'s synonym.
    "reserve": "it reserves something",
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

    # Romanian, stored ASCII-folded: _words() strips diacritics before matching.
    "plateste": "it pays something",
    "platiti": "it pays something",
    # Payment noun and verb: "Realizează plata", "Achită acum".
    "plata": "it pays something",
    "achita": "it pays something",
    "cumpara": "it buys something",
    "cumparati": "it buys something",
    # "comanda" is both "order now" and "order details": committing, with the noun uses
    # in _READ_ONLY_LABELS.
    "comanda": "it places an order",
    "trimite": "it sends something",
    "sterge": "it deletes something",
    # Nominalised form ("Ștergere cont").
    "stergere": "it deletes something",
    "elimina": "it removes something",
    "confirma": "it confirms something that may not be undoable",
    "semneaza": "it signs something",
    "aboneaza": "it starts a subscription",
    # Nominalised form.
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
    # dezactiveaza / dezactivare are in _COMMITTING_PAIRS, paired with cont.

    # Spanish
    "pagar": "it pays something",
    "pague": "it pays something",
    # Imperative tú and the payment noun.
    "paga": "it pays something",
    "pago": "it pays something",
    "comprar": "it buys something",
    "compre": "it buys something",
    # "pedido": committing, with "Detalles del pedido" in _READ_ONLY_LABELS.
    "pedido": "it places an order",
    "confirmar": "it confirms something that may not be undoable",
    # "Finalizar compra".
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
    # "Aceptar", "Acepto los términos".
    "aceptar": "it accepts something on the user's behalf",
    "acepto": "it accepts something on the user's behalf",
    # desactivar is in _COMMITTING_PAIRS, paired with cuenta.
}

# Verbs that only commit when their object appears anywhere in the label: "Close your
# account" refuses, "Close menu" does not.
# Objects shared by every dismiss-or-end verb, so the senses cannot drift apart per
# verb.
_ACCOUNT_OBJECTS = {"account", "accounts", "membership", "profile"}
_SUBSCRIPTION_OBJECTS = {"subscription", "subscriptions", "plan", "membership"}

_COMMITTING_PAIRS = {
    # Every verb takes both object sets (test_the_pairs_table_shares_its_object_sets).
    "close": (_ACCOUNT_OBJECTS | _SUBSCRIPTION_OBJECTS,
              "it closes an account or subscription"),
    "cancel": (_ACCOUNT_OBJECTS | _SUBSCRIPTION_OBJECTS,
               "it cancels an account or subscription"),
    "disable": (_ACCOUNT_OBJECTS | _SUBSCRIPTION_OBJECTS,
                "it disables an account or subscription"),
    "end": (_ACCOUNT_OBJECTS | _SUBSCRIPTION_OBJECTS,
            "it ends an account or subscription"),

    # "Empty cart" is reversible; emptying the trash is not.
    "empty": ({"trash", "bin", "wastebasket", "recycle"},
              "it permanently deletes everything in the trash"),

    # A free trial is a delayed billing obligation.
    "start": ({"trial"}, "it starts a trial that becomes a paid subscription"),
    "begin": ({"trial"}, "it starts a trial that becomes a paid subscription"),
    "activate": ({"trial", "subscription", "plan"},
                 "it activates a paid subscription"),

    # Both directions change what the user is charged; bare "Upgrade" is usually a
    # software update.
    "upgrade": (_SUBSCRIPTION_OBJECTS | {"premium", "pro", "plus"},
                "it changes a paid plan"),
    "downgrade": (_SUBSCRIPTION_OBJECTS | {"premium", "pro", "plus"},
                  "it changes a paid plan"),
    # `terminate` stays bare-committing.

    # Romanian: "Închide" alone is a dismiss word, so it is paired.
    "inchide": ({"cont", "contul"}, "it closes an account"),
    # Nominalised form ("Închidere cont").
    "inchidere": ({"cont", "contul"}, "it closes an account"),
    # "Anulează" alone is a Cancel button.
    "anuleaza": ({"abonament", "abonamentul"}, "it cancels a subscription"),
    # "Renunță la abonament" cancels; "Renunță" alone is a dismiss word.
    "renunta": ({"abonament", "abonamentul"}, "it cancels a subscription"),
    # Romanian uses one verb for deactivating an account and switching off a toggle, so
    # it is paired with the account.
    "dezactiveaza": ({"cont", "contul"}, "it deactivates an account"),
    # Nominalised form ("Dezactivare cont").
    "dezactivare": ({"cont", "contul"}, "it deactivates an account"),

    # Spanish: "Cerrar sesión" signs out, so "cerrar" is paired with "cuenta".
    "cerrar": ({"cuenta", "cuentas"}, "it closes an account"),
    "cancelar": ({"suscripcion", "suscripciones"}, "it cancels a subscription"),
    # "Darse de baja" (unsubscribe), paired: "darse" alone is too broad.
    "darse": ({"baja"}, "it cancels a subscription"),
    # "Dar de baja", the more common form.
    "dar": ({"baja"}, "it cancels a subscription"),
    # Spanish uses one verb for disabling a toggle and deactivating an account.
    "desactivar": ({"cuenta", "cuentas"}, "it deactivates an account"),
}

# Whole labels that read a record rather than commit an act, matched against the entire
# label: "Order history" is a page, "Order now" is not.
_READ_ONLY_LABELS = {
    "order history", "order details", "order summary", "order status",
    "purchase history", "purchase details", "purchase summary",
    "purchase receipt", "purchase receipts",
    "payment history", "payment details", "payment method",
    "payment methods", "saved payment methods",
    "transfer history", "transfer details", "transfer status",
    "billing history", "post history", "your orders", "your purchases",

    # Romanian
    "detalii comanda",       # Detalii comandă (order details)
    "sumar comanda",          # Sumar comandă (order summary)
    "comanda mea",            # Comanda mea (my order)
    # Homographs that are not the committing sense in these exact phrases.
    "informatie publica",     # Informație publică (public information)
    "oferta publica",         # Ofertă publică (public offer)
    "aplica filtrul",         # Aplică filtrul (apply the filter)
    "aplica filtre",          # Aplică filtre (apply filters)
    "piese de rezerva",       # Piese de rezervă (spare parts)
    "rezerva de energie",     # Rezervă de energie (energy reserve)

    # Spanish
    "detalles del pedido",   # Detalles del pedido (order details)
    "estado del pedido",      # Estado del pedido (order status)
    "resumen del pedido",     # Resumen del pedido (order summary)
    "seguimiento del pedido",  # Seguimiento del pedido (order tracking)
    "confirmacion de pedido",  # Confirmación de pedido (order confirmation)
    "mi pedido",               # Mi pedido (my order)
    "detalles de la compra",   # Detalles de la compra (purchase details)
    "resumen de compra",       # Resumen de compra (purchase summary)
    "recibo de compra",        # Recibo de compra (purchase receipt)
    "mi compra",                # Mi compra (my purchase)
    # Homographs that are not the committing sense here.
    "informacion publica",     # Información pública (public information)
    "retirar en tienda",       # Retirar en tienda (pick up in store)
    "solicitar informacion",   # Solicitar información (request information)
    "solicitar presupuesto",   # Solicitar presupuesto (request a quote)
}

# Benign login prefixes, stripped so "Sign in with Google" is allowed. The rest of the
# label goes back through the full check.
_BENIGN_PREFIXES = ["sign in", "log in", "sign out", "log out", "sign up"]


def _is_standalone_noise(ch: str) -> bool:
    """A noise character that could sit between two letters like a separator: Cf,
    symbols (minus the ASCII exceptions) and non-ASCII spaces. Combining marks are
    handled separately.
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
    """A combining mark (Mn) exposed by NFD. Always removed, never a separator: a
    human never sees it split a word.
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
    """Normalise `text` and neutralise each noise character: removed ("join") or
    turned into a space ("split").

    Noise is stripped on the raw text before NFKC, because NFKC turns a spacing
    diacritic (Sk) into space + combining mark and so manufactures a word boundary
    ("P´ay now"). The pass runs again after NFKC and NFD. Checking both readings
    means the split reading can only add refusals.
    """
    text = text or ""
    text = _replace_noise(text, replacement)
    text = unicodedata.normalize("NFKC", text)
    text = _replace_noise(text, replacement)
    text = unicodedata.normalize("NFD", text)
    text = _replace_noise(text, replacement)
    return text


# Punctuation that can hide inside a word: "De.lete", "B-u-y", "Pur_chase".
_INWORD_PUNCT = r".\-_'’·*~^|/\\,;:+="

# Only between two word characters.
_DEPUNCT_RE = re.compile(rf"(?<=\w)[{_INWORD_PUNCT}]+(?=\w)")


def _tokenise(text: str, *, replacement: str, depunct: bool = False) -> list[str]:
    text = _strip_noise(text, replacement=replacement).lower()
    # Fold homoglyphs onto Latin letters (a false-refusal reducer, not the boundary).
    text = "".join(_CONFUSABLES.get(ch, ch) for ch in text)
    if any(ch not in _ALLOWED_CHARS for ch in text):
        # Something unreadable survived: the whole label is unreadable (_ALLOWED_CHARS).
        return []
    if depunct:
        # The third reading: in-word punctuation deleted, so "De.lete" reads as
        # "delete". Separate from the noise set, which would double the readings per
        # mark.
        text = _DEPUNCT_RE.sub("", text)
    return [w for w in re.split(r"[^a-z0-9]+", text) if w]


def _words(text: str) -> list[str]:
    """Tokenise with noise removed (the "join" reading)."""
    return _tokenise(text, replacement="")


def _words_depunct(text: str) -> list[str]:
    """Tokenise with in-word punctuation removed."""
    return _tokenise(text, replacement="", depunct=True)


def _words_split(text: str) -> list[str]:
    """Tokenise with noise as separators (the "split" reading)."""
    return _tokenise(text, replacement=" ")


# Noise characters allowed before the label is refused as unreadable; bounds the
# readings at 2**8.
_MAX_NOISE_CHARS = 8


def _mixed_readings(text: str):
    """Every mixed join/split reading of `text`, or None if it is too noisy. Needed
    because the extremes can both miss: "P<hair>ay<hair>now" joins to "paynow" and
    splits to "p ay now", while a human reads "pay now". Only raw noise is
    enumerated.
    """
    raw = text or ""
    # Only noise between two alphanumerics can change the reading, so decorated copy
    # ("⭐⭐⭐ Reviews") is not enumerated.
    positions = [i for i, ch in enumerate(raw)
                 if _is_standalone_noise(ch)
                 and 0 < i < len(raw) - 1
                 and raw[i - 1].isalnum() and raw[i + 1].isalnum()]
    if len(positions) > _MAX_NOISE_CHARS:
        return None
    if len(positions) < 2:
        # Zero or one interior noise character is covered by the two extreme readings.
        return iter(())

    def _readings():
        # Masks strictly between all-join and all-split.
        for mask in range(1, 2 ** len(positions) - 1):
            chars = list(raw)
            for bit, pos in enumerate(positions):
                chars[pos] = " " if (mask >> bit) & 1 else ""
            yield _tokenise("".join(chars), replacement="")

    return _readings()


def _reason_for_words(words: list[str]) -> str:
    """The committing-or-not decision for an already-tokenised label."""
    if not words:
        return ""

    for prefix in _BENIGN_PREFIXES:
        prefix_words = prefix.split()
        n = len(prefix_words)
        if words[:n] == prefix_words:
            # Run the full check on what is left after a benign prefix.
            return _reason_for_words(words[n:])

    label = " ".join(words)
    if label in _READ_ONLY_LABELS:
        return ""

    word_set = set(words)
    for verb, (objects, reason) in _COMMITTING_PAIRS.items():
        if verb in word_set and word_set & objects:
            return reason

    # Reverse iteration prefers the final verb: "Confirm and pay" gives "pay".
    for word in reversed(words):
        reason = _COMMITTING.get(word)
        if reason:
            return reason
    return ""


def irreversible_reason(name: str, role: str = "") -> str:
    """Why the eagle must not click this on its own, or "" if it may. A phrase for "I
    stopped because <reason>". Refuses if any reading matches; the join reading's
    reason is preferred.
    """
    words_join = _words(name)
    words_split = _words_split(name)

    if not words_join and not words_split:
        if _strip_noise(name).strip():
            # Visible text that folds to no Latin word: most likely a non-Latin label.
            return ("it is not in a script this guard can read, so there is "
                    "no way to tell what it does")
        return ("it has no readable label, so there is no way to tell what it "
                "does")

    reason = (_reason_for_words(words_join)
              or _reason_for_words(words_split)
              or _reason_for_words(_words_depunct(name)))
    if reason:
        return reason

    # Neither extreme matched; try the mixed readings.
    mixed = _mixed_readings(name)
    if mixed is None:
        return ("it carries too many invisible or decorative characters to "
                "read reliably")
    for words in mixed:
        reason = _reason_for_words(words)
        if reason:
            return reason
    return ""
