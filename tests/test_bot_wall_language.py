"""A Cloudflare wall in Romanian is still a Cloudflare wall.

From the user's own session log, twice, on two different sites:

    [Tool] ✓ web_agency (854ms)
    result: 8 controls on https://bambulab.com/:
    - bambulab.com Efectuarea verificării de securitate ... (main)
    - Verificarea a reușit. Se așteaptă un răspuns din partea site-ului ...

`ok=True`. The eagle believed it was on bambulab.com, and the next tool call
was `look` for "printers or products menu" against a challenge page.

It got past BOTH gates independently:

1. **Language.** `_BOT_WALL_PHRASES` contains "performing security
   verification" — in English. The page said "Efectuarea verificării de
   securitate", which is that phrase in Romanian. The user speaks Romanian and
   his browser is Romanian-locale, so for him this is not an edge case, it is
   the DEFAULT rendering of every Cloudflare challenge.

2. **Shape.** The fallback ceiling was 6 controls. The page had 8.

The memory file already names this exact hazard: "Latin-script-but-unknown-
vocabulary is the dangerous fail-open direction." Here it is, live.

The fix does not add Romanian to a word list — that just moves the wall to the
next language. It keys on signals a challenge page has in EVERY language: the
Cloudflare Ray ID footer, the challenge-platform iframe/script, and the
cf_chl/turnstile markers. Words stay as one signal among several, not the only
one.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.grounding.web.handoff import bot_wall_reason  # noqa: E402


class N:
    def __init__(self, name, role="link"):
        self.name = name
        self.role = role


#: The real page, transcribed from the user's log.
ROMANIAN_WALL = [
    N("bambulab.com Efectuarea verificării de securitate Acest site web "
      "utilizează un serviciu de securitate pentru a proteja", "main"),
    N("Pictogramă pentru site-ul bambulab.com", "img"),
    N("bambulab.com", "heading"),
    N("Efectuarea verificării de securitate", "heading"),
    N("Verificarea a reușit. Se așteaptă un răspuns din partea site-ului "
      "bambulab.com", "heading"),
    N("Ray ID: a28f99a1cd10e1a5 Performanță și securitate de la Cloudflare "
      "Confidențialitate", "contentinfo"),
    N("Cloudflare, se deschide într-o filă nouă", "link"),
    N("Confidențialitate, se deschide într-o filă nouă", "link"),
]


def test_the_romanian_wall_from_the_real_log_is_caught():
    assert bot_wall_reason(ROMANIAN_WALL, "https://bambulab.com/"), \
        "reported a Cloudflare challenge as a readable page"


def test_it_is_caught_despite_being_over_the_old_control_ceiling():
    """8 controls, and the old shape gate gave up above 6."""
    assert len(ROMANIAN_WALL) == 8
    assert bot_wall_reason(ROMANIAN_WALL, "https://makerworld.com/")


def test_the_english_wall_still_works():
    page = [N("Just a moment...", "heading"),
            N("Enable JavaScript and cookies to continue", "heading")]
    assert bot_wall_reason(page, "https://example.com/")


def test_a_challenge_url_is_still_conclusive_on_its_own():
    assert bot_wall_reason([], "https://x.com/cdn-cgi/challenge-platform/h/b")


# ── it must not start crying wolf ───────────────────────────────────────────

def test_a_real_page_that_merely_mentions_cloudflare_is_not_a_wall():
    """Cloudflare's own docs, a hosting comparison, a status page. The eagle
    was ON a Cloudflare dashboard earlier in this very conversation."""
    page = [N(f"Nav {i}") for i in range(40)] + [
        N("Cloudflare Workers documentation", "heading"),
        N("Get started with Cloudflare", "link"),
        N("Ray ID explained", "link"),
    ]
    assert bot_wall_reason(page, "https://developers.cloudflare.com/workers/") == ""


def test_a_shop_selling_security_products_is_not_a_wall():
    page = [N("Security cameras", "link"), N("Verification service", "link"),
            N("Add to cart", "button")] + [N(f"Product {i}") for i in range(30)]
    assert bot_wall_reason(page, "https://shop.example.com/") == ""


def test_a_normal_small_page_is_not_a_wall():
    page = [N("Example Domain", "heading"), N("Learn more", "link")]
    assert bot_wall_reason(page, "https://example.com/") == ""


def test_an_empty_page_is_not_asserted_to_be_a_wall():
    """A blank read is 'could not see', which is a different message with a
    different next step. Do not collapse the two."""
    assert bot_wall_reason([], "https://example.com/") == ""
