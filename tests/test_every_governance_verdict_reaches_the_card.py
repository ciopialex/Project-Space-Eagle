"""A pay-versus-performance verdict the card has no icon for is still shown.

The harness mapped six verdict names onto the card's labels; the trade module
says eight, and one of the six it no longer says. RISING TIDE SKEPTICISM and
INEFFICIENT GROWTH drew an em dash where the finding belonged. The adapter now
passes any verdict it cannot map through in its own words, so a verdict the
module adds tomorrow reaches the card too.
"""
from core.card_assembly import adapt_governance


def test_a_verdict_the_card_has_no_icon_for_keeps_its_own_words():
    out = adapt_governance({"verdict": "RISING TIDE SKEPTICISM",
                            "sentence": "Stock is up, but peers did better."})
    assert out["gov_label"] == "RISING TIDE SKEPTICISM"


def test_a_verdict_nobody_has_seen_yet_is_not_a_dash():
    out = adapt_governance({"verdict": "Something New", "sentence": "x"})
    assert out["gov_label"] == "SOMETHING NEW"


def test_the_mapped_ones_still_get_their_icon_label():
    assert adapt_governance({"verdict": "TOTAL DRAIN"})["gov_label"] == "CASHING OUT"
    assert adapt_governance({"verdict": "SHARED PAIN"})["gov_label"] == "FAIR DEAL"


def test_no_verdict_means_no_label():
    assert "gov_label" not in adapt_governance({"sentence": "x"})
