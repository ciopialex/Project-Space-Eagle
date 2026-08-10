# Mission Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a spoken goal ("download a laptop stand from makerworld") into a sequence of small, individually-verified steps the eagle grinds through on its own — decomposing, escalating through strategies on failure, and asking a human only when it has genuinely run out of ways.

**Architecture:** A `Mission` holds the goal and its steps across turns. Each step carries a **strategy ladder** — the same tiering `GroundingResolver` already uses to *find* things, applied one level up to *do* them. A step completes only when the world is observed to have changed; that verification is what makes the next step safe to take without asking. Planning comes from the eagle itself, or from an outside agent handed a **context pack** describing what the eagle is and exactly what it can do.

**Tech Stack:** Python 3.12, existing `core/tool_result.py` contract, `actions/grounding/*` resolver + verifier, `core/capabilities.py` catalogue, pytest.

## Global Constraints

- **No mid-mission approval prompts.** A leader delegates and trusts. Reliability is bought with per-step verification, not with friction. The only thing that stops a mission is running out of strategies.
- **A step is complete only when re-observed.** `act_and_verify` semantics: act, then look again. Never mark a step done because a tool returned.
- **Never run the identical (strategy, target) twice.** The observed failure was `screen_click "Search bar"` run twice, 81 attempts each. Code enforces this; the prompt does not.
- **Never undo on failure.** Every checkpoint reached is valuable. On abandonment, write a status file and stop — do not roll back.
- **Missions survive a reconnect.** Gemini sessions `GoAway` mid-task; the log shows it happening during the MakerWorld attempt.
- **Every failure path returns `ToolResult`** with `ok` and `guidance`. No bare strings.
- **Delegation is context-rich.** An outside agent gets told what the eagle is, what it can do, and that every step must be executable through a GUI by keyboard/cursor/screen.

---

## File Structure

| File | Responsibility |
|---|---|
| `core/mission.py` (new) | `Step`, `Mission`, state transitions, the anti-thrash rules. No I/O, no tools — pure state so it is trivially testable. |
| `core/mission_ladder.py` (new) | The strategy ladder: given a step, the ordered list of ways to attempt it, and the executor that walks them. |
| `core/mission_handoff.py` (new) | The context pack — renders what the eagle is and can do, for an outside agent. |
| `core/mission_store.py` (new) | Persist/restore the active mission across reconnects and restarts. |
| `actions/mission.py` (new) | The tool boundary: `start` / `next` / `status` / `abandon`, returning `ToolResult`. |
| `main.py` (modify) | Declare the `mission` tool; dispatch it. |
| `core/prompt.txt` (modify) | Route goal-shaped requests to `mission`, not to a single tool call. |

---

## Task 1: Mission and Step state

