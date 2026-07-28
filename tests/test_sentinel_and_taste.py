"""The re-delegation loop, the log bells, and the taste pipeline.

Run:  .venv/bin/python -m pytest tests/ -q

THE LOOP THIS PINS DOWN
-----------------------
Observed live: agents finished the nail-salon build, wrote status "completed"
to the blackboard, committed their work, and went quiet. The sentinel judged
liveness purely by seconds_since_activity, so a FINISHED agent was
indistinguishable from a hung one — it re-delegated, the replacement found the
work already done, reported "no new commit needed", went quiet in turn, and was
re-delegated again. For an hour. Each hand-off spawned a session and a window.

Done and stuck produce exactly the same amount of output: none. The fix is that
silence is ambiguous and the blackboard is not.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.pty_session import _clean_for_log  # noqa: E402
from actions.swarm_sentinel import (  # noqa: E402
    MAX_FAILOVERS_PER_WORKSTREAM, TERMINAL_STATUSES, SwarmSentinel,
)
from core import aesthetics  # noqa: E402


# ------------------------------------------------------- the sentinel loop

class FakeBoard:
    def __init__(self, status):
        self._status = status

    def read(self):
        return {"agents": {"site": {"status": self._status,
                                    "worktree": "/tmp/wt/site"}}}


@pytest.mark.parametrize("status", sorted(TERMINAL_STATUSES))
def test_finished_workstreams_are_never_touched(status, monkeypatch):
    """The regression: a completed agent is silent, and silence used to mean
    'stuck'. It must now mean nothing at all."""
    s = SwarmSentinel()
    monkeypatch.setattr(s, "_board_entry",
                        lambda b, d, k: ("site", b.read()["agents"]["site"]))
    monkeypatch.setattr("actions.swarm_orchestrator.Blackboard",
                        lambda root: FakeBoard(status))
    assert s._is_finished("/tmp/root", "/tmp/wt/site", "claude_code") is True


@pytest.mark.parametrize("status", ["working", "", "review"])
def test_unfinished_workstreams_are_still_healed(status, monkeypatch):
    """The fix must not disable self-healing for genuinely stuck agents."""
    s = SwarmSentinel()
    monkeypatch.setattr(s, "_board_entry",
                        lambda b, d, k: ("site", b.read()["agents"]["site"]))
    monkeypatch.setattr("actions.swarm_orchestrator.Blackboard",
                        lambda root: FakeBoard(status))
    assert s._is_finished("/tmp/root", "/tmp/wt/site", "claude_code") is False


def test_unreadable_board_fails_open(monkeypatch):
    """A corrupt state file must degrade to the old behaviour, not silently
    switch self-healing off."""
    s = SwarmSentinel()

    def boom(root):
        raise OSError("state file is gibberish")

    monkeypatch.setattr("actions.swarm_orchestrator.Blackboard", boom)
    assert s._is_finished("/tmp/root", "/tmp/wt/site", "claude_code") is False


def test_failover_ceiling_is_small_and_finite():
    """Without a ceiling an un-completable task recruits every installed CLI in
    an endless relay, one window each."""
    assert 1 <= MAX_FAILOVERS_PER_WORKSTREAM <= 3


def test_completed_is_a_terminal_status():
    assert "completed" in TERMINAL_STATUSES and "merged" in TERMINAL_STATUSES


# ------------------------------------------------------------ the log bells

def test_window_title_escapes_are_stripped_whole():
    """Claude Code repaints its title with a spinner — 231 sequences in one
    observed session, each ending in BEL. That was the notification sound every
    couple of seconds, and it renamed our own viewer windows to look like
    unrelated apps."""
    raw = (b"\x1b]0;\xe2\x9c\xb3 Build static landing page\x07"
           b"actual output\x1b]2;Claude Code\x07more")
    out = _clean_for_log(raw)
    assert b"Build static landing page" not in out
    assert b"actual output" in out and b"more" in out
    assert b"\x07" not in out


def test_partial_escape_does_not_eat_the_stream():
    """Stripping only the BEL would leave ESC]0;title unterminated, and a
    terminal will swallow everything after it as more title."""
    out = _clean_for_log(b"\x1b]0;unterminated title")
    assert b"unterminated title" not in out


def test_ordinary_output_is_untouched():
    payload = b"Create(/home/u/site/index.html)\nThought for 3s\n"
    assert _clean_for_log(payload) == payload


# --------------------------------------------------------------- the taste

def test_seven_axes_four_words_each():
    opts = aesthetics.options()
    assert len(opts) == 7
    for o in opts:
        assert len(o["words"]) == 4, o


def test_vocabulary_is_plain_language():
    """A person booking nail appointments must never have to decode a
    design-school term to say what they like."""
    jargon = {"chromatic", "tactile", "editorial", "geometric", "organic",
              "typography", "density", "palette", "monochrome", "saturation"}
    for o in aesthetics.options():
        for w in o["words"] + [o["label"]]:
            assert not (jargon & set(w.lower().split())), f"jargon: {w}"
            assert len(w) <= 14, f"too long to be a chip: {w}"


def test_brief_carries_buildable_meaning_not_just_the_word():
    """'Strong' tells an agent nothing; the brief must ship the spec too."""
    brief = aesthetics.brief_from_choices({"colors": "Pastel", "surface": "Matte"})
    assert "Pastel" in brief and "Matte" in brief
    assert "muted" in brief.lower()
    assert "hex" in brief.lower(), "architect isn't told to pin exact values"


def test_partial_picks_are_kept_not_defaulted():
    """Half a picker is better direction than none; inventing the rest would
    put words in the user's mouth."""
    brief = aesthetics.brief_from_choices({"colors": "Dark"})
    assert "Dark" in brief
    assert "Pastel" not in brief and "Shiny" not in brief


