"""Tell an outside agent what the eagle IS, before asking it for a plan.

The failure this exists to prevent is specific and near-certain. Ask any
capable agent "how do I download a laptop stand from MakerWorld" and it
answers with `curl` — correct, useful to a human at a terminal, and entirely
unexecutable by something whose hands are a cursor and a keyboard. The agent
is not wrong; it was never told who it was writing for.

So the pack states three things: what the eagle is, what it can actually do
(read out of `core/capabilities.py`, which is already DATA, so this cannot
drift into a stale prose description), and — when the eagle is stuck — every
approach that has already been ruled out, so the reply is a *different* plan
rather than the same one again.

Everything from the moment the request reaches the outside agent to the
moment the goal is met is autonomous. That is why the plan has to be
executable on the first read: nobody is going to be there to interpret it.
"""
from __future__ import annotations

import re

from core.capabilities import CATALOGUE
from core.mission import Mission, Step

#: A plan longer than this is a hallucination rather than a plan, and running
#: it would be forty minutes of the eagle grinding through invented steps.
MAX_STEPS = 40

_WHAT_I_AM = """\
You are writing a plan for AETHELARK — a human-emulation agent running on the
user's own computer. It is not a shell script, not a scraper, and not a coding
agent. It works the way a person does: it LOOKS at the screen, moves the
CURSOR, CLICKS, and types on the KEYBOARD. When a page is open in its own
browser it can also read that page's structure directly.

Write the plan so that agent can execute it, unattended, on the first read.
That means:

- ONE observable action per step. "Click the search box" is a step. "Search
  for a laptop stand" is not — that is three steps wearing one sentence.
- Every step must be checkable by looking at the screen afterwards. If there
  is no way to see whether a step worked, split it until there is.
- Name controls the way they APPEAR ON SCREEN — "the Download button", "the
  search box". Never a CSS selector, never XPath, never coordinates.
- Do NOT tell it to run shell commands, curl, wget or a package manager
  unless the step is explicitly about a terminal. It has hands, not a shell.
- Prefer a direct URL over navigating from a home page whenever you know one.
  Landing closer to the goal is fewer steps and fewer ways to go wrong.
- Assume nothing is already open or signed in unless told so below.
- NUMBER the steps. Nothing else in your reply is read.
"""


def _capability_lines(limit: int = 40) -> str:
    out = []
    for c in list(CATALOGUE)[:limit]:
        says = ", ".join(c.says[:3]) if getattr(c, "says", None) else ""
        out.append(f"  - {c.id} (via {c.tool}): {says}")
    return "\n".join(out)


def context_pack(goal: str, mission: Mission | None = None) -> str:
    """Everything the outside agent needs, and nothing it has to infer."""
    parts = [
        _WHAT_I_AM, "",
        "WHAT IT CAN DO (its real tool surface, not a description of one):",
        _capability_lines(), "",
        "THE GOAL, in the user's own words:",
        f"  {goal}", "",
    ]

    if mission is not None and mission.steps:
        done, total = mission.progress()
        parts += [f"WHERE IT HAS GOT TO — {done} of {total} steps done:", ""]
        for s in mission.steps:
            if s.done:
                mark = "done"
            elif s is mission.current():
                mark = "STUCK HERE"
            else:
                mark = "not reached"
            parts.append(f"  [{mark}] {s.intent}")
            for a in s.attempts:
                parts.append(f"       {a.strategy}: "
                             f"{'ok' if a.ok else 'FAILED'} — {a.detail}")
        parts += [
            "",
            "Write a NEW numbered plan that gets from where it is now to the "
            "goal. Everything marked done has ALREADY HAPPENED — do not plan "
            "it again. Do not suggest any approach listed above as FAILED; it "
            "has been tried and it does not work here.",
        ]

    return "\n".join(parts)


_STEP = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+(.{3,200}?)\s*$")
_URL = re.compile(r"https?://[^\s'\"<>)]+")
_QUOTED = re.compile(r"[\"'“”‘’]([^\"'“”‘’]{1,120})[\"'“”‘’]")


def parse_plan(text: str) -> list[Step]:
    """Numbered or bulleted lines become steps; everything else is ignored.

    A reply with no steps yields NO steps. An apology or a refusal must not
    become a one-step mission that the eagle then earnestly tries to execute —
    that is the same defect as a failed tool reporting success.
    """
    steps: list[Step] = []
    for line in (text or "").splitlines():
        m = _STEP.match(line)
        if not m:
            continue
        intent = m.group(1).strip()
        url = (_URL.search(intent) or [""])[0] if _URL.search(intent) else ""
        quoted = _QUOTED.search(intent)
        # Only treat quoted text as typing input when the step is about typing;
        # "Click the 'Download' button" quotes a control, not a payload.
        is_typing = intent.lower().startswith(
            ("type", "write", "enter", "fill", "search for"))
        steps.append(Step(
            intent=intent,
            url=url,
            text=quoted.group(1) if (quoted and is_typing) else "",
        ))
        if len(steps) >= MAX_STEPS:
            break
    return steps
