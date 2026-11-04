"""Ways of doing one step, most exact first: the DOM (exact, in the eagle's browser),
the accessibility tree (exact when the app publishes one), then vision (a guess).

A rung that failed is never offered again for the same step, and running out of
rungs is an outcome (`exhausted`) carrying every reason for the handoff.
"""
from __future__ import annotations

import re

from dataclasses import dataclass
from typing import Callable

from core.mission import Mission, Step

#: Click: DOM, then the accessibility tree, then pixels.
_CLICK = ["web_click", "user_click", "screen_click", "vision_click"]

# Type: the same, ending in single keypresses.
_TYPE = ["web_type", "user_type", "screen_type", "press_keys"]

# Open: the eagle's own browser first; web_agency cannot see into the user's Chrome.
_OPEN = ["web_open", "browser_open", "user_open"]

#: Read: never clicks anything.
_READ = ["web_look", "user_look", "screen_look"]

# The disk: reading a file must never fall to a page read that reports success.
_FILE_READ = ["file_read"]
_FILE_WRITE = ["file_fill", "file_write"]

# Handing a file to a page: success means the input holds the file.
_UPLOAD = ["web_upload"]

# Getting a file from a page: success means a file on disk, not a page reaction.
_DOWNLOAD = ["web_download"]

_LADDERS = {"click": _CLICK, "type": _TYPE, "open": _OPEN, "read": _READ,
            "file_read": _FILE_READ, "file_write": _FILE_WRITE,
            "upload": _UPLOAD, "download": _DOWNLOAD}

# "download" as a standalone word: "downloaded" fails the trailing \b, so "the
# downloaded form" is a file step.
_DOWNLOAD_WORD = re.compile(r"\bdownload\b")

# Words that mean the disk rather than the page.
_FILE_WORDS = ("file", "document", ".txt", ".pdf", ".csv", ".json", ".md",
               "desktop", "downloads", "downloaded", "folder", "directory",
               "disk")
_SAVE_WORDS = ("fill ", "save ", "write ", "edit ", "complete ")


def _is_local(intent: str) -> bool:
    """Does this step mean a file on disk or a page? Deliberately narrow."""
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
    # True/False when the world was observed before and after, None when not. `ok` means
    # the call worked; this means something happened.
    moved: bool | None = None


# Words that join two actions: a step with one was never decomposed.
_JOINERS = (" then ", " and then ", " after that ", " next ", " followed by ",
            " , then ")

# Verbs that start an action. Two in one step is two steps.
_ACTION_WORDS = ("open ", "go to ", "navigate ", "click ", "press ", "type ",
                 "write ", "enter ", "fill ", "search ", "download ",
                 "upload ", "submit ", "select ", "read ", "save ")


# Nouns that close a control's name, so "the Go to Submit page link" is one label.
_NAME_END = ("link", "button", "form", "page", "field", "box", "tab", "menu",
             "option", "checkbox", "icon", "file", "entry", "item", "result")

_NAME_SPAN = re.compile(
    r"\b(?:the|a|an|that|this|its)\b.{0,60}?\b(?:%s)s?\b" % "|".join(_NAME_END))
_QUOTED = re.compile(r"""["'“”‘’][^"'“”‘’]{0,60}["'“”‘’]""")


def _without_names(low: str) -> str:
    """The sentence with control names blanked out, so a label like "Download the
    form" does not count as an action. Non-greedy and length-capped.
    """
    return _NAME_SPAN.sub(" ", _QUOTED.sub(" ", low))


def is_compound(intent: str) -> bool:
    """Is this several actions in one step? A joining word outside a name, or three
    or more action verbs (two is tolerated: "click the Download button").
    """
    low = f" {(intent or '').lower().strip()} "
    bare = f" {_without_names(low).strip()} "
    if any(j in bare for j in _JOINERS):
        return True
    return sum(1 for v in _ACTION_WORDS if v in bare) >= 3


def kind_of(step: Step) -> str:
    """Which ladder this step belongs on. Disk checks first: a misrouted file read
    fails silently.
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
    """Walk the ladder until a rung works, and check the world moved. A rung with no
    runner is skipped, not counted as a failure. With `observe`, a successful rung
    that changed nothing is still ok but carries moved=False. Steps that
    legitimately change nothing are exempt.
    """
    # Hand in the blackboard (the same dict as mission.facts) so later steps see what
    # earlier ones found.
    step.data = mission.facts
    # Re-read the up-front yes from the mission on every attempt.
    step.authorized = mission.authorized

    # Downloads, uploads and file steps are not judged by page change.
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
    """Did the page change, and is it worth saying? (moved, note); moved is None when
    the world could not be read.
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
