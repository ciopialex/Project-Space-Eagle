"""Turning a spoken goal into steps small enough to grind through.

The eagle plans it itself when it can. The context pack for an outside agent
already exists (`core/mission_handoff.py`) and plugs in at exactly this seam
when it cannot — the user's three cases: do it alone, ask an outside tool for
a plan then do it, or spawn a swarm and work alongside them.

What must never happen is the failure this codebase keeps producing in other
forms: a planner that cannot plan returning something that LOOKS like a plan.
An apology, a refusal, or a wall of prose must yield zero steps, so the
mission fails to start rather than starting wrong.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.mission_planner as P  # noqa: E402


def _model(reply):
    return lambda prompt: reply


def test_a_numbered_reply_becomes_steps(monkeypatch):
    monkeypatch.setattr(P, "_ask", _model(
        "1. Open makerworld.com\n2. Click the search box\n3. Type laptop stand"))
    steps = P.plan("download a laptop stand")
    assert [s.intent for s in steps][:3] == [
        "Open makerworld.com", "Click the search box", "Type laptop stand"]


def test_an_apology_yields_no_steps_rather_than_a_fake_plan(monkeypatch):
    monkeypatch.setattr(P, "_ask", _model("I'm sorry, I can't help with that."))
    assert P.plan("something") == []


def test_a_model_that_raises_yields_no_steps_rather_than_exploding(monkeypatch):
    def boom(prompt):
        raise RuntimeError("429 RESOURCE_EXHAUSTED")
    monkeypatch.setattr(P, "_ask", boom)
    assert P.plan("something") == []


def test_the_prompt_tells_the_model_it_is_planning_for_a_gui_operator(monkeypatch):
    seen = {}
    monkeypatch.setattr(P, "_ask", lambda prompt: seen.setdefault("p", prompt) or "1. a")
    P.plan("download a laptop stand")
    p = seen["p"].lower()
    assert "cursor" in p or "mouse" in p
    assert "keyboard" in p
    assert "one" in p and "step" in p


def test_the_goal_reaches_the_model_verbatim(monkeypatch):
    seen = {}
    monkeypatch.setattr(P, "_ask", lambda prompt: seen.setdefault("p", prompt) or "1. a")
    P.plan("ship my nail salon landing page")
    assert "ship my nail salon landing page" in seen["p"]


def test_a_url_in_a_step_survives_into_the_step(monkeypatch):
    monkeypatch.setattr(P, "_ask", _model("1. Go to https://makerworld.com/en"))
    assert P.plan("g")[0].url == "https://makerworld.com/en"


def test_an_absurd_plan_is_truncated(monkeypatch):
    monkeypatch.setattr(P, "_ask",
                        _model("\n".join(f"{i}. Step {i}" for i in range(1, 200))))
    assert len(P.plan("g")) <= 40
