"""What the eagle will not click on its own.

The motivating example — filing taxes — ends in an act that cannot be undone.
Every other guard in this system is about doing the right thing; this one is
about not being the thing that decides.

The list is deliberately name-based rather than structural. Basing it on "is
this a form submit" would refuse the search box on every site in the world,
which trains everyone to switch it off. Basing it on what the button SAYS
refuses the small number of controls that actually commit something.
"""
import pytest

from actions.grounding.web.consent import irreversible_reason


@pytest.mark.parametrize("name", [
    "Pay now", "Complete purchase", "Place order", "Buy it now",
    "Checkout", "Transfer funds", "Submit return", "File my taxes",
    "Confirm and pay", "Delete account", "Send payment",
    "Accept and continue", "Sign agreement", "Subscribe",
    # Holes found in review: multi-word phrases and additional verbs
    "Pay in full", "Pay in 4 interest-free payments", "Sign in and pay",
    "Confirm order details", "Submit payment details",
    "Pay with saved payment method", "Delete all history",
    "Cancel subscription", "Unsubscribe", "Close account",
    "Withdraw funds", "Donate now",
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
    # Verify that multi-word reading phrases (3+ words) do NOT exempt the
    # label when they contain a committing verb.
    assert irreversible_reason("Confirm order details", "button") != ""
    assert irreversible_reason("Submit payment details", "button") != ""


def test_benign_labels_that_contain_committing_verbs_are_allowed():
    # "Sign in" contains "sign" (a committing verb) but is exempted as a whole
    # label. "Sign in and pay" does not match the whole-label exemption.
    assert irreversible_reason("Sign in", "link") == ""
    assert irreversible_reason("Login", "link") == ""
    assert irreversible_reason("Sign out", "link") == ""
    # Multi-word variants must be explicitly in the exemption set or they will
    # be checked for verbs.
    assert irreversible_reason("Sign in and pay", "button") != ""


def test_reading_words_only_exempt_short_phrases():
    # Reading words exempt labels only when they are short noun phrases
    # (≤2 words) ending in the reading word. This prevents false negatives.
    assert irreversible_reason("Payment history", "link") == ""  # 2 words, ends in reading word
    assert irreversible_reason("Order history", "link") == ""  # 2 words, ends in reading word
    assert irreversible_reason("Your orders", "link") == ""  # 2 words, ends in reading word
    assert irreversible_reason("Delete all history", "button") != ""  # 3 words, has "delete"
    assert irreversible_reason("Pay with saved payment method", "button") != ""  # 5 words, has "pay"


def test_an_empty_name_is_refused_because_we_cannot_tell_what_it_does():
    assert irreversible_reason("", "push button") != ""


def test_links_that_merely_read_are_allowed():
    assert irreversible_reason("Your orders", "link") == ""