**Files:**
- Create: `core/mission.py`
- Test: `tests/test_mission_state.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Step(intent, target=None, text=None, done=False, attempts=[])`, `Mission(goal, steps, cursor, status)`, `Mission.current() -> Step | None`, `Mission.record_attempt(strategy, ok, detail) -> None`, `Mission.tried(strategy) -> bool`, `Mission.advance() -> None`, `Mission.status` in `{"planning","running","blocked","done","abandoned"}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mission_state.py
from core.mission import Mission, Step


def test_a_mission_tracks_which_step_it_is_on():
    m = Mission(goal="download a laptop stand", steps=[
        Step(intent="open makerworld.com"),
        Step(intent="find the search box"),
    ])
    assert m.current().intent == "open makerworld.com"
    m.advance()
    assert m.current().intent == "find the search box"


def test_the_same_strategy_is_never_offered_twice_for_one_step():
    """The observed failure: screen_click 'Search bar' ran twice, 81 attempts
    each, identically. Code must refuse the repeat; the prompt did not."""
    m = Mission(goal="g", steps=[Step(intent="click the search box")])
    assert m.tried("screen_click") is False
    m.record_attempt("screen_click", ok=False, detail="not_found after 81 tries")
    assert m.tried("screen_click") is True


def test_a_failed_attempt_keeps_its_reason():
    m = Mission(goal="g", steps=[Step(intent="click X")])
    m.record_attempt("web_click", ok=False, detail="no control matches")
    assert "no control matches" in m.current().attempts[0].detail


def test_advancing_marks_the_step_done():
    m = Mission(goal="g", steps=[Step(intent="a"), Step(intent="b")])
    m.advance()
    assert m.steps[0].done is True


def test_a_mission_is_done_when_the_last_step_advances():
    m = Mission(goal="g", steps=[Step(intent="only")])
    m.advance()
    assert m.status == "done"
    assert m.current() is None


def test_a_mission_with_no_steps_is_not_running():
    assert Mission(goal="g", steps=[]).status in ("planning", "done")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mission_state.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.mission'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/mission.py
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Attempt:
    strategy: str
    ok: bool
    detail: str = ""


@dataclass
class Step:
    """One small thing, chosen so it can be VERIFIED after it is done.

    'find the search box' is a step. 'search makerworld' is not — there is no
    single observation that confirms it.
    """
    intent: str
    target: str = ""      # what to act on, in the words a person would use
    text: str = ""        # what to type, if anything
    done: bool = False
    attempts: list[Attempt] = field(default_factory=list)


@dataclass
class Mission:
    goal: str
    steps: list[Step] = field(default_factory=list)
    cursor: int = 0
    status: str = "running"

    def __post_init__(self) -> None:
        if not self.steps:
            self.status = "planning"

    def current(self) -> Step | None:
        if self.cursor >= len(self.steps):
            return None
        return self.steps[self.cursor]

    def tried(self, strategy: str) -> bool:
        step = self.current()
        return bool(step) and any(a.strategy == strategy for a in step.attempts)

    def record_attempt(self, strategy: str, ok: bool, detail: str = "") -> None:
        step = self.current()
        if step is not None:
            step.attempts.append(Attempt(strategy, ok, detail))

    def advance(self) -> None:
        step = self.current()
        if step is not None:
            step.done = True
            self.cursor += 1
        if self.current() is None:
            self.status = "done"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mission_state.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add core/mission.py tests/test_mission_state.py
git commit -m "feat(mission): the goal, its steps, and what has already been tried"
```

---

## Task 2: The strategy ladder

**Files:**
- Create: `core/mission_ladder.py`
- Test: `tests/test_mission_ladder.py`

**Interfaces:**
- Consumes: `Mission`, `Step` from Task 1.
- Produces: `strategies_for(step) -> list[str]`, `attempt(step, mission, runners) -> Outcome`, `Outcome(ok: bool, strategy: str, detail: str, exhausted: bool)`. `runners` is `dict[str, Callable[[Step], tuple[bool, str]]]` so tests never touch a browser.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mission_ladder.py
from core.mission import Mission, Step
from core.mission_ladder import attempt, strategies_for


def _m(intent="click the search box", target="the search box"):
    return Mission(goal="g", steps=[Step(intent=intent, target=target)])


def test_a_click_step_ladders_from_dom_to_screen_to_vision():
    s = strategies_for(Step(intent="click the search box", target="x"))
    assert s == ["web_click", "screen_click", "vision_click"]


def test_a_type_step_has_its_own_ladder():
    s = strategies_for(Step(intent="type laptop stand", target="x", text="laptop stand"))
    assert s[0] == "web_type"
    assert "keys" in s[-1] or "press" in s[-1]


def test_the_first_rung_that_works_wins_and_nothing_below_it_runs():
    ran = []
    runners = {
        "web_click": lambda st: (ran.append("web_click") or (True, "clicked")),
        "screen_click": lambda st: (ran.append("screen_click") or (True, "clicked")),
        "vision_click": lambda st: (ran.append("vision_click") or (True, "clicked")),
    }
    m = _m()
    out = attempt(m.current(), m, runners)
    assert out.ok is True and out.strategy == "web_click"
    assert ran == ["web_click"], ran


def test_a_failing_rung_escalates_to_the_next():
    ran = []
    runners = {
        "web_click": lambda st: (ran.append("w") or (False, "no DOM here")),
        "screen_click": lambda st: (ran.append("s") or (True, "clicked")),
        "vision_click": lambda st: (ran.append("v") or (True, "clicked")),
    }
    m = _m()
    out = attempt(m.current(), m, runners)
    assert out.ok is True and out.strategy == "screen_click"
    assert ran == ["w", "s"]


