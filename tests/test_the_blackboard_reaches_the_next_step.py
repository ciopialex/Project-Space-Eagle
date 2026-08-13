"""A workflow that touches the disk, not just the browser.

The observed failure, live: `tools/mission_e2e.py` ran

    Click the Download the registration form link
    Read the file my-details.txt on the Desktop
    Fill the downloaded form with those details and save it

and step 1 reported done having downloaded nothing (the CLICK ladder does a
plain DOM click, not `web_agency`'s dedicated `download` action, so no file
ever lands on disk); step 2 had no runner at all ("Tried nothing available");
and once both were fixed, the filename regex used to find "my-details.txt on
the Desktop" matched the WHOLE sentence "read the file my-details.txt" as one
filename, because `[\\w .\\-]*` allows spaces and there is only one `.txt` in
the sentence for it to stop at.

Fixed by: a `download` ladder (`kind_of` routes the bare word "download",
never "downloaded") so a click that produces a file goes through
`web_agency`'s `download` action; `file_read`/`file_write` runners that never
touch the browser; and a filename pattern (`\\S+\\.ext`) that stops at
whitespace, because a real filename never has spaces inside it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.mission import Mission, Step  # noqa: E402
from core.mission_ladder import attempt, kind_of  # noqa: E402
from core.mission_runners import (_fill_template, _file_read,  # noqa: E402
                                  _named_file, _parse_fields)


# ── kind_of routing ─────────────────────────────────────────────────────────

def test_the_bare_word_download_routes_off_the_click_ladder():
    assert kind_of(Step(intent="Click the Download the registration form link")) \
        == "download"
    assert kind_of(Step(intent="Download the report")) == "download"


def test_downloaded_past_tense_is_a_file_not_a_browser_action():
    """"downloaded" must not collide with "download" — one names a FILE
    already on disk, the other names a browser action that produces one."""
    assert kind_of(Step(intent="Fill the downloaded form with those details "
                        "and save it")) == "file_write"


def test_read_a_local_file_never_touches_the_page():
    s = kind_of(Step(intent="Read the file my-details.txt on the Desktop"))
    assert s == "file_read"


# ── filename extraction — the regression itself ─────────────────────────────

def test_a_filename_inside_a_sentence_is_not_the_whole_sentence(tmp_path,
                                                                monkeypatch):
    """The exact bug: `[\\w .\\-]*` matched from "read" all the way to the
    extension because everything in between — including the spaces — was a
    legal character in that class, and there was only one ".txt" to stop at."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / "Desktop").mkdir()
    path = _named_file("read the file my-details.txt on the desktop")
    assert path == tmp_path / "Desktop" / "my-details.txt"


def test_no_filename_in_the_wording_is_none_not_a_guess():
    assert _named_file("fill it in and save it") is None


# ── field parsing and template filling ──────────────────────────────────────

def test_label_value_lines_are_harvested_case_folded():
    text = "My details\n-----------\nFull name: Test Testerson\nEmail: a@b.c\n"
    fields = _parse_fields(text)
    assert fields == {"full name": "Test Testerson", "email": "a@b.c"}


def test_a_blank_template_line_is_not_mistaken_for_data():
    """"FULL NAME:" with nothing after the colon is the TEMPLATE's own empty
    line, not a value — keeping it would overwrite a real answer with ''."""
    fields = _parse_fields("FULL NAME:\nEMAIL:\n")
    assert fields == {}


def test_filling_leaves_non_field_lines_untouched():
    template = "REGISTRATION FORM\n\nFULL NAME:\nEMAIL:\n"
    filled, missing = _fill_template(
        template, {"full name": "Test Testerson", "email": "a@b.c"})
    assert missing == []
    assert "REGISTRATION FORM" in filled
    assert "FULL NAME: Test Testerson" in filled
    assert "EMAIL: a@b.c" in filled


def test_a_field_with_no_value_is_reported_missing_not_left_blank():
    filled, missing = _fill_template("FULL NAME:\nCITY:\n",
                                     {"full name": "Test Testerson"})
    assert missing == ["CITY"]


# ── the runners, end to end, against real files ─────────────────────────────

def _mission_with(intent: str) -> Mission:
    return Mission(goal="g", steps=[Step(intent=intent)])


def test_file_read_harvests_fields_onto_the_blackboard(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / "Desktop").mkdir()
    (tmp_path / "Desktop" / "my-details.txt").write_text(
        "Full name: Test Testerson\nEmail: a@b.c\n", encoding="utf-8")

    m = _mission_with("Read the file my-details.txt on the Desktop")
    out = attempt(m.current(), m, {"file_read": _file_read})
    assert out.ok is True
    assert m.facts["fields"] == {"full name": "Test Testerson", "email": "a@b.c"}


def test_file_write_needs_fields_before_it_can_fill_anything():
    """Reported as a failure, never a silently empty copy — a mission that
    fills a form with nothing must not look identical to one that filled it
    correctly, or a later upload would send garbage and call it done."""
    from core.mission_runners import _file_write
    m = _mission_with("Fill the downloaded form with those details and save it")
    out = attempt(m.current(), m, {"file_write": _file_write})
    assert out.ok is False
    assert "no details" in out.detail.lower()


def test_the_whole_chain_hands_facts_forward(tmp_path, monkeypatch):
    """download -> read -> write, using only the blackboard to connect them —
    no step re-derives what an earlier one already found."""
    from core.mission_runners import _file_read, _file_write
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / "Desktop").mkdir()
    (tmp_path / "Desktop" / "my-details.txt").write_text(
        "Full name: Test Testerson\nEmail: a@b.c\n", encoding="utf-8")
    template = tmp_path / "registration-form.txt"
    template.write_text("FULL NAME:\nEMAIL:\n", encoding="utf-8")

    m = Mission(goal="g", steps=[
        Step(intent="Read the file my-details.txt on the Desktop"),
        Step(intent="Fill the downloaded form with those details and save it"),
    ])
    # Simulate what the download runner would have stashed.
    m.facts["downloaded_file"] = str(template)

    out1 = attempt(m.current(), m, {"file_read": _file_read})
    assert out1.ok is True
    m.advance()

    out2 = attempt(m.current(), m, {"file_write": _file_write})
    assert out2.ok is True
    filled = Path(m.facts["filled_file"])
    assert filled.name == "registration-form-filled.txt"
    text = filled.read_text(encoding="utf-8")
    assert "FULL NAME: Test Testerson" in text
    assert "EMAIL: a@b.c" in text
