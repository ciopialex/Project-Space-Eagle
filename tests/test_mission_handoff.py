"""Tell the outside agent what the eagle IS, before asking it for a plan.

The user's own words: "if the outside agent is aware of what the eagle's
capabilities are, then it knows to make the plan in such a way that the eagle
can slowly grind away at the goal until it can achieve it."

The failure this prevents is specific. Ask any capable agent "how do I
download a laptop stand from MakerWorld" and it will happily answer with
`curl` — correct, useful to a human at a terminal, and completely
unexecutable by something whose hands are a cursor and a keyboard. The
outside agent has to be told it is writing for a GUI operator, and told what
that operator can actually do.

`core/capabilities.py` is already DATA, so the pack states the real surface
rather than a prose description of it that drifts out of date.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.mission import Mission, Step  # noqa: E402
from core.mission_handoff import context_pack, parse_plan  # noqa: E402


# ── what the pack must say ──────────────────────────────────────────────────

def test_the_pack_says_what_the_eagle_is():
    p = context_pack("download a laptop stand").lower()
    assert "keyboard" in p
    assert "cursor" in p or "mouse" in p
    assert "screen" in p


def test_the_pack_forbids_the_shell_answer():
    """The single most likely wrong plan: 'run curl -O ...'."""
    p = context_pack("x").lower()
    assert "shell" in p or "curl" in p or "command" in p


def test_the_pack_lists_real_capability_ids_not_prose():
    from core.capabilities import CATALOGUE
    p = context_pack("x")
    assert any(c.id in p for c in CATALOGUE), "no capability ids in the pack"


def test_the_pack_demands_one_action_per_step():
    p = context_pack("x").lower()
    assert "one" in p and "step" in p


def test_the_pack_forbids_selectors_and_coordinates():
    """A plan naming CSS selectors is a plan for a scraper, not an operator."""
    p = context_pack("x").lower()
    assert "selector" in p or "xpath" in p
    assert "coordinate" in p


def test_the_pack_carries_the_goal_verbatim():
    assert "ship my landing page" in context_pack("ship my landing page")


def test_the_pack_asks_for_numbered_steps_because_that_is_what_is_parsed():
    assert "number" in context_pack("x").lower()


# ── what it hands over when stuck ───────────────────────────────────────────

def test_a_stuck_mission_hands_over_what_was_already_tried():
    m = Mission(goal="download a laptop stand",
                steps=[Step(intent="open makerworld"),
                       Step(intent="click the search box")])
    m.advance()
    m.record_attempt("screen_click", ok=False, detail="not in the a11y tree")
    m.record_attempt("vision_click", ok=False, detail="429 rate limit")
    p = context_pack(m.goal, m)
    assert "screen_click" in p and "a11y tree" in p
    assert "click the search box" in p


def test_progress_already_made_is_stated_so_it_is_not_replanned():
    m = Mission(goal="g", steps=[Step(intent="open makerworld"),
                                 Step(intent="click search")])
    m.advance()
    p = context_pack("g", m)
    assert "open makerworld" in p
    assert "done" in p.lower()


def test_it_is_told_not_to_suggest_what_already_failed():
    m = Mission(goal="g", steps=[Step(intent="click search")])
    m.record_attempt("web_click", ok=False, detail="no DOM")
    p = context_pack("g", m).lower()
    assert "failed" in p and ("not repeat" in p or "do not" in p)


# ── reading the reply back ──────────────────────────────────────────────────

def test_a_returned_plan_becomes_steps():
    steps = parse_plan("""
    1. Open makerworld.com
    2. Click the search box
    3. Type laptop stand
    """)
    assert [s.intent for s in steps] == [
        "Open makerworld.com", "Click the search box", "Type laptop stand"]


def test_bullet_points_are_accepted_too():
    assert len(parse_plan("- Open the page\n- Click download")) == 2


def test_a_plan_with_no_steps_yields_nothing_rather_than_garbage():
    """A refusal or an apology must not become a one-step mission."""
    assert parse_plan("I'm not sure how to do that, sorry.") == []


def test_prose_around_the_steps_is_ignored():
    steps = parse_plan("Sure! Here's how:\n\n1. Open the page\n2. Click it\n\n"
                       "Let me know if that helps!")
    assert [s.intent for s in steps] == ["Open the page", "Click it"]


def test_a_url_in_a_step_is_captured_so_the_ladder_can_open_it():
    steps = parse_plan("1. Go to https://makerworld.com/en and wait for it to load")
    assert steps[0].url == "https://makerworld.com/en"


def test_quoted_text_in_a_typing_step_is_captured():
    steps = parse_plan('1. Type "laptop stand" into the search box')
    assert steps[0].text == "laptop stand"


def test_an_absurdly_long_plan_is_truncated_rather_than_accepted_whole():
    """A 200-step plan is a hallucination, not a plan."""
    plan = "\n".join(f"{i}. Step {i}" for i in range(1, 201))
    assert len(parse_plan(plan)) <= 40