def test_a_rung_already_tried_is_not_run_again():
    """The exact observed bug, as a rule."""
    ran = []
    runners = {
        "web_click": lambda st: (ran.append("w") or (False, "nope")),
        "screen_click": lambda st: (ran.append("s") or (False, "not_found")),
        "vision_click": lambda st: (ran.append("v") or (False, "nope")),
    }
    m = _m()
    attempt(m.current(), m, runners)
    ran.clear()
    attempt(m.current(), m, runners)
    assert ran == [], f"re-ran strategies that had already failed: {ran}"


def test_exhausting_the_ladder_says_so_rather_than_looping():
    runners = {k: (lambda st: (False, "nope")) for k in
               ("web_click", "screen_click", "vision_click")}
    m = _m()
    out = attempt(m.current(), m, runners)
    assert out.ok is False and out.exhausted is True


def test_every_failure_reason_is_kept_for_the_handoff():
    runners = {
        "web_click": lambda st: (False, "no DOM"),
        "screen_click": lambda st: (False, "not in the a11y tree"),
        "vision_click": lambda st: (False, "429 rate limit"),
    }
    m = _m()
    attempt(m.current(), m, runners)
    detail = " ".join(a.detail for a in m.current().attempts)
    for reason in ("no DOM", "a11y tree", "429"):
        assert reason in detail
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mission_ladder.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.mission_ladder'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/mission_ladder.py
"""Escalate through ways of doing a step, cheapest and most exact first.

The same shape as `GroundingResolver`, which tiers AT-SPI -> vision to FIND a
control. This tiers ways to ACT on one. The ordering is not preference, it is
accuracy: the DOM knows exactly where a control is, the accessibility tree
knows exactly where it is when the app publishes one, and vision guesses -
measured live at 5.8s and ~650px off.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from core.mission import Mission, Step

_CLICK = ["web_click", "screen_click", "vision_click"]
_TYPE = ["web_type", "screen_type", "press_keys"]
_OPEN = ["web_open", "browser_open"]
_READ = ["web_look", "screen_look"]


@dataclass
class Outcome:
    ok: bool
    strategy: str = ""
    detail: str = ""
    exhausted: bool = False


def strategies_for(step: Step) -> list[str]:
    intent = (step.intent or "").lower()
    if step.text or intent.startswith("type"):
        return list(_TYPE)
    if intent.startswith("open") or intent.startswith("go to"):
        return list(_OPEN)
    if intent.startswith("read") or intent.startswith("look"):
        return list(_READ)
    return list(_CLICK)


def attempt(step: Step, mission: Mission,
            runners: dict[str, Callable[[Step], tuple[bool, str]]]) -> Outcome:
    """Walk the ladder until one rung works. Never re-runs a rung."""
    last = ""
    for strategy in strategies_for(step):
        if mission.tried(strategy):
            continue                    # already failed; trying again is thrash
        runner = runners.get(strategy)
        if runner is None:
            continue
        try:
            ok, detail = runner(step)
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}: {e}"
        mission.record_attempt(strategy, ok, detail)
        if ok:
            return Outcome(True, strategy, detail)
        last = detail
    return Outcome(False, "", last, exhausted=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mission_ladder.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add core/mission_ladder.py tests/test_mission_ladder.py
git commit -m "feat(mission): escalate through ways of doing a step, never repeat one"
```

---

## Task 3: The delegation context pack

**Files:**
- Create: `core/mission_handoff.py`
- Test: `tests/test_mission_handoff.py`

**Interfaces:**
- Consumes: `Mission`, `Step` from Task 1; `core.capabilities.CATALOGUE`.
- Produces: `context_pack(goal, mission=None) -> str`, `parse_plan(text) -> list[Step]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mission_handoff.py
from core.mission import Mission, Step
from core.mission_handoff import context_pack, parse_plan


def test_the_pack_says_what_the_eagle_IS():
    """An outside agent that does not know it is writing for a GUI operator
    writes 'run curl ...' — a plan the eagle cannot execute."""
    p = context_pack("download a laptop stand").lower()
    assert "keyboard" in p and ("cursor" in p or "mouse" in p) and "screen" in p


def test_the_pack_lists_real_capabilities_not_prose():
    p = context_pack("x")
    from core.capabilities import CATALOGUE
    assert any(c.id in p for c in CATALOGUE), "no capability ids in the pack"


def test_the_pack_demands_gui_sized_steps():
    p = context_pack("x").lower()
    assert "step" in p
    assert "one" in p or "single" in p


def test_the_pack_carries_the_goal_verbatim():
    assert "ship my landing page" in context_pack("ship my landing page")


def test_a_stuck_mission_hands_over_what_was_already_tried():
    m = Mission(goal="g", steps=[Step(intent="click the search box")])
    m.record_attempt("screen_click", ok=False, detail="not in the a11y tree")
    p = context_pack("g", m)
    assert "screen_click" in p and "a11y tree" in p
    assert "click the search box" in p


def test_a_returned_plan_becomes_steps():
    plan = """
    1. Open makerworld.com
    2. Click the search box
    3. Type laptop stand
    """
    steps = parse_plan(plan)
    assert [s.intent for s in steps] == [
        "Open makerworld.com", "Click the search box", "Type laptop stand"]


def test_a_plan_with_no_numbered_steps_yields_nothing_rather_than_garbage():
    assert parse_plan("I'm not sure how to do that, sorry.") == []


def test_bullet_points_are_accepted_too():
    steps = parse_plan("- Open the page\n- Click download")
    assert len(steps) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mission_handoff.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.mission_handoff'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/mission_handoff.py
"""Tell an outside agent what the eagle IS, before asking it for a plan.

