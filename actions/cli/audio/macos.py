"""Volume on macOS, via osascript.

The old code nudged relatively ("current + 10"), which cannot be verified and
drifts when two nudges race. This sets an absolute level and reads it back.
"""
from __future__ import annotations

import shutil

from actions.cli.base import CommandBackend

_OSA = ("osascript", "-e")


class OsascriptBackend(CommandBackend):
    name = "osascript"
    read_volume = _OSA + ("output volume of (get volume settings)",)
    volume_re = r"(\d+)"
    volume_scale = 1
    write_volume = _OSA + ("set volume output volume {:.0f}",)
    read_mute = _OSA + ("output muted of (get volume settings)",)
    mute_re = r"^\s*(true|false)"
    muted_word = "true"
    mute_absent = None
    write_mute = _OSA + ("set volume output muted {}",)
    mute_words = ("true", "false")

    #: 16 hardware steps ~6.25 apart, so asking for 47 reads back 44 — and
    #: since the nudge step is 10, nearly every nudge lands off-grid. See
    #: `AudioBackend.tolerance` for what a hardcoded 2 did to that.
    tolerance = 100 / 16

    def available(self) -> bool:
        return shutil.which(self.name) is not None
