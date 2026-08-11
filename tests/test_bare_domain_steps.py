"""A step saying "Go to makerworld.com" has a URL in it.

From a real voice session, the mission planned in 2ms and then died on step 1:

    Stuck on: Go to makerworld.com.
    Tried web_open (No URL to open.), browser_open (no url to open).

The model wrote a bare domain, as a person would. `parse_plan`'s URL pattern
required a scheme, captured nothing, and both rungs were handed an empty url.
`browser_control._normalize_url` has done exactly this job for months.

Also here: the model called `action='plan'` before `action='start'`. Same
shape as `type_text` — a name from a neighbouring API, refused correctly, then
self-corrected. Cheaper to accept it than to be right about it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.mission_handoff import parse_plan  # noqa: E402


def _url(line):
    steps = parse_plan(f"1. {line}")
    return steps[0].url if steps else None


# ── the exact failing step ──────────────────────────────────────────────────

def test_the_step_that_blocked_the_real_mission():
    assert _url("Go to makerworld.com") == "https://makerworld.com"


def test_a_bare_domain_anywhere_in_the_step():
    assert _url("Open makerworld.com and wait for it to load") == "https://makerworld.com"


def test_a_full_url_is_untouched():
    assert _url("Go to https://makerworld.com/en") == "https://makerworld.com/en"


def test_www_is_handled():
    assert _url("Go to www.makerworld.com") == "https://www.makerworld.com"


def test_a_path_survives():
    assert _url("Open makerworld.com/en/models") == "https://makerworld.com/en/models"


# ── it must not invent URLs out of ordinary words ───────────────────────────

def test_a_step_with_no_site_in_it_has_no_url():
    assert not _url("Click the search box")
    assert not _url("Type laptop stand")
    assert not _url("Select a basic, highly-rated model")


def test_a_sentence_ending_in_a_full_stop_is_not_a_domain():
    """'Download the STL files.' must not become https://files."""
    assert not _url("Download the STL files.")


def test_a_filename_is_not_a_domain():
    assert not _url("Open laptop_stand.stl in the slicer")
    assert not _url("Save it as report.pdf")


def test_a_decimal_number_is_not_a_domain():
    assert not _url("Set the layer height to 0.2mm")


def test_an_unknown_suffix_is_not_a_domain():
    assert not _url("Check the readme.notarealtld file")