An agent that does not know it is writing for something with a cursor and a
screen writes "run `curl -O ...`" — correct, and unexecutable. The capability
catalogue is already DATA (`core/capabilities.py`), so the handoff can state
the real surface rather than describing it in prose that drifts.
"""
from __future__ import annotations

import re

from core.capabilities import CATALOGUE
from core.mission import Mission, Step

_WHAT_I_AM = """\
You are writing a plan for AETHELARK, a human-emulation agent running on the
user's own computer. It is not a shell script and not a coding agent. It works
the way a person does: it looks at the SCREEN, moves the CURSOR, clicks, and
types on the KEYBOARD. It can also read a web page's structure directly when
the page is open in its own browser.

Write the plan so that agent can execute it. That means:

- ONE observable action per step. "Click the search box" is a step. "Search
  for a laptop stand" is not — it is three.
- Each step must be verifiable by looking at the screen afterwards.
- Name controls the way they appear on screen ("the Download button"), never
  by CSS selector, XPath or coordinates.
- Do not tell it to run shell commands unless the step is explicitly a
  terminal step.
- Prefer a direct URL over navigating from a home page when you know one.
- Number the steps. Nothing else in your reply is read.
"""


def _capability_lines(limit: int = 40) -> str:
    out = []
    for c in list(CATALOGUE)[:limit]:
        out.append(f"  - {c.id} ({c.tool}): {', '.join(c.says[:3])}")
    return "\n".join(out)


def context_pack(goal: str, mission: Mission | None = None) -> str:
    parts = [_WHAT_I_AM, "", "WHAT IT CAN DO:", _capability_lines(), "",
             f"THE GOAL, in the user's own words:\n  {goal}", ""]
    if mission is not None and mission.steps:
        parts.append("WHAT HAS ALREADY BEEN TRIED:")
        for s in mission.steps:
            mark = "done" if s.done else "current" if s is mission.current() else "pending"
            parts.append(f"  [{mark}] {s.intent}")
            for a in s.attempts:
                parts.append(f"      - {a.strategy}: "
                             f"{'ok' if a.ok else 'FAILED'} — {a.detail}")
        parts.append("")
        parts.append("Write a NEW numbered plan that gets from here to the goal. "
                     "Do not repeat a strategy that is listed as FAILED above.")
    return "\n".join(parts)


_NUMBERED = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+(.{3,200})$")


def parse_plan(text: str) -> list[Step]:
    """Numbered or bulleted lines become steps. Everything else is ignored —
    a reply with no steps yields none rather than a garbage plan."""
    steps: list[Step] = []
    for line in (text or "").splitlines():
        m = _NUMBERED.match(line)
        if m:
            steps.append(Step(intent=m.group(1).strip()))
    return steps
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mission_handoff.py -q`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add core/mission_handoff.py tests/test_mission_handoff.py
git commit -m "feat(mission): tell an outside agent what the eagle is before asking for a plan"
```

---

## Task 4: Persistence across reconnects

**Files:**
- Create: `core/mission_store.py`
- Test: `tests/test_mission_store.py`

**Interfaces:**
- Consumes: `Mission`, `Step`, `Attempt` from Task 1.
- Produces: `save(mission, path=None) -> Path`, `load(path=None) -> Mission | None`, `clear(path=None) -> None`, `report_path(mission) -> Path` (the stuck-status file).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mission_store.py
from core.mission import Mission, Step
from core.mission_store import clear, load, save, write_stuck_report


def test_a_mission_survives_a_round_trip(tmp_path):
    p = tmp_path / "m.json"
    m = Mission(goal="download a laptop stand",
                steps=[Step(intent="open makerworld"), Step(intent="click search")])
    m.record_attempt("web_click", ok=False, detail="no DOM")
    m.advance()
    save(m, p)
    back = load(p)
    assert back.goal == m.goal
    assert [s.intent for s in back.steps] == [s.intent for s in m.steps]
    assert back.cursor == 1
    assert back.steps[0].done is True
    assert back.steps[0].attempts[0].detail == "no DOM"


def test_loading_nothing_returns_none_rather_than_an_empty_mission(tmp_path):
    assert load(tmp_path / "absent.json") is None


def test_a_corrupt_file_does_not_raise(tmp_path):
    p = tmp_path / "m.json"
    p.write_text("{not json")
    assert load(p) is None


def test_clearing_removes_it(tmp_path):
    p = tmp_path / "m.json"
    save(Mission(goal="g", steps=[Step(intent="a")]), p)
    clear(p)
    assert load(p) is None


def test_a_stuck_mission_writes_a_report_naming_where_and_why(tmp_path):
    m = Mission(goal="print a laptop stand",
                steps=[Step(intent="open makerworld"), Step(intent="click download")])
    m.advance()
    m.record_attempt("web_click", ok=False, detail="no control matches")
    m.record_attempt("screen_click", ok=False, detail="not in the a11y tree")
    out = write_stuck_report(m, tmp_path / "stuck.md")
    text = out.read_text()
    assert "print a laptop stand" in text
    assert "click download" in text
    assert "a11y tree" in text
    assert "open makerworld" in text, "progress made must be recorded, not just the failure"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mission_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.mission_store'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/mission_store.py
"""Keep the mission across a reconnect, and leave a note when it gives up.

