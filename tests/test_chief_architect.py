"""Tests for the plan-validation gate.

Run:  .venv/bin/python -m pytest tests/ -q

`validate_plan` is the deterministic gate between "an LLM produced some JSON"
and "a swarm of agents starts editing a repository". It is the last purely
mechanical check before real work happens, so every rejection path deserves a
test — a validator that silently accepts a malformed plan hands agents an
incoherent mission and the failure surfaces much later, as merge conflicts.

None of this spawns an agent or costs anything: the functions under test are
pure, which is exactly why the plan handoff was designed as a file contract.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.chief_architect import (  # noqa: E402
    STATE_DIR, read_plan_when_ready, render_plan_summary, validate_plan,
)

AGENTS = ["claude_code", "antigravity_cli"]


def good_plan(**over):
    plan = {
        "goal": "a booking site for a dental clinic",
        "agent_count": 2,
        "coupled": True,
        "blackboard": True,
        "contract": {"base_url": "/api", "ownership": {"api": "server/", "web": "client/"}},
        "workstreams": [
            {"id": "api", "assignee": "claude_code", "task": "Build the FastAPI booking backend.",
             "owns": ["server/"], "acceptance": ["POST /appointments returns 201"]},
            {"id": "web", "assignee": "antigravity_cli", "task": "Build the booking UI.",
             "owns": ["client/"], "acceptance": ["form submits and shows confirmation"]},
        ],
        "merge_order": ["api", "web"],
    }
    plan.update(over)
    return plan


def test_valid_plan_passes():
    ok, reason = validate_plan(good_plan(), 2, AGENTS)
    assert ok, reason


# ------------------------------------------------------------ rejection paths

@pytest.mark.parametrize("plan,fragment", [
    ("not a dict",                                              "not a JSON object"),
    (good_plan(agent_count=0),                                  "agent_count"),
    (good_plan(agent_count=99),                                 "agent_count"),
    (good_plan(agent_count="two"),                              "agent_count"),
    # in range, but disagrees with the number of workstreams actually listed
    (good_plan(agent_count=1),                                  "workstreams"),
    (good_plan(workstreams="nope"),                             "workstreams"),
])
def test_structural_rejections(plan, fragment):
    ok, reason = validate_plan(plan, 2, AGENTS)
    assert not ok and fragment in reason, reason


def test_rejects_unknown_assignee():
    """A plan may only assign agents that are actually installed."""
    p = good_plan()
    p["workstreams"][0]["assignee"] = "grok_cli"
    ok, reason = validate_plan(p, 2, AGENTS)
    assert not ok and "not an" in reason


def test_rejects_missing_required_field():
    p = good_plan()
    del p["workstreams"][1]["task"]
    ok, reason = validate_plan(p, 2, AGENTS)
    assert not ok and "task" in reason


def test_rejects_duplicate_workstream_ids():
    """Duplicate ids collapse two worktrees into one — silent corruption."""
    p = good_plan()
    p["workstreams"][1]["id"] = "api"
    ok, reason = validate_plan(p, 2, AGENTS)
    assert not ok and "duplicate" in reason


def test_coupled_plan_requires_a_contract():
    """Coupled agents that never got a frozen interface will negotiate it
    live, which they cannot do — they run in isolated worktrees."""
    ok, reason = validate_plan(good_plan(contract={}), 2, AGENTS)
    assert not ok and "contract" in reason


def test_coupled_plan_requires_the_blackboard():
    ok, reason = validate_plan(good_plan(blackboard=False), 2, AGENTS)
    assert not ok and "blackboard" in reason


def test_rejects_merge_order_naming_unknown_workstream():
    ok, reason = validate_plan(good_plan(merge_order=["api", "ghost"]), 2, AGENTS)
    assert not ok and "merge_order" in reason


def test_over_allocation_is_capped_by_max_agents():
    """max_agents is the F1 capacity signal — the validator must honour it."""
    ok, reason = validate_plan(good_plan(), 1, AGENTS)
    assert not ok and "agent_count" in reason


def test_independent_plan_needs_no_contract():
    p = good_plan(coupled=False, blackboard=False, contract={})
    ok, reason = validate_plan(p, 2, AGENTS)
    assert ok, reason


# ------------------------------------------------------- failure injection

@pytest.mark.parametrize("junk", [None, [], 42, "", {"agent_count": None}])
def test_validator_never_raises_on_garbage(junk):
    """The validator sits directly downstream of LLM output. It must reject,
    never crash — an exception here would abort the mission opaquely."""
    ok, reason = validate_plan(junk, 2, AGENTS)
    assert ok is False and isinstance(reason, str)


# ---------------------------------------------------------- spoken summary

def test_summary_is_speakable_and_mentions_each_agent():
    """This text is what the human HEARS at the approval gate, so it has to
    stay deterministic — no LLM between the plan and the spoken words."""
    s = render_plan_summary(good_plan())
    assert "2 agents" in s
    assert "Approve?" in s
    assert "Coupled" in s
    assert "api then web" in s


def test_summary_handles_single_agent_grammar():
    p = good_plan(agent_count=1, coupled=False, blackboard=False, contract={},
                  merge_order=["api"])
    p["workstreams"] = p["workstreams"][:1]
    s = render_plan_summary(p)
    assert "1 agent." in s and "1 agents" not in s


def test_summary_truncates_on_a_word_boundary():
    p = good_plan()
    p["workstreams"][0]["task"] = "Build " + "the booking subsystem " * 20
    s = render_plan_summary(p)
    assert "…" in s
    assert "  " not in s.replace("\n", " ")


# ------------------------------------------------------- plan file handoff

def test_missing_plan_file_times_out_rather_than_hanging(tmp_path):
    plan, status = read_plan_when_ready(tmp_path, 2, AGENTS, timeout_s=0.6)
    assert plan is None and status == "timeout"


def test_corrupt_plan_file_is_reported_not_executed(tmp_path):
    d = tmp_path / STATE_DIR
    d.mkdir(parents=True)
    (d / "plan.json").write_text("{ this is not json")
    plan, status = read_plan_when_ready(tmp_path, 2, AGENTS, timeout_s=5)
    assert plan is None and status == "unparseable"


def test_schema_violating_plan_file_is_rejected(tmp_path):
    d = tmp_path / STATE_DIR
    d.mkdir(parents=True)
    (d / "plan.json").write_text(json.dumps(good_plan(agent_count=7)))
    plan, status = read_plan_when_ready(tmp_path, 2, AGENTS, timeout_s=5)
    assert plan is None and status.startswith("invalid")


def test_valid_plan_file_is_accepted(tmp_path):
    d = tmp_path / STATE_DIR
    d.mkdir(parents=True)
    (d / "plan.json").write_text(json.dumps(good_plan()))
    plan, status = read_plan_when_ready(tmp_path, 2, AGENTS, timeout_s=5)
    assert status == "ok" and plan is not None
    assert plan["agent_count"] == 2
