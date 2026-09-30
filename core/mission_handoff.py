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

#: A person writes "go to makerworld.com", not "https://makerworld.com". The
#: model does too, and the first mission to reach step 1 died on exactly that:
#: the scheme-only pattern captured nothing and both rungs got an empty url.
#:
#: Restricted to a known suffix list rather than "anything with a dot",
#: because steps are full of dots that are not domains — "Download the STL
#: files.", "laptop_stand.stl", "0.2mm". Guessing wrong here does not fail
#: safely: it sends the browser somewhere real.
_TLDS = ("com", "org", "net", "io", "dev", "app", "co", "ai", "me", "info",
         "edu", "gov", "uk", "de", "fr", "ro", "nl", "eu", "ca", "us", "shop",
         "store", "cloud", "xyz", "tv", "gg", "so", "sh", "to")
_BARE = re.compile(
    r"\b((?:www\.)?[a-z0-9][a-z0-9-]*(?:\.[a-z0-9][a-z0-9-]*)*\.(?:"
    + "|".join(_TLDS) + r"))(/[^\s'\"<>)]*)?\b", re.I)


def _url_in(text: str) -> str:
    """The address a step refers to, with a scheme, or "".

    Mirrors `browser_control._normalize_url`, which has done this for months —
    the mission parser simply never used it.
    """
    full = _URL.search(text or "")
    if full:
        return full.group(0)
    bare = _BARE.search(text or "")
    if not bare:
        return ""
    host = bare.group(1)
    path = bare.group(2) or ""
    return f"https://{host}{path}"
_QUOTED = re.compile(r"[\"'“”‘’]([^\"'“”‘’]{1,120})[\"'“”‘’]")

#: Verbs that mean "put this text somewhere", and the words that end the text
#: and begin the destination. "Type motherboard" has no quotes at all, which
#: is how a person writes it — and the first mission to reach a typing step
#: died with "nothing to type" because only quoted text was captured.
_TYPE_VERB = re.compile(
    r"^(?:type|write|enter|fill in|fill|search for|input)\s+(.+)$", re.I)
_DESTINATION = re.compile(
    r"\s+(?:into|in to|in|on|to|within|inside)\s+the\s+.+$", re.I)


def _typed_text(intent: str) -> str:
    """What this step puts into a field, or "".

    Quotes win when present, because they are unambiguous. Otherwise take what
    follows the verb, minus any trailing destination — "Type laptop stand into
    the search box" types "laptop stand", not the whole sentence.
    """
    quoted = _QUOTED.search(intent or "")
    if quoted:
        return quoted.group(1).strip()
    m = _TYPE_VERB.match((intent or "").strip())
    if not m:
        return ""
    text = _DESTINATION.sub("", m.group(1)).strip().rstrip(".,;:")
    return text


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
        url = _url_in(intent)
        # Only a typing step has a payload; "Click the 'Download' button"
        # quotes a control, not something to type.
        is_typing = bool(_TYPE_VERB.match(intent))
        steps.append(Step(
            intent=intent,
            url=url,
            text=_typed_text(intent) if is_typing else "",
        ))
        if len(steps) >= MAX_STEPS:
            break
    return steps