Gemini sessions GoAway mid-task — the real MakerWorld attempt was interrupted
by one. A mission held only in memory dies with the socket, and the user is
back to babysitting.

Nothing here undoes anything. Every checkpoint reached is worth keeping.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from core.mission import Attempt, Mission, Step


def _default_path() -> Path:
    from core import user_paths
    base = Path(user_paths.api_keys_path()).parent
    base.mkdir(parents=True, exist_ok=True)
    return base / "mission.json"


def save(mission: Mission, path: Path | None = None) -> Path:
    p = Path(path or _default_path())
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(mission), indent=2), encoding="utf-8")
    return p


def load(path: Path | None = None) -> Mission | None:
    p = Path(path or _default_path())
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    try:
        steps = [Step(**{**s, "attempts": [Attempt(**a) for a in s.get("attempts", [])]})
                 for s in raw.get("steps", [])]
        m = Mission(goal=raw["goal"], steps=steps,
                    cursor=raw.get("cursor", 0))
        m.status = raw.get("status", m.status)
        return m
    except Exception:
        return None


def clear(path: Path | None = None) -> None:
    try:
        Path(path or _default_path()).unlink()
    except Exception:
        pass


def write_stuck_report(mission: Mission, path: Path | None = None) -> Path:
    p = Path(path or (_default_path().parent / "mission-stuck.md"))
    lines = [f"# Mission abandoned — {datetime.now():%Y-%m-%d %H:%M}", "",
             f"**Goal:** {mission.goal}", "", "## Progress", ""]
    for s in mission.steps:
        mark = "x" if s.done else " "
        lines.append(f"- [{mark}] {s.intent}")
        for a in s.attempts:
            lines.append(f"  - {a.strategy}: {'ok' if a.ok else 'FAILED'} — {a.detail}")
    lines += ["", "Nothing was undone. Everything above that is ticked actually "
              "happened and is still in place."]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines), encoding="utf-8")
    return p
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mission_store.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add core/mission_store.py tests/test_mission_store.py
git commit -m "feat(mission): survive a reconnect, and leave a note when giving up"
```

---

## Task 5: The tool boundary

**Files:**
- Create: `actions/mission.py`
- Test: `tests/test_mission_tool.py`

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces: `mission(parameters: dict, player=None) -> ToolResult` with `action` in `{"start","next","status","abandon"}`. `start` needs `goal`; `next` takes none.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mission_tool.py
from core.tool_result import ToolResult
import actions.mission as M


def test_start_needs_a_goal():
    r = M.mission({"action": "start"})
    assert isinstance(r, ToolResult) and r.ok is False
    assert r.guidance


def test_start_plans_and_reports_the_steps(monkeypatch, tmp_path):
    monkeypatch.setattr(M, "_store_path", lambda: tmp_path / "m.json")
    monkeypatch.setattr(M, "_plan_locally",
                        lambda goal: ["Open makerworld.com", "Click the search box"])
    r = M.mission({"action": "start", "goal": "download a laptop stand"})
    assert r.ok is True
    assert "2" in r.message          # says how many steps
    assert "makerworld" in r.message.lower()


def test_next_without_a_mission_fails_rather_than_inventing_one(monkeypatch, tmp_path):
    monkeypatch.setattr(M, "_store_path", lambda: tmp_path / "m.json")
    r = M.mission({"action": "next"})
    assert r.ok is False and r.guidance


def test_status_reports_progress(monkeypatch, tmp_path):
    monkeypatch.setattr(M, "_store_path", lambda: tmp_path / "m.json")
    monkeypatch.setattr(M, "_plan_locally", lambda goal: ["a", "b", "c"])
    M.mission({"action": "start", "goal": "g"})
    r = M.mission({"action": "status"})
    assert r.ok is True and "3" in r.message


def test_abandon_writes_the_report_and_clears(monkeypatch, tmp_path):
    monkeypatch.setattr(M, "_store_path", lambda: tmp_path / "m.json")
    monkeypatch.setattr(M, "_report_path", lambda: tmp_path / "stuck.md")
    monkeypatch.setattr(M, "_plan_locally", lambda goal: ["a"])
    M.mission({"action": "start", "goal": "g"})
    r = M.mission({"action": "abandon"})
    assert r.ok is True
    assert (tmp_path / "stuck.md").exists()
    assert M.mission({"action": "next"}).ok is False


def test_an_unknown_action_is_refused_with_the_real_list():
    r = M.mission({"action": "teleport"})
    assert r.ok is False
    for verb in ("start", "next", "status", "abandon"):
        assert verb in r.message
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mission_tool.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'actions.mission'`

