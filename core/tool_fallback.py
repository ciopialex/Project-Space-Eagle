"""When a narrow tool fails, name the general one that can still do the job.

The failure this exists for, in the user's own words: he asked for his own
YouTube liked videos and was told "I can't access that, YouTube keeps it
private." That was false. They are his own account's videos, visible in any
signed-in browser, and `web_agency` was never tried. `youtube_video` — a tool
that only plays a search result — had captured the request, failed, and the
model reported the tool's limit as the world's.

The prompt was updated to tell the model to reach for a general tool when a
narrow one fails. That was necessary and not sufficient: it leaves the recovery
to the model's judgement at the exact moment its judgement has already been
shown to be wrong. Prompt text is advice. This is a mechanism — the guidance is
attached by code, on every failure, whether or not the model would have thought
of it.

Deliberately NOT automatic re-dispatch. Silently running a different tool than
the one chosen would hide the routing mistake from the user and from us, and
the general tools are the ones with the widest blast radius (a browser, the
screen, the disk). Naming the alternative keeps the decision visible and the
next actor accountable.
"""
from __future__ import annotations

#: Narrow tool -> the general tool that covers the same ground, with the reason
#: phrased for the model that has just failed. Keyed on the tool that captured
#: the request, not on the kind of failure: a narrow tool failing is itself the
#: signal, regardless of which way it broke.
FALLBACKS: dict[str, tuple[str, str]] = {
    "youtube_video": (
        "web_agency",
        "it drives a real signed-in browser and can open the user's own "
        "history, liked videos, playlists and subscriptions"),
    "browser_control": (
        "web_agency",
        "it can read what controls a page actually has and click them by name"),
    "web_search": (
        "web_agency",
        "it can open the site itself instead of only searching for it"),
    "send_message": (
        "web_agency",
        "the web version of the messaging app can be driven directly"),
    "messages_brief": (
        "web_agency",
        "the web version of the mailbox can be opened and read directly"),
    "open_app": (
        "computer_control",
        "it can find and click the app on screen the way a person would"),
    "computer_settings": (
        "computer_control",
        "it can operate the settings window on screen directly"),
    "file_processor": (
        "file_controller",
        "it can read and write the file directly"),
    "code_helper": (
        "swarm_mode",
        "it can build and verify the change end to end"),
}

#: Tools that ARE the general fallback. A general tool failing means the task
#: is genuinely blocked, and pointing it at itself would invite a loop.
GENERAL = frozenset({"web_agency", "computer_control", "file_controller",
                     "swarm_mode"})


def fallback_for(tool_name: str) -> tuple[str, str] | None:
    """The general tool to try after `tool_name` failed, if there is one."""
    if tool_name in GENERAL:
        return None
    return FALLBACKS.get(tool_name)


def with_fallback_guidance(tool_name: str, result):
    """Append the escape hatch to a failed narrow tool's guidance.

    Returns `result` unchanged when it succeeded, when the tool is already a
    general one, or when the guidance already names the fallback — the point is
    to guarantee the hint exists, not to say it twice.
    """
    if getattr(result, "ok", True):
        return result

    pair = fallback_for(tool_name)
    if pair is None:
        return result

    tool, why = pair
    existing = getattr(result, "guidance", "") or ""
    if tool in existing:
        return result

    hint = (f"This is {tool_name}'s limit, not the task's. Try {tool} before "
            f"telling the user it cannot be done — {why}.")
    result.guidance = f"{existing} {hint}".strip()
    return result
