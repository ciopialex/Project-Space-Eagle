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

import re

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

#: The disk. A workflow that only ever touches a browser cannot do the thing
#: the user actually asked for — download a form, read details off the Desktop,
#: fill it in, send it back. Before these existed, "Read the file
#: my-details.txt on the Desktop" fell to the READ ladder, `web_look` read the
#: web page instead, and the step reported SUCCESS having never touched the
#: disk. A wrong answer delivered confidently is worse than a refusal.
_FILE_READ = ["file_read"]
_FILE_WRITE = ["file_fill", "file_write"]

#: Handing a file to a page. Distinct from click for the same reason download
#: is: the success condition is "the input holds the file", not "the click
#: landed".
_UPLOAD = ["web_upload"]

#: Getting a file FROM a page. Its own ladder for the reason documented on
#: `web_agency._download`: a click succeeds when the page reacts, a download
#: succeeds only when a file is on disk. Before this existed, "Click the
#: Download the registration form link" fell to the CLICK ladder, the DOM
#: click genuinely worked, the step reported done, and no file ever arrived —
#: confident, wrong, and silent, discovered only by checking the disk by hand.
_DOWNLOAD = ["web_download"]

_LADDERS = {"click": _CLICK, "type": _TYPE, "open": _OPEN, "read": _READ,
            "file_read": _FILE_READ, "file_write": _FILE_WRITE,
            "upload": _UPLOAD, "download": _DOWNLOAD}

#: "download" as a standalone word, not as a modifier of something already on
#: disk. Word-bounded so "the downloaded form" (past tense — a FILE step) does
#: not collide with "Download the form" (present tense — a browser step): the
#: 'd' immediately after "download" in "downloaded" fails the trailing \b.
_DOWNLOAD_WORD = re.compile(r"\bdownload\b")

#: Words that mean the DISK rather than the page.
_FILE_WORDS = ("file", "document", ".txt", ".pdf", ".csv", ".json", ".md",
               "desktop", "downloads", "downloaded", "folder", "directory",
               "disk")
_SAVE_WORDS = ("fill ", "save ", "write ", "edit ", "complete ")


def _is_local(intent: str) -> bool:
    """Does this step mean a file on disk, or a page in a browser?

    Deliberately narrow. Guessing "local" for a web step would send a page
    read to the filesystem, which is the same class of confident wrong answer
    in the other direction.
    """
    low = f" {intent} "
    if "http://" in low or "https://" in low or " url " in low:
        return False
    return any(w in low for w in _FILE_WORDS)


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


#: Nouns that close the name of a control. A determiner opens a name and one
#: of these ends it, so "the Go to Submit page link" is a label from "the" to
#: "link" — not an instruction to go anywhere.
_NAME_END = ("link", "button", "form", "page", "field", "box", "tab", "menu",
             "option", "checkbox", "icon", "file", "entry", "item", "result")

_NAME_SPAN = re.compile(
    r"\b(?:the|a|an|that|this|its)\b.{0,60}?\b(?:%s)s?\b" % "|".join(_NAME_END))
_QUOTED = re.compile(r"""["'“”‘’][^"'“”‘’]{0,60}["'“”‘’]""")


def _without_names(low: str) -> str:
    """The sentence with the names of things blanked out.

    Websites name controls after what they do — "Download the form", "Submit
    form", "Go to checkout" — so counting verbs across a raw sentence counts
    labels as actions. It refused `Click the Go to Submit page link`, which is
    one click, and with it the entire e2e workflow.

    Non-greedy and length-capped so a name span stops at the first closing
    noun instead of swallowing a real second action further down the line.
    """
    return _NAME_SPAN.sub(" ", _QUOTED.sub(" ", low))


def is_compound(intent: str) -> bool:
    """Is this several actions wearing one step?

    Two signals, because either alone is wrong. A joining word ("then",
    "after that") is explicit — and it counts only OUTSIDE a name, so a
    control called "Terms and Conditions" is not two steps. Failing that,
    three or more action verbs is not a step; two is tolerated, because
    "click the Download button" contains "click" and "download" and is
    perfectly single.
    """
    low = f" {(intent or '').lower().strip()} "
    bare = f" {_without_names(low).strip()} "
    if any(j in bare for j in _JOINERS):
        return True
    return sum(1 for v in _ACTION_WORDS if v in bare) >= 3


def kind_of(step: Step) -> str:
    """Which ladder this step belongs on, from how a person phrased it.

    Disk beats browser: the local checks come FIRST, because the failure they
    prevent is silent. "Read the file on my Desktop" routed to the read ladder
    returns the current web page, cheerfully, as though it were the file.
    """
    intent = (step.intent or "").lower().strip()

    if intent.startswith(("upload", "attach", "choose file", "select file")):
        return "upload"
    if _DOWNLOAD_WORD.search(intent):
        return "download"
    if _is_local(intent):
        if any(w in f" {intent} " for w in _SAVE_WORDS):
            return "file_write"
        if intent.startswith(("read", "open", "look", "check", "find", "see")):
            return "file_read"

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
    # Hand in the blackboard right before anything runs. Same dict object as
    # `mission.facts` — never a copy — so a runner that writes into
    # `step.data` here has told every later step, which is the only way "read
    # the file on my Desktop" can reach "fill the form with those details".
    step.data = mission.facts
    # And the one up-front yes, re-read from the mission every attempt rather
    # than cached on the step: a step retried after a reconnect must see
    # whatever `mission.authorized` is NOW, not whatever it was when the step
    # was first attempted.
    step.authorized = mission.authorized

    # None of these are judged by whether the PAGE changed — a download and an
    # upload both say so explicitly (`web_agency._download`/`_upload`: success
    # is "a file is on disk" / "the input holds the file", not "the page
    # reacted"), and file_read/file_write never touch a page at all. Flagging
    # "nothing changed" on any of them would make the doubt signal noise
    # instead of information — the same reasoning that already exempts read.
    from core.mission_ladder import kind_of as _kind          # self, for clarity
    expects_movement = _kind(step) not in (
        "read", "file_read", "file_write", "download", "upload")
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
