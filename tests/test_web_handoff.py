"""When a site asks for a human, ask the human.

Not because solving the challenge is impossible, but because a site asking "are
you a person" and getting a machine answer is the eagle lying on the user's
behalf. A human assistant says "I need you for this bit". That degrades
gracefully as these systems change, and nothing else does.

Target languages: Romanian and English first, then Spanish. A guard that only
understands English is a correctness bug for the primary user, not a gap — so
this file spends most of its weight on the promo-code-vs-verification-code
distinction (Correction 1) and on Romanian/Spanish phrasing (Correction 2).
"""
from actions.grounding.web.handoff import await_human, wall_reason
from actions.grounding.web.page import nodes_from_records


def _nodes(*specs):
    return nodes_from_records([
        {"ref": f"e{i}", "name": n, "role": r, "left": 0, "top": i * 20,
         "width": 60, "height": 20, "states": ["ENABLED", "SENSITIVE",
                                               "VISIBLE", "SHOWING"],
         "value": ""}
        for i, (n, r) in enumerate(specs)
    ])


# --- Brief's baseline behaviour --------------------------------------------

def test_a_password_field_is_a_login_wall():
    nodes = _nodes(("Email", "textbox"), ("Password", "password"))
    assert "sign in" in wall_reason(nodes).lower()


def test_a_verification_code_field_is_a_two_factor_wall():
    nodes = _nodes(("Verification code", "textbox"), ("Verify", "button"))
    assert "code" in wall_reason(nodes).lower()


def test_a_human_check_is_named_as_one():
    nodes = _nodes(("I am not a robot", "checkbox"))
    reason = wall_reason(nodes)
    assert reason and "human" in reason.lower()


def test_an_ordinary_page_is_not_a_wall():
    nodes = _nodes(("Search", "searchbox"), ("Home", "link"),
                   ("Settings", "button"))
    assert wall_reason(nodes) == ""


def test_a_page_with_no_controls_is_not_reported_as_a_wall():
    # Thin snapshots are the escalation trigger's problem, not the handoff's.
    assert wall_reason(()) == ""


def test_human_check_reported_first_even_on_a_login_page():
    # Ordering: the part the eagle genuinely cannot do gets named, even when
    # a password field is also present.
    nodes = _nodes(("Password", "password"), ("I am not a robot", "checkbox"))
    assert "human" in wall_reason(nodes).lower()


# --- Correction 1: promo code vs verification code -------------------------
# "code" alone is everywhere on ordinary commerce pages. It needs either a
# positive qualifier (verification, security, sms, ...) or must be ruled out
# by a negative one (promo, discount, coupon, ...).

_PROMO_PHRASES = [
    ("Promo code", "textbox"),
    ("Discount code", "textbox"),
    ("Coupon code", "textbox"),
    ("Referral code", "textbox"),
    ("Gift code", "textbox"),
    ("Voucher code", "textbox"),
    # Romanian
    ("Cod promoțional", "textbox"),
    ("Cod de reducere", "textbox"),
    ("Cod voucher", "textbox"),
    ("Cod cupon", "textbox"),
    ("Cod de invitație", "textbox"),
    # Spanish
    ("Código promocional", "textbox"),
    ("Código de descuento", "textbox"),
    ("Código de cupón", "textbox"),
    ("Código de invitación", "textbox"),
]


def test_every_promo_code_phrasing_is_not_a_wall():
    for name, role in _PROMO_PHRASES:
        nodes = _nodes((name, role))
        assert wall_reason(nodes) == "", f"{name!r} should not be a wall"


def test_a_checkout_page_with_an_ordinary_promo_field_is_not_a_wall():
    # The realistic case the brief's bare "code" match would have broken:
    # a normal checkout, not a two-factor prompt.
    nodes = _nodes(
        ("Full name", "textbox"),
        ("Address", "textbox"),
        ("Promo code", "textbox"),
        ("Place order", "button"),
    )
    assert wall_reason(nodes) == ""


_VERIFICATION_PHRASES = [
    ("Verification code", "textbox"),
    ("Security code", "textbox"),
    ("SMS code", "textbox"),
    # Romanian
    ("Cod de verificare", "textbox"),
    ("Cod de securitate", "textbox"),
    ("Cod SMS", "textbox"),
    ("Codul primit", "textbox"),
    ("Autentificare în doi pași", "text"),
    ("Verificare în doi pași", "text"),
    # Spanish
    ("Código de verificación", "textbox"),
    ("Código de seguridad", "textbox"),
    ("Código SMS", "textbox"),
    ("Verificación en dos pasos", "text"),
    ("Autenticación de dos factores", "text"),
]


def test_every_verification_code_phrasing_triggers_a_handoff():
    for name, role in _VERIFICATION_PHRASES:
        nodes = _nodes((name, role))
        reason = wall_reason(nodes)
        assert reason, f"{name!r} should trigger a handoff"
        assert "code" in reason.lower()


# --- Correction 2: not English-only -----------------------------------------

def test_password_role_detection_needs_no_english_text_at_all():
    nodes = _nodes(("Parolă", "password"))
    reason = wall_reason(nodes)
    assert reason and "sign in" in reason.lower()


