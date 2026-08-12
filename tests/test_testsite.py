"""The rig itself has to be trustworthy, or its verdicts are worthless.

A harness that can pass without the work being done is worse than no harness:
it manufactures the exact false confidence this codebase has spent weeks
removing from its tools. So the verifier is tested before it is trusted.
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.testsite.server import (DESKTOP_DATA, EXPECTED,  # noqa: E402
                                   FORM_TEMPLATE, check, prepare, serve)

PORT = 8973
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture(scope="module", autouse=True)
def _site():
    httpd = serve(PORT)
    yield
    httpd.shutdown()


def _get(path):
    return urllib.request.urlopen(BASE + path, timeout=5).read().decode()


# ── the verifier must not be foolable ───────────────────────────────────────

def test_a_correctly_filled_form_is_accepted():
    filled = "\n".join(f"{k.upper()}: {v}" for k, v in EXPECTED.items())
    assert check(filled)[0] is True


def test_the_blank_template_is_rejected():
    """The likeliest false pass: the eagle uploads what it downloaded."""
    ok, missing = check(FORM_TEMPLATE)
    assert ok is False
    assert len(missing) == len(EXPECTED)


def test_an_empty_upload_is_rejected():
    assert check("")[0] is False


def test_a_partially_filled_form_is_rejected():
    """Three of five is not a completed form."""
    ok, missing = check("FULL NAME: Shenny Cioponea\nEMAIL: "
                        "shennyonthebeat@gmail.com\nCITY: Bucharest")
    assert ok is False
    assert set(missing) == {"phone", "reference"}


def test_layout_does_not_matter_only_the_values_do():
    """The eagle may format the form differently; that is not a failure."""
    prose = ("Here are the details: Shenny Cioponea, reachable at "
             "shennyonthebeat@gmail.com or +40 700 111 222, based in "
             "Bucharest, ref EAGLE-2026-A.")
    assert check(prose)[0] is True


def test_case_does_not_matter():
    assert check("\n".join(f"{k}: {v.upper()}" for k, v in EXPECTED.items()))[0]


def test_the_expected_values_are_not_in_the_goal_anywhere():
    """If the values appeared in the instruction, the eagle could pass without
    ever reading the Desktop file — and reading it is half the test."""
    from tools.mission_e2e import GOAL, STEPS
    haystack = (GOAL + " " + " ".join(STEPS)).lower()
    for value in EXPECTED.values():
        assert value.lower() not in haystack, f"{value!r} leaked into the goal"


def test_the_values_ARE_in_the_desktop_file():
    for value in EXPECTED.values():
        assert value in DESKTOP_DATA


# ── the site serves what the workflow needs ─────────────────────────────────

def test_the_dashboard_offers_a_download_and_a_route_to_submit():
    page = _get("/")
    assert "download" in page.lower()
    assert "submit" in page.lower()


def test_the_form_downloads_as_an_attachment():
    with urllib.request.urlopen(BASE + "/form.txt", timeout=5) as r:
        assert "attachment" in r.headers.get("Content-Disposition", "")
        assert "FULL NAME" in r.read().decode()


def test_the_submit_page_has_a_file_input_and_a_button():
    page = _get("/submit")
    assert 'type="file"' in page
    assert "submit" in page.lower()


def test_the_desktop_file_is_written_where_it_is_claimed(tmp_path):
    p = prepare(tmp_path)
    assert p.exists() and p.name == "my-details.txt"
    assert "Bucharest" in p.read_text()


def test_submissions_start_empty_after_a_reset():
    _get("/reset")
    import json
    assert json.loads(_get("/submissions")) == []
