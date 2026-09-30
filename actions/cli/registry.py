"""Resolve the platform once.

`computer_settings.py` re-made the same `if _OS == "Windows"` decision in every
function, which is how it drifted: `volume_set` reached for pycaw on Windows
while `volume_up` pressed a key. One resolution point, one answer.
"""
from __future__ import annotations

import platform

from actions.cli.base import Backend

_PLATFORMS = {"Linux": "linux", "Windows": "windows", "Darwin": "macos"}


def current_platform() -> str:
    """`linux` | `windows` | `macos`. Anything else is treated as linux, which
    is the closest match for the BSDs and keeps the eagle from hard-failing on
    a platform we have not met."""
    return _PLATFORMS.get(platform.system(), "linux")


def pick(backends: list[Backend]) -> Backend | None:
    """The first backend that says it can run. Order is preference, not
    alphabet — callers list their best option first."""
    for backend in backends:
        try:
            if backend.available():
                return backend
        except Exception:
            continue
    return None
