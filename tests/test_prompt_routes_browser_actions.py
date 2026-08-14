"""Live, 2026-08-13/14: given a single command like "click the search bar,"
the model reached for computer_control's pixel path or web_agency's
DISCONNECTED hidden browser — never the DOM-exact browser_control
click/type/look added in this same plan — because nothing told it to.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_prompt_prefers_browser_control_dom_actions_for_visible_browser_commands():
    prompt = (Path(__file__).resolve().parent.parent / "core" / "prompt.txt").read_text().lower()
    assert "browser_control" in prompt
    # The guidance must actually connect a single click/type command aimed
    # at what the user can see to browser_control's click/type/look, not
    # just mention both tools somewhere unrelated.
    assert "computer_control" in prompt
    # Assert on the specific connective language that wires routing
    assert "same exact dom lookup" in prompt


def test_web_agency_bullet_does_not_shadow_the_single_command_rule():
    """Final whole-branch review, Finding 6: bullet 1 of ROUTING PRECEDENCE
    ("Anything INSIDE a website ... → web_agency") sits ABOVE the single-
    command browser_control rule, and under a stated precedence order a
    higher bullet wins — so "click the search bar" resolved to web_agency
    before the model ever reached the click/type/look bullet. The fix is an
    inline exception in bullet 1 itself, so the two rules read together
    instead of contradicting.
    """
    prompt = (Path(__file__).resolve().parent.parent / "core" / "prompt.txt").read_text().lower()
    web_agency_bullet = next(
        line for line in prompt.splitlines()
        if line.strip().startswith("- anything inside a website"))
    assert "browser_control" in web_agency_bullet
    assert "click/type/look" in web_agency_bullet
