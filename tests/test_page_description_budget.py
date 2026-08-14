"""What the model is told about a page must not be decided by document order.

`_describe` sent the model `nodes[:60]`. That is a positional accident, not a
relevance decision, and on any page whose content sits below a long navigation
block it sends sixty lines of sidebar and nothing else.

Measured live before this fix:

    page                         nodes  subject-matter  reaching the model
    Wikipedia "Motherboard"        600        8 (idx 206+)          0
    Python docs pathlib            600      100 (idx 13+)           4
    Hacker News front page         228       87 (idx 3+)           23

Hacker News scored well only because its content happens to come first in the
document. That is luck, not perception.

Two signals were measured and REJECTED before settling on the fix:

- **"carries context text"** — on Wikipedia the chrome carries context that
  merely repeats its own name (`'Random article' ctx='Random article'`) while
  only 2 of 11 subject nodes carried any. Weak, and backwards.
- **geometry** — genuinely strong there (chrome median left=53, subject
  median left=342) but it encodes *Wikipedia's* left sidebar. A right-hand
  sidebar, a single-column page or an RTL layout each break it differently.

What survives is layout- and language-independent: spend the budget ACROSS
the page rather than at the top of it, keeping short runs of neighbours so a
control and the text beside it are not separated.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.web_agency import _describe  # noqa: E402
from actions.grounding.web.page import WebNode  # noqa: E402


def _node(name, role="link", context="", top=0):
    return WebNode(name=name, role=role, left=0, top=top, width=80, height=20,
                   ref="e", states=frozenset({"VISIBLE", "ENABLED"}),
                   context=context)


def _page(n_chrome=200, n_content=400):
    """A page shaped like the real ones: a long nav block, then the article."""
    chrome = [_node(f"Nav item {i}", top=i) for i in range(n_chrome)]
    content = [_node(f"Content {i}", context=f"article prose {i}", top=1000 + i)
               for i in range(n_content)]
    return chrome + content


# ── the failure ─────────────────────────────────────────────────────────────

def test_content_below_a_long_nav_block_still_reaches_the_model():
    """The whole point. Before this, 200 nav items exhausted the budget and
    the article was never mentioned."""
    out = _describe(_page())
    assert "Content " in out, "the entire article was truncated away"


def test_a_reasonable_share_of_the_budget_goes_to_content():
    out = _describe(_page())
    content_lines = sum(1 for line in out.splitlines() if "Content " in line)
    assert content_lines >= 15, (
        f"only {content_lines} of the page body survived the budget")


def test_the_navigation_is_not_thrown_away_either():
    """Chrome is how you leave the page. Losing all of it would trade one
    blindness for another."""
    out = _describe(_page())
    assert "Nav item" in out, "no way to navigate off this page"


# ── it must stay within budget and stay readable ────────────────────────────

def _content(out):
    """Only the page lines. The untrusted-content fence around them is
    framing, not budget — see test_page_is_untrusted.py."""
    return [l for l in out.splitlines() if l.startswith("- ")]


def test_the_budget_is_still_respected():
    assert len(_content(_describe(_page()))) <= 60


def test_a_short_page_is_shown_whole():
    nodes = [_node(f"Only {i}") for i in range(12)]
    out = _describe(nodes)
    assert len(_content(out)) == 12
    for i in range(12):
        assert f"Only {i}" in out


def test_an_empty_page_does_not_explode():
    assert _describe([]) == ""


def test_neighbours_stay_together_so_a_control_keeps_its_text():
    """Uniform every-Nth sampling would shred the page — a price separated
    from the thing it prices is worse than not sending it."""
    out = _describe(_page())
    nums = [int(l.split("Content ")[1].split(" ")[0])
            for l in out.splitlines() if "Content " in l]
    runs = sum(1 for a, b in zip(nums, nums[1:]) if b == a + 1)
    assert runs >= len(nums) // 2, "the page was sampled into unrelated fragments"


def test_context_that_merely_repeats_the_name_is_not_printed_twice():
    """Measured on Wikipedia: chrome carries `ctx` identical to its own name.
    Printing it spends budget to say the same word twice."""
    out = _describe([_node("Random article", context="Random article")])
    assert out.count("Random article") == 1, out


def test_real_context_is_still_printed():
    out = _describe([_node("Bambu Lab P2S", context="EUR 519 in stock")])
    assert "EUR 519 in stock" in out


# ── typable controls must never fall in a sampling gap ──────────────────────
#
# Measured live on makerworld.com: `collect()` correctly found the page's
# search box (role='textbox', unnamed, TYPABLE_ROLES fallback names it "text
# field") at document-order index 15 of 273 nodes. `_spread`'s evenly-spaced
# windows (budget=60, run=10 -> a window every ~45 nodes: 0-10, 45-55, ...)
# put it in the gap between window 0 and window 1. It was never dropped for
# being unimportant - it was dropped for landing on the wrong side of an
# arbitrary sampling grid. A page can have hundreds of links and images; it
# rarely has more than a handful of things you can type into, so those are
# cheap to always keep.

def _typable_node(name, role, top):
    """`_node`'s default states lack EDITABLE/SENSITIVE - real editable
    controls carry both (measured live: COLLECT_JS reports
    ENABLED/SENSITIVE/VISIBLE/SHOWING/EDITABLE for makerworld.com's search
    box), and `is_editable` requires them."""
    return WebNode(name=name, role=role, left=0, top=top, width=80, height=20,
                   ref="e", states=frozenset({"ENABLED", "SENSITIVE",
                                              "VISIBLE", "SHOWING", "EDITABLE"}))


def _typable_page(n_before=15, n_after=257, role="textbox", name="text field"):
    before = [_node(f"Nav item {i}", top=i) for i in range(n_before)]
    field = [_typable_node(name, role, n_before)]
    after = [_node(f"Content {i}", top=n_before + 1 + i) for i in range(n_after)]
    return before + field + after


def test_a_lone_search_field_in_a_sampling_gap_still_reaches_the_model():
    out = _describe(_typable_page())
    assert "text field" in out, "the only typable control on the page was dropped"


def test_multiple_typable_controls_all_survive():
    nodes = (_typable_page(n_before=15, n_after=200)
             + [_typable_node("search field", "searchbox", 400)]
             + [_typable_node("password field", "password", 401)])
    out = _describe(nodes)
    assert "text field" in out
    assert "search field" in out
    assert "password field" in out


def test_typable_survival_does_not_blow_the_budget():
    assert len(_content(_describe(_typable_page()))) <= 60