- [ ] **Step 3: Write minimal implementation**

Create `actions/mission.py` exposing `mission(parameters, player=None) -> ToolResult`, with module-level seams `_store_path()`, `_report_path()`, `_plan_locally(goal)` so tests never touch the real store or the network. `start` plans and saves; `next` loads, walks one step through `core.mission_ladder.attempt` with real runners, verifies, advances, saves, and reports; `status` summarises; `abandon` writes the stuck report and clears. Every return is a `ToolResult`; refusals carry `guidance`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mission_tool.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add actions/mission.py tests/test_mission_tool.py
git commit -m "feat(mission): start, next, status, abandon - on the tool contract"
```

---

## Task 6: Wire it in, and route goals to it

**Files:**
- Modify: `main.py` (TOOL_DECLARATIONS; the dispatch `elif` chain)
- Modify: `core/prompt.txt` (ROUTING PRECEDENCE)
- Test: `tests/test_mission_wired.py`

**Interfaces:**
- Consumes: `actions.mission.mission`.
- Produces: nothing new; the tool becomes reachable by the model.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mission_wired.py
import main


def _decl():
    return {d["name"]: d for d in main.TOOL_DECLARATIONS if isinstance(d, dict)}


def test_the_mission_tool_is_declared():
    assert "mission" in _decl()


def test_the_declaration_lists_every_action_the_tool_implements():
    import actions.mission as M
    desc = str(_decl()["mission"]["parameters"]["properties"]["action"])
    for verb in M._ACTIONS:
        assert verb in desc, f"{verb} is implemented but never advertised"


def test_the_prompt_routes_multi_step_goals_to_it():
    from pathlib import Path
    prompt = Path("core/prompt.txt").read_text().lower()
    assert "mission" in prompt


def test_the_declaration_says_it_is_for_multi_step_goals():
    d = _decl()["mission"]["description"].lower()
    assert "step" in d
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mission_wired.py -q`
Expected: FAIL — `KeyError: 'mission'`

