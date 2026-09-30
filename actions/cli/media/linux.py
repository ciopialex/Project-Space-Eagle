"""Media transport on Linux, over MPRIS — the interface every player already
publishes on the session bus.

Two Linux-specific measurements. Capabilities come from `Properties.Get` and
never from introspection: `gdbus introspect` on Chromium's MPRIS object returns
an empty node, while the same object answers `Properties.Get` perfectly, so an
introspection-based check sees a player that can do nothing. And `gdbus` rather
than a Python D-Bus binding, because it ships with GLib on every desktop Linux
this targets while `dbus-python` is an optional compiled dependency and is
absent on this machine.

Why the ranking and the per-action gating exist at all is `MediaBackend`'s
docstring in `actions/cli/base.py`.
"""
from __future__ import annotations

import re
import shutil

from actions.cli.base import CommandMixin, CommandRunner, MediaBackend

_BUS_NAME = "org.freedesktop.DBus"
_BUS_PATH = "/org/freedesktop/DBus"
_OBJECT = "/org/mpris/MediaPlayer2"
_IFACE = "org.mpris.MediaPlayer2.Player"
_PROPERTIES = "org.freedesktop.DBus.Properties"

#: Which player a bare "pause" means. Playing beats paused beats stopped, and
#: a name that will not say sorts last.
_RANK = {"playing": 0, "paused": 1, "stopped": 2}
_UNKNOWN_RANK = len(_RANK)

#: action -> (MPRIS method, the property that says whether it will be obeyed).
#: Never CanControl — see `MediaBackend`.
_ACTIONS = {
    "play": ("Play", "CanPlay"),
    "pause": ("Pause", "CanPause"),
    "next": ("Next", "CanGoNext"),
    "previous": ("Previous", "CanGoPrevious"),
}

#: gdbus prints a variant, e.g. `(<'Playing'>,)` or `(<true>,)`.
_QUOTED = re.compile(r"'([^']*)'")
_TRUE = re.compile(r"<\s*true\s*>")
_MPRIS_NAME = re.compile(r"'(org\.mpris\.MediaPlayer2\.[^']+)'")


class MprisBackend(CommandMixin, MediaBackend):
    """Every MPRIS-speaking player on the session bus, as one transport."""

    #: The bus interface, not the binary: which of several players answered is
    #: reported separately, and `gdbus` is only how we got there.
    name = "mpris"

    def __init__(self, run: CommandRunner | None = None) -> None:
        super().__init__(run)
        #: The player settled on for this object's lifetime — see `target`.
        self._target: str | None = None

    def available(self) -> bool:
        return shutil.which("gdbus") is not None

    # -- the bus ----------------------------------------------------------

    def _names(self) -> list[str]:
        """Every MPRIS player currently on the session bus, in bus order.

        The bus is mostly not players; filtering on the well-known prefix is
        what keeps `:1.7` and the desktop's own services out of the running.
        """
        out = self._stdout([
            "gdbus", "call", "--session", "--dest", _BUS_NAME,
            "--object-path", _BUS_PATH,
            "--method", f"{_BUS_NAME}.ListNames"])
        return _MPRIS_NAME.findall(out) if out is not None else []

    def _get(self, player: str, prop: str) -> str | None:
        """One property off one player, as gdbus printed it, or None if the
        player did not answer — a player that has just vanished is the normal
        case here, not an exception."""
        return self._stdout([
            "gdbus", "call", "--session", "--dest", player,
            "--object-path", _OBJECT,
            "--method", f"{_PROPERTIES}.Get", _IFACE, prop])

    def _status_of(self, player: str) -> str | None:
        out = self._get(player, "PlaybackStatus")
        match = _QUOTED.search(out) if out is not None else None
        # Lowercased at the boundary so nothing downstream has to know that
        # MPRIS capitalises its state words.
        return match.group(1).lower() if match else None

    def _survey(self) -> list[tuple[str, str | None]]:
        """(player, status) for everything on the bus, best candidate first.

        The sort is stable, so among equally-ranked players the one the bus
        listed first still wins — ranking only overrides bus order where the
        state actually differs.
        """
        seen = [(name, self._status_of(name)) for name in self._names()]
        return sorted(seen, key=lambda pair: _RANK.get(pair[1], _UNKNOWN_RANK))

    def player(self) -> str | None:
        """Rank the bus and name the best candidate, surveying it afresh. The
        resolver behind `target`, which is what everything else asks."""
        ranked = self._survey()
        return ranked[0][0] if ranked else None

    # -- the contract -----------------------------------------------------

    def target(self) -> str | None:
        """The player this object commands: resolved once, then kept.

        Kept because the survey re-ranks — a pause drops Spotify below a
        browser tab that is still playing, and `MediaBackend.target` has what
        that costs. Nothing is cached while there is no player at all: one may
        appear between two questions.
        """
        if self._target is None:
            self._target = self.player()
        return self._target

    def status(self) -> str | None:
        player = self.target()
        return self._status_of(player) if player is not None else None

    def _can_on(self, player: str, action: str) -> bool:
        entry = _ACTIONS.get(action)
        if entry is None:
            return False  # an action we cannot spell is one we cannot promise
        out = self._get(player, entry[1])
        # Unreadable is not permission: None must not read as yes.
        return out is not None and _TRUE.search(out) is not None

    def can(self, action: str) -> bool:
        player = self.target()
        return player is not None and self._can_on(player, action)

    def _do(self, action: str) -> bool:
        """Ask the settled player whether it will obey, then ask it.

        Through `target`, never `player`: checking one player's capability
        before commanding another is the same bug as not checking at all.
        """
        player = self.target()
        if player is None or not self._can_on(player, action):
            return False
        method = _ACTIONS[action][0]
        return self._accepted([
            "gdbus", "call", "--session", "--dest", player,
            "--object-path", _OBJECT, "--method", f"{_IFACE}.{method}"])

    def play(self) -> bool:
        return self._do("play")

    def pause(self) -> bool:
        return self._do("pause")

    def next(self) -> bool:
        return self._do("next")

    def previous(self) -> bool:
        return self._do("previous")
