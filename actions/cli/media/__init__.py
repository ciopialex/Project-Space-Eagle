"""Media transport, on whichever platform this is.

Every mutating call reads `status()` back and compares it to what the action
promised. What this replaced was `pyautogui.press("space")`: a keystroke to
whatever window had focus, which could not fail, could not be verified, and —
in the scenario this assistant exists for, a podcast playing while the user is
in a game — went to the game.

Three rules here are easy to get backwards, and each is documented where it is
enforced: why `can()` is read only after the action, at `_refusal`; why an
unreadable state is not "nothing is playing", at `_player_present`; and why
that read-back is a bounded poll rather than one immediate read, at `_settle`.
"""
from __future__ import annotations

import time

from actions.cli import registry
from actions.cli.base import MediaBackend
from core.tool_result import ToolResult

#: How long a mutating command may keep re-reading `status()` waiting for the
#: state it promised, and how often it reads. Both come from a measurement, not
#: a preference. A media command is a call on a bus; the state is a property
#: the player's own process updates afterwards, so the two do not land
#: together. Timed against a live Chromium/MPRIS player on Linux: `play`
#: returned with the state still reading "paused" and read "playing" 48ms
#: later, while a `pause` sent to an already-paused player was still being
#: polled at 2869ms because nothing was ever going to change.
#:
#: So: the interval is short enough that the 48ms case costs two or three
#: reads, and the deadline is long enough to cover a bus slower than this
#: machine's without making a genuinely dead command hang the assistant. Do not
#: swap this for a fixed sleep — a sleep pays the worst case every time and
#: still guesses; the loop below exits at the first read that agrees.
_SETTLE_TIMEOUT_S = 0.5
_SETTLE_INTERVAL_S = 0.025


def backends_for(platform: str) -> list[MediaBackend]:
    """Preference order per platform. First entry is the best available path.

    Each import sits inside its branch, not at the top: `windows.py` reaches
    for WinRT, which cannot load anywhere else, and a top-level import would
    take this package down on every machine we develop on.
    """
    if platform == "windows":
        from actions.cli.media.windows import SmtcBackend
        return [SmtcBackend()]
    if platform == "macos":
        from actions.cli.media.macos import OsascriptMediaBackend
        return [OsascriptMediaBackend()]
    # One rung on Linux by design: MPRIS is what every player publishes, so a
    # second backend would be a second way to reach the same bus.
    from actions.cli.media.linux import MprisBackend
    return [MprisBackend()]


#: action -> (backend method, the state it promises, the sentence when it
#: worked, the verb for a refusal). `next`/`previous` promise nothing: a skip
#: leaves a paused player paused, and demanding "playing" back would fail a
#: track change that worked perfectly.
_ACTIONS = {
    "play": ("play", "playing", "Playing", "resume"),
    "pause": ("pause", "paused", "Paused", "pause"),
    "next": ("next", None, "Skipped to the next track", "skip forward"),
    "previous": ("previous", None, "Back to the previous track", "skip back"),
}

_PHRASE = {"playing": "Playing.", "paused": "Paused.",
           "stopped": "Stopped — nothing is playing right now."}


def _result_no_player() -> ToolResult:
    """Nothing to control. Not a malfunction, and it must not sound like one.

    The macOS half of the guidance says what `macos.py` is limited to, because
    a user whose YouTube tab will not pause needs to hear the limit rather than
    spend an afternoon hunting a bug.
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
    """Is there a player at all — separately from whether it will answer.

    True/False when the backend names a `target()`, None when it declines to.
    A backend that leaves the optional default in place cannot say, and the
    caller then words its answer to cover both cases rather than picking one.
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
    """The backend would not do it, and `can()` is finally worth asking.

    Only on this path: on the happy path it was a second survey of the bus for
    an answer the backend had already used. Here it buys the difference
    between three genuinely different sentences.
    """
    verb = _ACTIONS[action][3]
    if chosen.can(action):
        # It said yes and then did not do it — the player has probably just quit.
        return ToolResult.failure(
            f"The player accepted a {verb} but did not carry it out.",
            guidance="It may have just closed. Check what is playing.",
            backend=chosen.name)
    present = _player_present(chosen)
    if present is False:
        return _result_no_player()
    if present is None:
        # Cannot tell which it is, so say both rather than pick the likelier.
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
    """Read `status()` back until it is what was promised, or time runs out.

    The single immediate read this replaced was the whole bug: it caught the
    state from *before* the command, so a play that worked came back "paused"
    and was reported as a failure — and the model, told the action failed,
    retried and toggled the audio straight back off. A false negative here is
    worse than no verification at all.

    Returns the last state seen either way, so the caller still fails honestly
    on a command that genuinely did not take: waiting longer for a mismatch is
    not the same as accepting it.

    An unreadable `None` is polled through rather than returned at once, on
    purpose — "not readable yet" is the same kind of not-yet as "not updated
    yet", and a player that is still waking up gets the same grace as a slow
    one. A player that never answers costs the deadline and then gets the
    honest unverified success it always got.
    """
    # Nothing promised, nothing to wait for: `next` leaves a paused player
    # paused, so any state it reports is already the right one.
    if promised is None:
        return chosen.status()
    deadline = time.monotonic() + _SETTLE_TIMEOUT_S
    while True:
        state = chosen.status()
        if state == promised or time.monotonic() >= deadline:
            return state
        time.sleep(_SETTLE_INTERVAL_S)


def _command(action: str, backend: MediaBackend | None) -> ToolResult:
    """Act, then read the state back and check it against what was promised."""
    if (chosen := _resolve(backend)) is None:
        return _result_no_player()
    method, promised, done, _ = _ACTIONS[action]
    if not getattr(chosen, method)():
        return _refusal(chosen, action)
    state = _settle(chosen, promised)
    if state is None:
        # It probably worked; we cannot prove it. Say exactly that, and do NOT
        # put `promised` in the data — that is a state nobody observed.
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
    """Read the state, then send the explicit verb for it.

    Same rule as `toggle_mute` in the audio slice, with the same reason: a
    toggle reports a state it did not measure. Guessing here pauses the podcast
    the user asked to resume, and then says it resumed it.
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
            # Names two actions the model can actually invoke: guidance
            # pointing at a verb it cannot is a dead end dressed as a step.
            guidance=("Ask for it explicitly instead: media_play to resume, "
                      "media_pause to pause."),
            status=None, backend=chosen.name)
    # A stopped player gets play: "play/pause" on silence means start it.
    return pause(backend=chosen) if state == "playing" else play(backend=chosen)