- [ ] **Step 3: Write minimal implementation**

Add the declaration to `TOOL_DECLARATIONS`, a dispatch branch calling `mission(parameters=args, player=self.ui)`, and one ROUTING PRECEDENCE line in `core/prompt.txt`: anything that takes more than one action → `mission`, and after `start` keep calling `mission action='next'` until it reports done or blocked.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/ -q -p no:randomly`
Expected: PASS — full suite green, including `test_declared_actions_match` and `test_tool_routing_unambiguous`

- [ ] **Step 5: Commit**

```bash
git add main.py core/prompt.txt tests/test_mission_wired.py
git commit -m "feat(mission): route multi-step goals to the loop instead of a single tool call"
```

---

## Task 7: Acceptance — the real MakerWorld run

**Files:**
- Create: `tools/mission_smoke.py`

**Interfaces:**
- Consumes: `actions.mission.mission`.
- Produces: a script that runs the real goal end to end and prints each step's ladder outcome.

- [ ] **Step 1: Write the smoke script**

```python
# tools/mission_smoke.py
"""Run a real mission end to end and print what each step actually did.

Not a unit test — it drives a live browser. This is the only evidence that
counts: the unit tests prove the rules, this proves the eagle finishes.
"""
import sys
sys.path.insert(0, ".")
from actions.mission import mission

GOAL = sys.argv[1] if len(sys.argv) > 1 else \
    "go to makerworld.com and download a laptop stand"

print(mission({"action": "start", "goal": GOAL}).message)
for i in range(30):
    r = mission({"action": "next"})
    print(f"  [{i}] ok={r.ok} {r.message[:150]}")
    if not r.ok or "done" in r.message.lower():
        break
print(mission({"action": "status"}).message)
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/python tools/mission_smoke.py`
Expected: it gets further than the babysat attempt did, unattended. Record where it stops.

- [ ] **Step 3: Write down where it stopped**

Add the result to `docs/Aethelark_Roadmap.md` §2 — the step it reached, the ladder rungs tried, and the reason. That is the next task's spec.

- [ ] **Step 4: Commit**

```bash
git add tools/mission_smoke.py docs/Aethelark_Roadmap.md
git commit -m "test(mission): the real makerworld run, and where it actually stops"
```

---

## Deliberately not in this plan

- **Mid-mission replanning.** A blocked step delegates once (Task 3's pack) and then abandons with a report. Loops that replan themselves need a stop condition nobody has designed yet.
- **The swarm case.** "Ship my landing page" — where the eagle spawns coding agents and works alongside them on a blackboard — reuses `swarm_orchestrator` and belongs in its own plan once the single-agent loop is proven.
- **Blueprint research.** Looking up a documented path before planning is the highest-value addition, and it plugs straight into `_plan_locally`. Deliberately after the loop works, so there is something to plug it into.
- **Native apps (the slicer).** The ladder's rungs are web and screen. A native-app rung is additive once `strategies_for` has proven itself.
