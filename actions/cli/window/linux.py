"""X11 window control through xdotool.

xdotool is chosen over wmctrl because it is the one that is installed — and
because it addresses windows by id. wmctrl's `:ACTIVE:` re-resolves the active
window inside wmctrl itself, which reintroduces exactly the race that using an
id removes.

The 2016 build on this machine has no `windowstate`, so true maximise and
fullscreen (EWMH `_NET_WM_STATE`) are not reachable from here. What is
reachable — close, minimise, move, resize, geometry — covers everything the
old keystrokes were aiming at except the maximised state itself.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass

from actions.cli.base import Backend, CommandMixin


@dataclass(frozen=True)
class Window:
    id: str
    name: str


class XdotoolBackend(Backend, CommandMixin):
    def __init__(self, run=None) -> None:
        # Delegate, never assign directly. Setting `self._run` only when a
        # runner was injected left `_run` UNSET in production, where nothing is
        # injected: every call raised AttributeError straight into `_exec`'s
        # bare except and came back None, so the backend reported "no active
        # window" and "no screen size" on a machine where xdotool works fine
        # from the shell. Every test passed, because every test passed a
        # runner — the same shape as the bytes-vs-str defect in the audio
        # backends, and caught the same way: by running it for real.
        CommandMixin.__init__(self, run)

    @property
    def name(self) -> str:
        return "xdotool"

    def available(self) -> bool:
        return shutil.which("xdotool") is not None

    # ------------------------------------------------------------- reading

    def active(self) -> Window | None:
        """The focused window as (id, name), or None if nothing resolves.

        The name is looked up separately and is allowed to fail: an unnamed
        window is ordinary, and dropping the id because the name was empty
        would make splash screens and tooltips uncloseable.
        """
        wid = self._stdout(["xdotool", "getactivewindow"])
        if not wid:
            return None
        wid = wid.strip().splitlines()[0].strip()
        if not wid.isdigit():
            return None
        return Window(id=wid, name=self._window_name(wid))

    def _window_name(self, wid: str) -> str:
        return (self._stdout(["xdotool", "getwindowname", wid]) or "").strip()

    def exists(self, wid: str) -> bool:
        """Read-back. A window that no longer answers has gone."""
        return self._accepted(["xdotool", "getwindowname", wid])

    def screen_size(self) -> tuple[int, int] | None:
        raw = (self._stdout(["xdotool", "getdisplaygeometry"]) or "").split()
        if len(raw) < 2:
            return None
        try:
            return int(raw[0]), int(raw[1])
        except ValueError:
            return None

    # ------------------------------------------------------------- acting

    def close(self, wid: str) -> bool:
        """WM_DELETE_WINDOW, never `windowkill`.

        `windowclose` asks; `windowkill` destroys the client outright and takes
        unsaved work with it. The eagle asks.
        """
        return self._accepted(["xdotool", "windowclose", wid])

    def minimize(self, wid: str) -> bool:
        return self._accepted(["xdotool", "windowminimize", wid])

    def move_resize(self, wid: str, x: int, y: int,
                    width: int, height: int) -> bool:
        moved = self._accepted(["xdotool", "windowmove", wid, str(x), str(y)])
        sized = self._accepted(["xdotool", "windowsize", wid,
                                str(width), str(height)])
        return moved and sized
