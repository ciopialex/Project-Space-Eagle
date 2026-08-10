"""Break a spoken goal into steps small enough to grind through.

The eagle plans for itself here. The same prompt discipline as the outside-
agent handoff, for the same reason: a plan written without knowing it is for
something with a cursor and a screen comes back as `curl -O ...` — correct,
and unexecutable.

The one rule with teeth: **a planner that cannot plan returns NO steps.** An
apology, a refusal, or a wall of prose must not become a mission, because a
mission that starts wrong is worse than one that never starts — it acts.
That is the same defect as a failed tool reporting success, moved one layer
up, and it is the one worth being strict about.

`core/mission_handoff.context_pack` plugs in at this seam for the other two
cases the user described: ask an outside agent for the plan, or hand the goal
to a swarm and work alongside it.
"""
from __future__ import annotations

from core.mission import Step
from core.mission_handoff import parse_plan

_PROMPT = """\
Break this goal into the smallest steps a person would actually take, working
at a computer with a MOUSE, a KEYBOARD and a SCREEN. Nothing else.

Rules:
- ONE observable action per step. "Click the search box" is a step. "Search
  for a laptop stand" is three.
- Each step must be checkable by looking at the screen afterwards.
- Name controls as they APPEAR — "the Download button", "the search box".
  Never a CSS selector, never XPath, never coordinates.
- No shell commands, no curl, no package managers. It has hands, not a shell.
- Prefer a direct URL over navigating from a home page when you know one.
- Between 3 and 15 steps. If the goal genuinely needs one action, give one.
- If you cannot break it down, reply exactly: CANNOT_PLAN

Number every step. Nothing else in your reply is read.

GOAL: {goal}
"""


def _ask(prompt: str) -> str:
    """One text completion. Split out so tests never reach the network."""
    import json

    from google import genai

    from core import user_paths
    key = json.loads(
        open(user_paths.api_keys_path(), encoding="utf-8").read()
    )["gemini_api_key"]
    client = genai.Client(api_key=key)
    resp = client.models.generate_content(
        model="gemini-2.5-flash", contents=prompt)
    return (getattr(resp, "text", "") or "").strip()


#: Why the last plan came back empty, or "" if the model simply declined.
#: Same distinction the vision grounder needed: "could not look" and "not
#: there" are different answers, and collapsing them makes the eagle give up
#: on something that would work in forty seconds.
last_error: str = ""


def _is_quota(detail: str) -> bool:
    d = detail.lower()
    return "429" in d or "resource_exhausted" in d or "quota" in d


def plan(goal: str) -> list[Step]:
    """Steps for `goal`, or an empty list.

    Never raises. An empty list is a real answer — the caller turns it into
    "could not break this down", which is honest, rather than into a
    one-step mission built out of an apology.
    """
    global last_error
    last_error = ""
    try:
        reply = _ask(_PROMPT.format(goal=goal))
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        last_error = ("the brain is rate-limited" if _is_quota(detail)
                      else detail)
        print(f"[Mission] planning failed: {detail[:160]}")
        return []
    if "CANNOT_PLAN" in (reply or "").upper():
        return []
    return parse_plan(reply)
