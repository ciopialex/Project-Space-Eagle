"""Ways of doing one step, cheapest and most exact first.

The same shape as `GroundingResolver`, which tiers AT-SPI then vision to FIND
a control. This tiers ways to ACT on one, and the ordering is accuracy rather
than preference:

  the DOM          knows exactly where a control is, in the eagle's own browser
  the a11y tree    knows exactly where it is when the app publishes one -
                   Chrome publishes NOTHING without --force-renderer-
                   accessibility, which is why screen_click was blind on
                   MakerWorld while the search bar sat in plain sight
  vision           guesses. Measured live: 5808ms, and ~650px off the target

Two rules, both learned from the log rather than from theory:

1. **A rung that failed is never offered again for the same step.** The eagle
   ran the identical `screen_click "Search bar"` twice, 81 attempts each, ten
   seconds total, to prove the same thing twice.
2. **Running out of rungs is an outcome, not a loop.** `exhausted` is what
   turns "I cannot" into "delegate this, or ask the human" — with every reason
   already collected for the handoff.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from core.mission import Mission, Step

#: Click: DOM, then the accessibility tree, then pixels.
_CLICK = ["web_click", "user_click", "screen_click", "vision_click"]

#: Type: the same, ending in single keypresses. That last rung is what the
#: user had to drive by voice, letter by letter — it belongs in the ladder so
#: nobody has to.
_TYPE = ["web_type", "user_type", "screen_type", "press_keys"]

#: Open: the eagle's OWN browser first. `browser_control` opens the user's
#: Chrome, which `web_agency` cannot see into — prompt.txt warns about this,
#: and the MakerWorld run fell into it anyway after the bot wall.
_OPEN = ["web_open", "browser_open", "user_open"]

#: Read: never clicks anything.
_READ = ["web_look", "user_look", "screen_look"]

_LADDERS = {"click": _CLICK, "type": _TYPE, "open": _OPEN, "read": _READ}


@dataclass
class Outcome:
    ok: bool
    strategy: str = ""
    detail: str = ""
    exhausted: bool = False
    #: True/False when the world was observed before and after, None when it
    #: was not looked at. `ok` means the CALL worked; this means something
    #: actually happened. They are different, and every bug this codebase has
    #: fought for weeks lives in the gap between them.
    moved: bool | None = None


#: Words that join two actions into one sentence. A step containing one is a
#: plan that was never decomposed — and the ladder will happily run its FIRST
#: verb and call the whole thing done, which is exactly what happened live.
_JOINERS = (" then ", " and then ", " after that ", " next ", " followed by ",
            " , then ")

#: Verbs that start an action. Two of them in one step is two steps.
_ACTION_WORDS = ("open ", "go to ", "navigate ", "click ", "press ", "type ",
                 "write ", "enter ", "fill ", "search ", "download ",
                 "upload ", "submit ", "select ", "read ", "save ")


def is_compound(intent: str) -> bool:
    """Is this several actions wearing one step?

    Two signals, because either alone is wrong. A joining word ("then",
    "after that") is explicit. Failing that, three or more action verbs in one
    sentence is not a step — two is tolerated, because "click the Download
    button" contains "click" and "download" and is perfectly single.
    """
    low = f" {(intent or '').lower().strip()} "
    if any(j in low for j in _JOINERS):
        return True
    return sum(1 for v in _ACTION_WORDS if v in low) >= 3


def kind_of(step: Step) -> str:
    """Which ladder this step belongs on, from how a person phrased it."""
    intent = (step.intent or "").lower().strip()
    if step.text or intent.startswith(("type", "write", "enter", "fill")):
        return "type"
    if step.url or intent.startswith(("open", "go to", "navigate", "visit")):
        return "open"
    if intent.startswith(("read", "look", "check", "find", "see")):
        return "read"
    return "click"


def strategies_for(step: Step) -> list[str]:
    return list(_LADDERS[kind_of(step)])


def attempt(step: Step, mission: Mission,
            runners: dict[str, Callable[[Step], tuple[bool, str]]],
            observe: Callable[[], object] | None = None) -> Outcome:
    """Walk the ladder until a rung works, and check the world moved.

    `runners` is injected so the rules can be tested without a browser, a
    screen or a network. A rung with no runner is SKIPPED rather than counted
    as a failure — an unimplemented strategy must not burn the step's budget
    or pollute the handoff with a failure that never happened.

    `observe` fingerprints the current page. Optional, and absent by default,
    so every existing caller behaves exactly as before. When present, a rung
    that reports success while nothing changed is still reported as `ok` — it
    DID work — but carries `moved=False`, because "the call returned" and
    "something happened" are different claims and only one of them is what the
    user asked for.

    Steps that legitimately change nothing (reading a page, opening one
    already open) are exempt: flagging those would make the signal useless.
    """
    from core.mission_ladder import kind_of as _kind          # self, for clarity
    expects_movement = _kind(step) not in ("read",)
    before = None
    if observe is not None:
        try:
            before = observe()
        except Exception:
            before = None

    last = ""
    for strategy in strategies_for(step):
        if mission.tried(strategy):
            continue                  # already failed here; retrying is thrash
        runner = runners.get(strategy)
        if runner is None:
            continue                  # not implemented — not a failure
        try:
            ok, detail = runner(step)
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}: {e}"
        detail = str(detail)
        moved = None
        if ok and observe is not None and before is not None:
            moved, note = _moved(before, observe, expects_movement)
            if note:
                detail = f"{detail} — {note}"
        mission.record_attempt(strategy, ok, detail)
        if ok:
            return Outcome(True, strategy, detail, moved=moved)
        last = detail
    return Outcome(False, "", last, exhausted=True)


def _moved(before, observe, expects_movement) -> tuple[bool | None, str]:
    """Did the page change, and is that worth saying?

    Returns `(moved, note)`. `moved` is None when the world could not be read
    — never False, because a failed look is not a still world.
    """
    try:
        after = observe()
    except Exception:
        return None, ""
    if after is None:
        return None, ""
    try:
        from core.world_state import describe_change
        if getattr(before, "unknown", False) or getattr(after, "unknown", False):
            return None, "could not read the page to confirm"
        same = before.same_as(after)
    except Exception:
        return None, ""
    if not same:
        return True, ""
    if not expects_movement:
        return True, ""          # reading changes nothing, and should not
    return False, ("the call worked but nothing on the page changed — "
                   "do not assume it took effect")
