"""Media transport on macOS, via osascript — honest partial coverage.

macOS has no public system-wide now-playing API (MediaRemote is private), so
this drives only what AppleScript exposes: the two apps below. It cannot touch
browser video, and `available()` is False when neither runs rather than
pretending otherwise.

Why per-action gating and `status()` exist at all: `MediaBackend` in `base.py`.
"""
from __future__ import annotations

from actions.cli.base import CommandMixin, CommandRunner, MediaBackend

_OSA = ("osascript", "-e")

#: Probed in this order; the first one running is the one commanded.
_APPS = ("Spotify", "Music")

#: action -> AppleScript verb. Both apps take the same four, which is what lets
#: one table serve either. `pause`, never the `playpause` both also offer: a
#: toggle cannot report the state it left behind.
_COMMANDS = {"play": "play", "pause": "pause",
             "next": "next track", "previous": "previous track"}

#: Music also answers `fast forwarding` and `rewinding`; both are audibly
#: playing. Anything else is None — unknown must not read as stopped.
_STATES = {"playing": "playing", "paused": "paused", "stopped": "stopped",
           "fast forwarding": "playing", "rewinding": "playing"}


class OsascriptMediaBackend(CommandMixin, MediaBackend):
    """Whichever scriptable music app is running, as one transport."""

    #: Not "osascript": the audio slice already publishes that for volume, and
    #: two backends sharing a name makes the ToolResult unable to say which ran.
    name = "osascript-media"

    def __init__(self, run: CommandRunner | None = None) -> None:
        super().__init__(run)
        #: The app settled on for this object's lifetime — see `target`.
        self._target: str | None = None

    def _tell(self, app: str, phrase: str) -> str | None:
        """One script at one app, or None if it did not answer. `text=True`
        comes from `CommandMixin._exec`, which is where that hazard lives."""
        return self._stdout(_OSA + (f'tell application "{app}" to {phrase}',))

    def app(self) -> str | None:
        """Probe both apps and name the one running. The resolver behind
        `target`, which is what everything else asks.

        `is running` before `tell`: asking a stopped app for its player state
        launches it, and opening Music because the user said "pause" is worse
        than saying no.
        """
        for app in _APPS:
            out = self._stdout(_OSA + (f'application "{app}" is running',))
            if out is not None and out.strip().lower() == "true":
                return app
        return None

    def target(self) -> str | None:
        """The app this object commands: resolved once, then kept, per
        `MediaBackend.target`. Nothing is cached while none is running.
        """
        if self._target is None:
            self._target = self.app()
        return self._target

    def available(self) -> bool:
        return self.target() is not None

    def status(self) -> str | None:
        app = self.target()
        out = self._tell(app, "player state") if app is not None else None
        return _STATES.get(out.strip().lower()) if out is not None else None

    def can(self, action: str) -> bool:
        # A running app takes all four; there is no per-action flag to read.
        # An action we cannot spell is still one we cannot promise.
        return action in _COMMANDS and self.target() is not None

    def _do(self, action: str) -> bool:
        app = self.target()
        if app is None or action not in _COMMANDS:
            return False
        return self._tell(app, _COMMANDS[action]) is not None

    def play(self) -> bool:
        return self._do("play")

    def pause(self) -> bool:
        return self._do("pause")

    def next(self) -> bool:
        return self._do("next")

    def previous(self) -> bool:
        return self._do("previous")
