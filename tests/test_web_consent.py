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

from actions.grounding.web.consent import irreversible_reason


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