def test_password_field_backup_name_match_romanian_diacritic_and_stripped():
    for name in ("Parolă", "Parola", "Introdu parola"):
        nodes = _nodes((name, "textbox"))  # role deliberately not "password"
        reason = wall_reason(nodes)
        assert reason and "sign in" in reason.lower(), name


def test_password_field_backup_name_match_spanish():
    for name in ("Contraseña", "Clave"):
        nodes = _nodes((name, "textbox"))
        reason = wall_reason(nodes)
        assert reason and "sign in" in reason.lower(), name


def test_verification_code_diacritic_and_stripped_forms_both_work():
    for name in ("Código de verificación", "Codigo de verificacion"):
        nodes = _nodes((name, "textbox"))
        assert wall_reason(nodes) != "", name


def test_human_check_detection_romanian():
    nodes = _nodes(("Nu sunt robot", "checkbox"))
    reason = wall_reason(nodes)
    assert reason and "human" in reason.lower()


def test_human_check_detection_romanian_security_phrase():
    nodes = _nodes(("Verificare de securitate", "text"))
    reason = wall_reason(nodes)
    assert reason and "human" in reason.lower()


def test_human_check_detection_spanish():
    nodes = _nodes(("No soy un robot", "checkbox"))
    reason = wall_reason(nodes)
    assert reason and "human" in reason.lower()


def test_human_check_detection_spanish_security_phrase():
    nodes = _nodes(("Verificación de seguridad", "text"))
    reason = wall_reason(nodes)
    assert reason and "human" in reason.lower()


def test_sign_in_button_alone_is_not_a_wall():
    # These are just buttons — a login wall is real, but only when there is
    # actually something (a password field) asking for the user.
    for name in ("Autentificare", "Conectare", "Logare",
                 "Iniciar sesión", "Acceder", "Entrar"):
        nodes = _nodes((name, "button"))
        assert wall_reason(nodes) == "", name


# --- await_human -------------------------------------------------------------

def test_await_human_returns_true_once_the_wall_clears():
    states = ["blocked", "blocked", ""]

    def check():
        return states.pop(0)

    slept = []
    assert await_human(check, timeout=100, poll=2,
                       clock=lambda: 0.0, sleep=slept.append) is True
    assert slept == [2, 2]


def test_await_human_gives_up_and_says_so():
    ticks = iter([0, 1, 2, 400])

    assert await_human(lambda: "still blocked", timeout=300, poll=2,
                       clock=lambda: next(ticks),
                       sleep=lambda _s: None) is False


def test_await_human_survives_a_check_that_explodes():
    calls = {"n": 0}

    def check():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("page navigating")
        return ""

    assert await_human(check, timeout=100, poll=0,
                       clock=lambda: 0.0, sleep=lambda _s: None) is True


# --- Cookie/consent walls (found live on consent.youtube.com) -------------

def _buttons(*names):
    return nodes_from_records([
        {"ref": f"e{i}", "name": n, "role": "button", "left": 0, "top": i * 20,
         "width": 80, "height": 24,
         "states": ["ENABLED", "SENSITIVE", "VISIBLE", "SHOWING"], "value": ""}
        for i, n in enumerate(names)])


def test_a_google_consent_wall_is_recognised_by_its_url():
    """The decisive signal. Google's wall never says "cookie" in readable
    text — it says "Before you continue to YouTube" — but it always moves you
    to a consent host, which is unambiguous."""
    from actions.grounding.web.handoff import cookie_wall_choice
    assert cookie_wall_choice(
        _buttons("Accept all", "Reject all"),
        "https://consent.youtube.com/m?continue=x") == "Reject all"


def test_the_romanian_wall_the_eagle_actually_hit():
    from actions.grounding.web.handoff import cookie_wall_choice
    assert cookie_wall_choice(
        _buttons("Acceptă tot", "Respinge tot"),
        "https://consent.youtube.com/m?hl=ro") == "Respinge tot"


def test_it_never_accepts_tracking_on_the_users_behalf():
    """A wall offering only "Accept all" has no move the eagle may make
    alone. Returning nothing is what routes it to telling the user, and
    `consent.irreversible_reason` refuses "Accept all" independently — two
    separate reasons it cannot happen."""
    from actions.grounding.web.consent import irreversible_reason
    from actions.grounding.web.handoff import cookie_wall_choice
    assert cookie_wall_choice(_buttons("We use cookies", "Accept all")) == ""
    assert irreversible_reason("Accept all") != ""
    assert irreversible_reason("I agree") != ""


def test_the_decline_control_is_one_the_consent_gate_permits():
    """The whole design rests on this: the privacy-preserving choice and the
    unblocking choice are the same choice, so clearing a wall needs no
    exception to the gate."""
    from actions.grounding.web.consent import irreversible_reason
    for label in ["Reject all", "Only necessary", "Respinge tot",
                  "Refuz tot", "Doar necesare", "Rechazar todo"]:
        assert irreversible_reason(label) == "", label


def test_an_ordinary_page_is_not_mistaken_for_a_consent_wall():
    from actions.grounding.web.handoff import cookie_wall_choice
    assert cookie_wall_choice(
        _buttons("Search", "Sign in", "Home", "Reject"),
        "https://news.ycombinator.com") == ""