def test_no_choices_yields_no_brief():
    assert aesthetics.brief_from_choices({}) == ""
    assert aesthetics.brief_from_text("  ") == ""


def test_free_text_survives_in_the_users_own_words():
    brief = aesthetics.brief_from_text("like a Tokyo nail bar, dark and neon")
    assert "Tokyo nail bar" in brief


def test_spoken_shorthand_maps_onto_the_picker():
    brief = aesthetics.brief_from_answer("soft and pastel please")
    assert "Pastel" in brief and "Calm" in brief


def test_unrecognised_speech_is_kept_verbatim():
    brief = aesthetics.brief_from_answer("kind of like a 1970s diner")
    assert "1970s diner" in brief


def test_the_one_question_is_one_question_with_options():
    q = aesthetics.the_one_question()
    assert q.count("?") == 1, "must be a single question, not an interview"
    assert "pastel" in q.lower() and "shiny" in q.lower()


# ------------------------------------------------- architect integration

def test_design_brief_reaches_the_architect_prompt():
    from actions.chief_architect import build_architect_prompt
    brief = aesthetics.brief_from_choices({"colors": "Dark", "surface": "Shiny"})
    p = build_architect_prompt("a landing page for a nail salon", Path("/tmp/x"),
                               2, ["claude_code"], design_brief=brief)
    assert "Dark" in p and "Shiny" in p
    assert "acceptance criteria" in p.lower()


def test_visual_mission_without_a_brief_still_gets_design_direction():
    """The last run produced eight functional criteria and nothing about looks.
    Silence must not mean 'skip design'."""
    from actions.chief_architect import build_architect_prompt
    p = build_architect_prompt("build a landing page for a nail salon",
                               Path("/tmp/x"), 2, ["claude_code"])
    assert "DESIGN" in p
    assert "CHOOSE a coherent visual direction" in p


def test_non_visual_mission_is_not_burdened_with_design():
    from actions.chief_architect import build_architect_prompt
    p = build_architect_prompt("a script to rename files in a folder",
                               Path("/tmp/x"), 2, ["claude_code"])
    assert "DESIGN (" not in p


def test_readme_is_a_standing_principle():
    """Only `booking` shipped a README last run — because only its criteria
    asked for one. Hope is not a mechanism."""
    from actions.chief_architect import _ARCHITECT_PRINCIPLES
    assert "README" in _ARCHITECT_PRINCIPLES


def test_self_hosted_assets_are_allowed_for_visual_work():
    """The blanket no-external-assets rule forced system fonts and hand-drawn
    SVG stand-ins onto a product whose whole job was to look good."""
    from actions.chief_architect import _DESIGN_PRINCIPLES
    assert "SELF-HOSTED" in _DESIGN_PRINCIPLES
    assert "NOT SVG placeholders" in aesthetics.AXES["pictures"][1]["Photos"]
