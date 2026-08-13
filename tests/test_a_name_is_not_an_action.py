"""Verbs inside the NAME of a thing are not steps of their own.

The e2e rig would not even start. `mission start` refused this step:

    Click the Go to Submit page link
    → "That is not a step, it is several"

It counted "click", "go to" and "submit" as three actions. They are one:
a single click, on a link whose name is "Go to Submit page". Websites name
their controls after what they do, so any real workflow is full of this —
"Download the form", "Submit form", "Go to checkout". A step splitter that
cannot tell a verb from a label will refuse the ordinary case.

The rule that replaces the raw count: a determiner opens a NAME, and the
name runs until the noun that ends it ("link", "button", "form", "page"…).
Whatever is inside that span is a label, not an instruction. Count actions
only in what is left. Quoted spans are names too, for the same reason.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.mission_ladder import is_compound  # noqa: E402


# ── the steps the rig actually uses ─────────────────────────────────────────

def test_every_step_in_the_e2e_workflow_is_accepted():
    from tools.mission_e2e import STEPS
    bad = [s for s in STEPS if is_compound(s)]
    assert bad == [], f"the harness cannot start; refused: {bad}"


# ── names that contain verbs ────────────────────────────────────────────────

def test_a_link_named_after_what_it_does_is_one_click():
    assert is_compound("Click the Go to Submit page link") is False


def test_download_is_a_label_here_not_a_second_action():
    assert is_compound("Click the Download the registration form link") is False


def test_a_quoted_name_is_a_name():
    assert is_compound('Click the button labelled "Save and Submit"') is False


def test_ordinary_single_steps_survive():
    for s in ("Open http://127.0.0.1:8971/",
              "Upload the filled form",
              "Click the Submit form button",
              "Read the file my-details.txt on the Desktop",
              "Type Test Testerson into the full name field",
              "Press Enter"):
        assert is_compound(s) is False, s


# ── genuinely compound steps must STILL be refused ──────────────────────────

def test_an_explicit_joiner_is_still_compound():
    assert is_compound("open the dashboard then click download") is True
    assert is_compound("go to makerworld, then search for a stand") is True


def test_three_real_actions_are_still_compound():
    assert is_compound("click download and save the file and upload it") is True


def test_a_whole_workflow_in_one_line_is_refused():
    assert is_compound(
        "download the form, fill it in and submit it") is True


def test_masking_cannot_swallow_the_whole_sentence():
    """A name span must not eat a real second action that follows it."""
    assert is_compound(
        "click the download link then open the file and save it") is True


def test_empty_is_not_compound():
    assert is_compound("") is False
    assert is_compound(None) is False
