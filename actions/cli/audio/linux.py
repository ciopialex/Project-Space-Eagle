"""Volume on Linux: PipeWire first, then PulseAudio, then ALSA.

Three backends rather than one because the audio stack genuinely differs
between machines, and `shutil.which` is a cheap, honest way to find out which
one is here. They differ only in argv and in the text they print back, so each
is a table over `CommandBackend`.
"""
from __future__ import annotations

import shutil

from actions.cli.base import CommandBackend

_SINK_WP = "@DEFAULT_AUDIO_SINK@"
_SINK_PA = "@DEFAULT_SINK@"
_MIXER_ALSA = "Master"


class _LinuxBackend(CommandBackend):
    """Present exactly when its program is on PATH."""

    def available(self) -> bool:
        return shutil.which(self.name) is not None


class WpctlBackend(_LinuxBackend):
    """PipeWire. Volumes are floats in 0.0-1.0, and the mute marker rides on
    the very line the volume comes off: "Volume: 0.40 [MUTED]"."""

    name = "wpctl"
    read_volume = ("wpctl", "get-volume", _SINK_WP)
    volume_re = r"Volume:\s*([0-9]*\.?[0-9]+)"
    volume_scale = 100
    write_volume = ("wpctl", "set-volume", _SINK_WP, "{:.2f}")
    read_mute = read_volume
    mute_re = r"(\[MUTED\])"
    muted_word = "[muted]"
    mute_absent = False  # wpctl prints the marker only when muted
    write_mute = ("wpctl", "set-mute", _SINK_WP, "{}")
    mute_words = ("1", "0")


class PactlBackend(_LinuxBackend):
    """PulseAudio. Volumes are percent strings."""

    name = "pactl"
    read_volume = ("pactl", "get-sink-volume", _SINK_PA)
    volume_re = r"(\d+)%"
    volume_scale = 1
    write_volume = ("pactl", "set-sink-volume", _SINK_PA, "{:.0f}%")
    read_mute = ("pactl", "get-sink-mute", _SINK_PA)
    mute_re = r"Mute:\s*(yes|no)"
    muted_word = "yes"
    mute_absent = None
    write_mute = ("pactl", "set-sink-mute", _SINK_PA, "{}")
    mute_words = ("1", "0")


class AmixerBackend(_LinuxBackend):
    """ALSA, the floor under both of the above. Reads
    "  Front Left: Playback 45874 [70%] [on]".

    Kept because a machine with neither PipeWire nor PulseAudio is a real
    machine, not a hypothetical: dropping this tier took an ALSA-only box from
    working volume to "No usable audio backend", which is worse than the
    keypress code the slice replaced.
    """

    name = "amixer"
    read_volume = ("amixer", "get", _MIXER_ALSA)
    volume_re = r"\[(\d+)%\]"
    volume_scale = 1
    write_volume = ("amixer", "-q", "set", _MIXER_ALSA, "{:.0f}%")
    read_mute = read_volume
    mute_re = r"\[(on|off)\]"
    muted_word = "off"
    mute_absent = None  # a volume-only control prints no switch: unknown
    write_mute = ("amixer", "-q", "set", _MIXER_ALSA, "{}")
    mute_words = ("mute", "unmute")
