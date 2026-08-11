"""The rules for a good step must live where the model that writes them reads.

From a real session, the plan came back with:

    3. Select a basic, highly-rated model

That is a judgement, not an action. There is no control on MakerWorld labelled
"a basic, highly-rated model", so all three rungs of the ladder failed and the
mission stopped two steps in. What a person does is: click the Trending
filter, then click the first result — two things you can see.

The cause was mine. The step-quality rules ("one observable action", "name
controls as they appear") were written into `mission_planner._PROMPT`, which
is only the FALLBACK path. Planning was moved to the voice model to save a
rate-limited API call, and the rules stayed behind. It was planning blind.

Second bug from the same session: it asked "any look in mind — soft and pastel,
dark and shiny?" about a laptop stand. The taste question was scoped to "when
the thing has a LOOK", and a laptop stand has a look. It is for BUILDING
something visual, never for finding or downloading something that exists.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402

_PROMPT = (Path(__file__).resolve().parent.parent / "core" / "prompt.txt").read_text()


def _steps_desc() -> str:
    d = {x["name"]: x for x in main.TOOL_DECLARATIONS if isinstance(x, dict)}
    return str(d["mission"]["parameters"]["properties"]["steps"]).lower()


# ── the rules reach the model that writes the steps ─────────────────────────

def test_the_declaration_demands_an_observable_action():
    d = _steps_desc()
    assert "see" in d and "screen" in d


def test_the_declaration_names_the_exact_failure_that_happened():
    """A rule stated abstractly gets ignored; the counter-example does not."""
    assert "highly-rated" in _steps_desc()


def test_the_declaration_shows_what_to_do_instead():
    d = _steps_desc()
    assert "trending" in d and "first result" in d


def test_the_declaration_forbids_selectors_and_coordinates():
    d = _steps_desc()
    assert "selector" in d or "xpath" in d
    assert "coordinate" in d


def test_the_declaration_asks_for_the_address_on_an_open_step():
    """A step with no url blocked the first real mission entirely."""
    assert "address" in _steps_desc() or "url" in _steps_desc()


def test_the_fallback_planner_still_carries_the_same_rules():
    """Both paths must agree, or a delegated plan and a local one differ."""
    from core.mission_planner import _PROMPT as planner_prompt
    low = planner_prompt.lower()
    assert "observable" in low
    assert "selector" in low


# ── the taste question stays where it belongs ───────────────────────────────

def test_the_taste_question_is_scoped_to_building_something():
    low = _PROMPT.lower()
    i = low.index("taste")
    window = low[i:i + 400]
    assert "building" in window or "build" in window


def test_the_taste_question_names_downloading_as_out_of_scope():
    """Stated as a counter-example, because 'a laptop stand has a look' is
    exactly the reading that produced the bug."""
    low = _PROMPT.lower()
    window = low[low.index("taste"):low.index("taste") + 500]
    assert "download" in window
