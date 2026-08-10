"""The loop is only worth anything if the model can reach it."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402


def _decl():
    return {d["name"]: d for d in main.TOOL_DECLARATIONS if isinstance(d, dict)}


def test_the_mission_tool_is_declared():
    assert "mission" in _decl()


def test_the_declaration_lists_every_action_the_tool_implements():
    import actions.mission as M
    desc = str(_decl()["mission"]["parameters"]["properties"]["action"])
    for verb in M._ACTIONS:
        assert verb in desc, f"{verb} is implemented but never advertised"


def test_the_declaration_says_it_is_for_multi_step_goals():
    d = _decl()["mission"]["description"].lower()
    assert "step" in d
    assert "more than one" in d or "several" in d


def test_the_declaration_tells_the_model_to_keep_calling_next():
    """One `next` is one step. Without this the model starts a mission and
    then waits for the user to drive it — which is the thing being fixed."""
    desc = str(_decl()["mission"]["parameters"]["properties"]["action"]).lower()
    assert "until" in desc and "next" in desc


def test_the_prompt_routes_multi_action_requests_to_it():
    prompt = (Path(__file__).resolve().parent.parent /
              "core" / "prompt.txt").read_text().lower()
    assert "mission" in prompt
    assert "more than one action" in prompt


def test_the_prompt_says_not_to_narrate_every_step():
    """Speech is latency; `spoken` already had a 4.9s median."""
    prompt = (Path(__file__).resolve().parent.parent /
              "core" / "prompt.txt").read_text().lower()
    assert "narrate" in prompt


def test_it_is_dispatched_not_merely_declared():
    src = (Path(__file__).resolve().parent.parent / "main.py").read_text()
    assert 'elif name == "mission":' in src, "declared but never dispatched"
    assert "from actions.mission import mission" in src
