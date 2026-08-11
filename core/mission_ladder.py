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
            runners: dict[str, Callable[[Step], tuple[bool, str]]]) -> Outcome:
    """Walk the ladder until a rung works.

    `runners` is injected so the rules can be tested without a browser, a
    screen or a network. A rung with no runner is SKIPPED rather than counted
    as a failure — an unimplemented strategy must not burn the step's budget
    or pollute the handoff with a failure that never happened.
    """
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
        mission.record_attempt(strategy, ok, str(detail))
        if ok:
            return Outcome(True, strategy, str(detail))
        last = str(detail)
    return Outcome(False, "", last, exhausted=True)
