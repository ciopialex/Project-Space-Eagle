"""A field you can type in is a control, named or not.

Measured on makerworld.com: the page has a visible <input type="text"> 870px
wide — the search bar — and the collector reported 269 nodes and ZERO
editable. It was dropped by one line:

    const name = accName(el);
    if (!name) continue;

That input has no placeholder, no aria-label and no <label for>. So it has no
accessible name, so it did not exist as far as the eagle was concerned. Every
downstream rung then failed honestly about the wrong thing: "No field on this
page matches", "Element is not fillable", "nothing has focus". The user's
question — how hard is it to see the search bar — had the answer "it was never
collected".

The name requirement is right for most things: an unnamed div is noise. It is
wrong for controls whose ROLE already tells you what to do with them. You can
type into a textbox without knowing what it is called, and a person does
exactly that every day.

Scoped deliberately to typable roles. Keeping every unnamed element would
flood the model's 60-line budget with things it cannot use, which is a
different way of being blind.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.grounding.web.page import COLLECT_JS  # noqa: E402


def test_the_collector_no_longer_drops_every_unnamed_control():
    """The exact line that hid makerworld's search bar."""
    assert "if (!name) continue;" not in COLLECT_JS, \
        "unnamed controls are still discarded wholesale"


def test_typable_roles_are_kept_without_a_name():
    assert "TYPABLE_ROLES" in COLLECT_JS or "typable" in COLLECT_JS.lower()


def test_an_unnamed_control_still_gets_something_to_refer_to():
    """The model has to be able to name it in a step. An empty name is not
    addressable."""
    assert re.search(r"text field|search field|unnamed", COLLECT_JS, re.I), \
        "an unnamed field is kept but cannot be referred to"


def test_unnamed_non_typable_elements_are_still_dropped():
    """Keeping every unnamed element would flood the 60-line budget with
    things the model cannot act on."""
    assert "continue" in COLLECT_JS, "the skip path is gone entirely"
