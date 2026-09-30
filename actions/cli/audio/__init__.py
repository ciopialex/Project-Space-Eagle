"""Volume, on whichever platform this is.

Every mutating call reads the value back and compares it to what was asked
for. A command that exits 0 without moving the volume is a failure here — the
old code called that success, which is exactly the lie `ToolResult` exists to
kill.
"""
from __future__ import annotations

from actions.cli import registry
from actions.cli.base import AudioBackend, clamp
from core.tool_result import ToolResult


def backends_for(platform: str) -> list[AudioBackend]:
    """Preference order per platform. First entry is the best available path."""
    if platform == "windows":
        from actions.cli.audio.windows import PycawBackend
        return [PycawBackend()]
    if platform == "macos":
        from actions.cli.audio.macos import OsascriptBackend
        return [OsascriptBackend()]
    from actions.cli.audio.linux import (AmixerBackend, PactlBackend,
                                         WpctlBackend)
    # amixer last and never removed: an ALSA-only machine has neither of the
    # other two, and without this tier it gets "no usable audio backend" while
    # its speakers work fine.
    return [WpctlBackend(), PactlBackend(), AmixerBackend()]


def _result_no_backend() -> ToolResult:
    return ToolResult.failure(
        "No usable audio backend on this machine.",
        guidance=("Install one: PipeWire's wpctl, PulseAudio's pactl or "
                  "ALSA's amixer on Linux, or check that an audio device is "
                  "present."))


def _resolve(backend: AudioBackend | None) -> AudioBackend | None:
    if backend is not None:
        return backend
    return registry.pick(backends_for(registry.current_platform()))


def _unreadable(chosen: AudioBackend, message: str, guidance: str) -> ToolResult:
    """The backend answered, but not with the value it was asked for. Naming
    the backend matters: which of three it was is the first thing to check."""
    return ToolResult.failure(message, guidance=guidance, backend=chosen.name)


def get_volume(backend: AudioBackend | None = None) -> ToolResult:
    if (chosen := _resolve(backend)) is None:
        return _result_no_backend()
    value = chosen.get()
    if value is None:
        return _unreadable(chosen, "Could not read the current volume.",
                           "The audio backend answered, but not with a volume.")
    return ToolResult.success(f"Volume is {value}%.",
                              volume=value, backend=chosen.name)


def set_volume(percent: int, backend: AudioBackend | None = None) -> ToolResult:
    if (chosen := _resolve(backend)) is None:
        return _result_no_backend()
    target = clamp(percent)
    if not chosen.set(target):
        return _unreadable(chosen, f"Could not set the volume to {target}%.",
                           "The audio backend rejected the change.")
    actual = chosen.get()
    if actual is None:
        # It probably worked; we cannot prove it. Say exactly that.
        return ToolResult.success(
            f"Set the volume to {target}%, but could not read it back.",
            volume=target, backend=chosen.name, verified=False)
    # The backend, not this function, says how far a read-back may sit from the
    # request — see `AudioBackend.tolerance` for what a hardcoded 2 did to a Mac.
    if abs(actual - target) > chosen.tolerance:
        return ToolResult.failure(
            f"Asked for {target}% but the volume is {actual}%.",
            guidance="Another application may be controlling the volume.",
            volume=actual, backend=chosen.name)
    return ToolResult.success(f"Volume is now {actual}%.", volume=actual,
                              backend=chosen.name, verified=True)


def nudge_volume(delta: int, backend: AudioBackend | None = None) -> ToolResult:
    """Relative change, measured from where the volume actually is. The old
    `volume_up` sent five keypresses and hoped; reading first is what makes
    "turn it up a bit" reportable as a number."""
    if (chosen := _resolve(backend)) is None:
        return _result_no_backend()
    current = chosen.get()
    if current is None:
        return _unreadable(
            chosen,
            "Could not read the current volume, so cannot change it by a step.",
            "Use an absolute volume instead, e.g. 'set volume to 50'.")
    return set_volume(current + int(delta), backend=chosen)


def get_mute(backend: AudioBackend | None = None) -> ToolResult:
    if (chosen := _resolve(backend)) is None:
        return _result_no_backend()
    state = chosen.get_muted()
    if state is None:
        return _unreadable(
            chosen, "Could not read the current mute state.",
            "The audio backend answered, but not with a mute state.")
    return ToolResult.success("Muted." if state else "Not muted.",
                              muted=state, backend=chosen.name)


def set_mute(muted: bool, backend: AudioBackend | None = None) -> ToolResult:
    if (chosen := _resolve(backend)) is None:
        return _result_no_backend()
    if not chosen.set_muted(muted):
        return _unreadable(chosen, "Could not change the mute state.",
                           "The audio backend rejected the change.")
    word = "Muted" if muted else "Unmuted"
    # Read back, exactly like set_volume: a command that exits 0 without
    # changing the state is a failure, not a success.
    actual = chosen.get_muted()
    if actual is None:
        return ToolResult.success(f"{word}, but could not read the state back.",
                                  muted=muted, backend=chosen.name,
                                  verified=False)
    if actual is not muted:
        return ToolResult.failure(
            f"Asked to be {'muted' if muted else 'unmuted'} but the audio is "
            f"{'muted' if actual else 'not muted'}.",
            guidance="Another application may be controlling the audio.",
            muted=actual, backend=chosen.name)
    return ToolResult.success(f"{word}.", muted=actual, backend=chosen.name,
                              verified=True)


def toggle_mute(backend: AudioBackend | None = None) -> ToolResult:
    """Read the state, then set its opposite explicitly.

    Never `set_muted(not remembered)` and never a backend-level toggle: both
    report a state they did not measure. When the state cannot be read this
    refuses rather than flipping a coin on the user's speakers.
    """
    if (chosen := _resolve(backend)) is None:
        return _result_no_backend()
    current = chosen.get_muted()
    if current is None:
        return _unreadable(
            chosen,
            "Could not read the current mute state, so cannot toggle it.",
            "Ask for it explicitly instead: mute to mute, unmute to unmute.")
    return set_mute(not current, backend=chosen)
