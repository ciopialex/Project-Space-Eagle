"""The brain is already thinking about this. Don't pay twice.

From a real voice session, three turns in a row:

    [Tool] ▶ mission {goal=download a laptop stand from makerworld, action=start}
    [Mission] planning failed: 429 RESOURCE_EXHAUSTED
      limit: 20, model: gemini-2.5-flash
      quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier
    ...
    [Aethelark] [Trace] turn=1 to_action=10726ms

Two separate mistakes, both mine.

**Paying twice.** The voice model was already mid-conversation about "download
a laptop stand from makerworld". Making a SECOND `generate_content` call to
break that into steps spends one of a *daily* budget of twenty — shared with
vision grounding, video summarising, code_helper and desktop tasks — and cost
10.7 seconds before the tool even started. The model that is already talking
can emit the steps in the same breath, for nothing.

**Inviting the retry storm.** The failure guidance said "offer to retry", so
the model retried at once, three turns running, burning three of the twenty on
an error that clears on a clock. Guidance is not decoration; the model does
what it says.

So `steps` becomes the primary input and the extra call is the fallback.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actions.mission as M  # noqa: E402


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "_store_path", lambda: tmp_path / "m.json")
    monkeypatch.setattr(M, "_report_path", lambda: tmp_path / "stuck.md")
    return tmp_path


def _never_called(monkeypatch):
    calls = []
    monkeypatch.setattr(M, "_plan_locally",
                        lambda goal: calls.append(goal) or [])
    return calls


# ── steps supplied by the caller ────────────────────────────────────────────

def test_steps_from_the_model_are_used_without_a_second_api_call(monkeypatch):
    calls = _never_called(monkeypatch)
    r = M.mission({"action": "start",
                   "goal": "download a laptop stand from makerworld",
                   "steps": ["Go to makerworld.com",
                             "Click the search box",
                             "Type laptop stand"]})
    assert r.ok is True
    assert calls == [], "made a second model call when the steps were handed to it"
    assert "3" in r.message


def test_steps_as_a_newline_string_are_accepted(monkeypatch):
    """A function-call arg often arrives as one string, not a list."""
    _never_called(monkeypatch)
    r = M.mission({"action": "start", "goal": "g",
                   "steps": "1. Go to makerworld.com\n2. Click the search box"})
    assert r.ok is True and "2" in r.message


def test_a_url_in_a_supplied_step_is_captured(monkeypatch):
    _never_called(monkeypatch)
    M.mission({"action": "start", "goal": "g",
               "steps": ["Go to https://makerworld.com/en"]})
    from core import mission_store as store
    m = store.load(M._store_path())
    assert m.steps[0].url == "https://makerworld.com/en"


def test_empty_supplied_steps_fall_back_to_planning(monkeypatch):
    calls = []
    monkeypatch.setattr(M, "_plan_locally",
                        lambda goal: calls.append(goal) or ["a step"])
    r = M.mission({"action": "start", "goal": "g", "steps": []})
    assert r.ok is True
    assert calls == ["g"], "did not fall back when given no steps"


def test_no_steps_key_at_all_still_falls_back(monkeypatch):
    calls = []
    monkeypatch.setattr(M, "_plan_locally",
                        lambda goal: calls.append(goal) or ["a step"])
    M.mission({"action": "start", "goal": "g"})
    assert calls == ["g"]


# ── the retry storm ─────────────────────────────────────────────────────────

def test_a_rate_limited_failure_tells_the_model_NOT_to_retry_now(monkeypatch):
    """The guidance caused this. Three turns, three wasted requests."""
    import core.mission_planner as P
    monkeypatch.setattr(M, "_plan_locally", lambda goal: [])
    monkeypatch.setattr(P, "last_error", "the brain is rate-limited")
    r = M.mission({"action": "start", "goal": "g"})
    assert r.ok is False
    g = r.guidance.lower()
    assert "do not" in g or "don't" in g
    assert "retry" in g
    assert "impossible" in g, "must still say the goal is not impossible"


def test_the_rate_limit_guidance_does_not_say_offer_to_retry(monkeypatch):
    import core.mission_planner as P
    monkeypatch.setattr(M, "_plan_locally", lambda goal: [])
    monkeypatch.setattr(P, "last_error", "the brain is rate-limited")
    g = M.mission({"action": "start", "goal": "g"}).guidance.lower()
    assert "offer to retry" not in g


def test_the_model_is_told_it_can_supply_the_steps_itself(monkeypatch):
    """If it does not know, it will keep paying for the second call."""
    import main
    d = {x["name"]: x for x in main.TOOL_DECLARATIONS if isinstance(x, dict)}
    props = d["mission"]["parameters"]["properties"]
    assert "steps" in props
    desc = str(props["steps"]).lower()
    assert "step" in desc
