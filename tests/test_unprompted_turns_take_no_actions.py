"""A turn the user did not ask for may not act on the world.

The user reported pages spawning every few tens of seconds — opening, doing
nothing, closing — long after the mission that started them had failed. His
hypothesis was right: something kept the loop alive.

Two background tasks wake the model on a timer. The system monitor every 10
seconds when a metric crosses a threshold, and proactive mode every 60. Each
sends content into the live session, and a woken model does what a model does:
looks at its context, sees a mission mid-flight, and calls a tool. A browser
opens, the step fails or completes, `_release_browsers` closes it, and a
minute later it happens again.

`core/prompt.txt` already says "Do NOT call any tools during a proactive
check". That is soft law — prose the model may talk itself out of, and did.
This is the dispatch-layer version, which it cannot route around: belt and
suspenders, exactly as the governance model describes.

The second half of the same loop: `mission next` only refused a mission that
was DONE, so a BLOCKED one re-ran its ladder on every nudge. Every rung had
already been tried, so it exhausted immediately and blocked again — but it
still cost a turn, and any open rung along the way still spawned.
"""
from __future__ import annotations

import sys
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actions.mission as M  # noqa: E402


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "_store_path", lambda: tmp_path / "m.json")
    monkeypatch.setattr(M, "_report_path", lambda: tmp_path / "s.md")
    monkeypatch.setattr(M, "_observe", lambda: None)


# ── a blocked mission stays blocked ─────────────────────────────────────────

def test_next_on_a_blocked_mission_refuses_without_running_anything(monkeypatch):
    ran = []
    monkeypatch.setattr(M, "_runners", lambda: {
        "web_open": lambda s: ran.append("open") or (False, "nope")})
    M.mission({"action": "start", "goal": "g", "steps": ["Open a.test", "b"]})
    M.mission({"action": "next"})               # blocks
    ran.clear()

    r = M.mission({"action": "next"})
    assert r.ok is False
    assert ran == [], f"re-ran the ladder on a dead mission: {ran}"
    assert "blocked" in (r.message + r.guidance).lower()


def test_the_refusal_says_what_would_actually_help(monkeypatch):
    monkeypatch.setattr(M, "_runners", lambda: {})
    M.mission({"action": "start", "goal": "g", "steps": ["a"]})
    M.mission({"action": "next"})
    g = M.mission({"action": "next"}).guidance.lower()
    assert "start" in g or "abandon" in g


def test_a_running_mission_is_unaffected(monkeypatch):
    monkeypatch.setattr(M, "_runners", lambda: {"web_click": lambda s: (True, "ok")})
    M.mission({"action": "start", "goal": "g", "steps": ["a", "b"]})
    assert M.mission({"action": "next"}).ok is True
    assert M.mission({"action": "next"}).ok is True


# ── an unprompted turn cannot act ───────────────────────────────────────────

def _dispatcher(unprompted: bool):
    """A live dispatcher with no Qt, no session and no network.

    Built with __new__ on purpose: the constructor opens audio devices and a
    Gemini client, and neither is needed to ask the one question here.
    """
    import main

    live = main.AethelarkLive.__new__(main.AethelarkLive)
    live._unprompted_turn = unprompted
    live._turn_epoch = 0
    live._inflight_by_args = {}
    live.ui = SimpleNamespace(muted=True, set_state=lambda *_: None)
    return live


class _Call:
    def __init__(self, name, args=None):
        self.id, self.name, self.args = "call-1", name, args or {}


def test_the_dispatcher_refuses_tools_on_an_unprompted_turn():
    """Drive the guard, do not read it.

    This replaced a regex over main.py asserting that the string
    "_unprompted_turn" appeared inside _execute_tool. That assertion passed
    whether the check was right or INVERTED, and it failed on a rename that
    broke nothing. It could not have caught a real bug in either direction.

    A background wake-up must not be able to touch anything. The system
    monitor fires every 10s and the proactive check every 60, and a woken
    model looks at its context, sees a mission mid-flight and calls a tool.
    That is how pages kept spawning long after the mission had died.
    """
    live = _dispatcher(unprompted=True)
    reply = asyncio.run(live._execute_tool(_Call("desktop_control", {"action": "click"})))

    assert reply.response.get("ok") is False, (
        "a turn nobody asked for was allowed to run a tool that touches the "
        "desktop")
    assert "background check" in reply.response.get("result", ""), reply.response


def test_a_turn_the_user_asked_for_is_not_refused():
    """The half a mirror can never check: that the guard is not backwards.

    `if unprompted: refuse` and `if not unprompted: refuse` contain the same
    identifiers, so reading the source cannot tell them apart. Running it can.
    """
    live = _dispatcher(unprompted=False)
    reply = asyncio.run(live._execute_tool(_Call("definitely_not_a_tool", {})))

    # Unknown, so it fails — but for being unknown, never for being unasked.
    assert "background check" not in reply.response.get("result", ""), (
        "a turn the user DID ask for was refused as unprompted; the guard is "
        "inverted")


def test_the_prompt_still_asks_as_well():
    """Belt AND suspenders — the prompt keeps the model from trying, the
    dispatcher stops it when it does anyway."""
    p = (Path(__file__).resolve().parent.parent / "core" / "prompt.txt").read_text()
    assert "PROACTIVE_CHECK" in p
    assert "not call any tools" in p.lower() or "do not call" in p.lower()
