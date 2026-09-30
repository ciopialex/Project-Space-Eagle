"""Volume on Windows, via pycaw's scalar API.

Scalar, not `SetMasterVolumeLevel`: that one takes decibels, so the old code's
`20*log10(50/100)` set -6.02 dB and read back as roughly 78%. The eagle was
wrong about its own action even when the call succeeded. The scalar API maps
0-100 linearly onto 0.0-1.0 and round-trips.

`endpoint_factory` is injected because pycaw imports COM and cannot load off
Windows — hence the imports inside `_default_endpoint`. They must stay there,
or this module stops importing on every machine we develop on.
"""
from __future__ import annotations

from typing import Any, Callable

from actions.cli.base import AudioBackend, clamp


def _default_endpoint() -> Any:
    from ctypes import POINTER, cast

    import comtypes
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    # COM is per-thread and comtypes only initializes the thread that imports
    # it, but `computer_settings` runs tools on a multi-worker pool: the turn
    # after the importing one lands on another worker and GetSpeakers raises
    # "CoInitialize has not been called", which the guards below swallow into
    # "No usable audio backend" on a machine with working speakers. Idempotent
    # per thread, so calling it on every build is correct and cheap.
    comtypes.CoInitialize()

    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


class PycawBackend(AudioBackend):
    """Every call rebuilds the endpoint, because every call may land on a
    different pool thread and a COM object belongs to the thread that made it.
    """

    name = "pycaw"

    def __init__(self,
                 endpoint_factory: Callable[[], Any] | None = None) -> None:
        self._factory = endpoint_factory or _default_endpoint

    def _read(self, call: Callable[[Any], Any]) -> Any | None:
        try:
            return call(self._factory())
        except Exception:
            return None

    def _write(self, call: Callable[[Any], Any]) -> bool:
        try:
            call(self._factory())
            return True
        except Exception:
            return False

    def available(self) -> bool:
        return self._write(lambda _endpoint: None)

    def get(self) -> int | None:
        return self._read(
            lambda ep: int(round(ep.GetMasterVolumeLevelScalar() * 100)))

    def set(self, percent: int) -> bool:
        value = clamp(percent) / 100
        return self._write(
            lambda ep: ep.SetMasterVolumeLevelScalar(value, None))

    def set_muted(self, muted: bool) -> bool:
        return self._write(lambda ep: ep.SetMute(1 if muted else 0, None))

    def get_muted(self) -> bool | None:
        return self._read(lambda ep: bool(ep.GetMute()))
