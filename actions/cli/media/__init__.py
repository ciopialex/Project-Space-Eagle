"""Media transport on whichever platform this is. Every mutating call reads status()
back and compares it to what the action promised.

Three rules are documented where enforced: can() is read only after a refusal
(_refusal), an unreadable state is not "nothing is playing" (_player_present), and
the read-back is a bounded poll (_settle).
"""
from __future__ import annotations

import time

from actions.cli import registry
from actions.cli.base import MediaBackend
from core.tool_result import ToolResult

# How long and how often a command re-reads status() for the promised state. The player
# updates its state after the bus call returns (measured: 48 ms after `play`). A poll
# exits at the first agreeing read; a fixed sleep always pays the worst case.
_SETTLE_TIMEOUT_S = 0.5
_SETTLE_INTERVAL_S = 0.025


def backends_for(platform: str) -> list[MediaBackend]:
    """Preference order per platform, first is best. Imports stay inside their
    branch: windows.py needs WinRT.
    """
    if platform == "windows":
        from actions.cli.media.windows import SmtcBackend
        return [SmtcBackend()]
    if platform == "macos":
        from actions.cli.media.macos import OsascriptMediaBackend
        return [OsascriptMediaBackend()]
    # One rung on Linux: MPRIS is what every player publishes.
    from actions.cli.media.linux import MprisBackend
    return [MprisBackend()]


# action -> (backend method, the state it promises, the sentence when it worked, the
# refusal verb). next/previous promise nothing: a skip leaves a paused player paused.
_ACTIONS = {
    "play": ("play", "playing", "Playing", "resume"),
    "pause": ("pause", "paused", "Paused", "pause"),
    "next": ("next", None, "Skipped to the next track", "skip forward"),
    "previous": ("previous", None, "Back to the previous track", "skip back"),
}

_PHRASE = {"playing": "Playing.", "paused": "Paused.",
           "stopped": "Stopped — nothing is playing right now."}


def _result_no_player() -> ToolResult:
    """Nothing to control: not a malfunction. The macOS guidance states what macos.py
    is limited to.
    """
    guidance = "Ask the user to start something playing, then try again."
    if registry.current_platform() == "macos":
        guidance += (" On macOS only Spotify and Music can be controlled — "
                     "browser video is out of reach, because macOS publishes "
                     "no system-wide now-playing API.")
    return ToolResult.failure("Nothing is playing that I can control.",
                              guidance=guidance)


def _resolve(backend: MediaBackend | None) -> MediaBackend | None:
    if backend is not None:
        return backend
    return registry.pick(backends_for(registry.current_platform()))


def _player_present(chosen: MediaBackend) -> bool | None:
    """Is there a player at all? True/False when the backend names a target(), None
    when it cannot say.
    """
    if type(chosen).target is MediaBackend.target:
        return None
    try:
        return chosen.target() is not None
    except Exception:
        return None


def get_status(backend: MediaBackend | None = None) -> ToolResult:
    if (chosen := _resolve(backend)) is None:
        return _result_no_player()
    state = chosen.status()
    if state is None:
        if _player_present(chosen) is False:
            return _result_no_player()
        return ToolResult.failure(
            "There is a player, but it did not report what it is doing.",
            guidance=("It may still be starting up or stuck. Wait a moment "
                      "and ask again, or tell the user to check the player."),
            status=None, backend=chosen.name)
    return ToolResult.success(_PHRASE.get(state, f"Playback is {state}."),
                              status=state, backend=chosen.name)


def _refusal(chosen: MediaBackend, action: str) -> ToolResult:
    """The backend refused: now ask can(), which separates three different answers."""
    verb = _ACTIONS[action][3]
    if chosen.can(action):
        # It said yes and then did not: the player probably just quit.
        return ToolResult.failure(
            f"The player accepted a {verb} but did not carry it out.",
            guidance="It may have just closed. Check what is playing.",
            backend=chosen.name)
    present = _player_present(chosen)
    if present is False:
        return _result_no_player()
    if present is None:
        # Cannot tell which: say both.
        return ToolResult.failure(
            f"Nothing accepted the {verb}.",
            guidance=("Either nothing is playing, or the player will not "
                      f"{verb} right now. Ask for the playback status."),
            backend=chosen.name)
    return ToolResult.failure(
        f"The player is there but will not {verb} right now.",
        guidance=("A stopped player, or an ad, refuses individual buttons. "
                  "Try starting playback first."),
        backend=chosen.name)


def _settle(chosen: MediaBackend, promised: str | None) -> str | None:
    """Read status() back until it is what was promised, or time runs out. Returns
    the last state seen. A single immediate read caught the state from before the
    command and reported a working play as a failure. None is polled through like
    any not-yet.
    """
    # Nothing promised: any state is right.
    if promised is None:
        return chosen.status()
    deadline = time.monotonic() + _SETTLE_TIMEOUT_S
    while True:
        state = chosen.status()
        if state == promised or time.monotonic() >= deadline:
            return state
        time.sleep(_SETTLE_INTERVAL_S)


def _command(action: str, backend: MediaBackend | None) -> ToolResult:
    """Act, then check the state against the promise."""
    if (chosen := _resolve(backend)) is None:
        return _result_no_player()
    method, promised, done, _ = _ACTIONS[action]
    if not getattr(chosen, method)():
        return _refusal(chosen, action)
    state = _settle(chosen, promised)
    if state is None:
        # Probably worked, unproven: say so, and do not put `promised` in the data.
        return ToolResult.success(
            f"{done}, but could not read the player's state back.",
            status=None, backend=chosen.name, verified=False)
    if promised is not None and state != promised:
        return ToolResult.failure(
            f"Asked the player to {action}, but it is {state}.",
            guidance="Another application may be controlling playback.",
            status=state, backend=chosen.name)
    return ToolResult.success(f"{done}.", status=state, backend=chosen.name,
                              verified=True)


def play(backend: MediaBackend | None = None) -> ToolResult:
    return _command("play", backend)


def pause(backend: MediaBackend | None = None) -> ToolResult:
    return _command("pause", backend)


def next_track(backend: MediaBackend | None = None) -> ToolResult:
    return _command("next", backend)


def previous_track(backend: MediaBackend | None = None) -> ToolResult:
    return _command("previous", backend)


def play_pause(backend: MediaBackend | None = None) -> ToolResult:
    """Read the state, then send the explicit verb: a toggle reports a state it never
    measured.
    """
    if (chosen := _resolve(backend)) is None:
        return _result_no_player()
    state = chosen.status()
    if state is None:
        if _player_present(chosen) is False:
            return _result_no_player()
        return ToolResult.failure(
            "Could not read what the player is doing, so cannot tell whether "
            "to play or pause.",
            # Name two actions the model can invoke.
            guidance=("Ask for it explicitly instead: media_play to resume, "
                      "media_pause to pause."),
            status=None, backend=chosen.name)
    # A stopped player gets play.
    return pause(backend=chosen) if state == "playing" else play(backend=chosen)
