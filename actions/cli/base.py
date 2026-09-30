"""What every clified backend is, and the seam that makes it testable.

The command runner is injected rather than imported so a Windows or macOS
backend can be exercised from a Linux CI box. That is not test convenience — a
cross-platform contract nobody can run on one machine is a contract nobody
checks.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Callable, Protocol, Sequence


class CompletedProcessLike(Protocol):
    returncode: int
    stdout: str
    stderr: str


#: How a backend talks to the outside world. Defaults to `core.run_cmd.run_cmd`
#: in production; tests pass a fake that records calls and returns canned output.
CommandRunner = Callable[..., CompletedProcessLike]


def clamp(percent: int) -> int:
    """0-100, so a nonsense request reaches the OS as a legal one."""
    return max(0, min(100, int(percent)))


def _render(template: Sequence[str], value: object) -> list[str]:
    """argv from a template: only the part holding `{}` changes, and its format
    spec is what decides `0.65` versus `65%`."""
    return [part.format(value) for part in template]


class Backend(ABC):
    """One platform's way of doing one capability."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, e.g. 'wpctl'. Appears in ToolResult data so the
        user and the logs can tell which path actually ran."""

    @abstractmethod
    def available(self) -> bool:
        """True only when this backend can really run here. Checking a binary
        exists is enough; do not run the mutating command to find out."""


class AudioBackend(Backend):
    """The four operations every audio backend owes its interface.

    Every mutating method is paired with a reader, because the interface
    verifies what it changed. `set_muted` shipped without `get_muted` and three
    defects followed from that one omission: `set_mute` could not verify what
    it changed, `toggle_mute` became a permanent refusal still advertised in
    the model's action vocabulary, and nothing told the next platform's author
    that the pair was the point.
    """

    @property
    def tolerance(self) -> float:
        """How far a read-back may sit from the requested volume and still
        count as success.

        Per-backend because hardware differs: pactl and pycaw take any integer,
        while macOS quantizes to 16 steps ~6.25 apart, so asking for 47 reads
        back 44 and a hardcoded 2 blamed "another application" for a volume
        that did exactly what it was told. The default stays tight — a loose
        tolerance everywhere would hide a volume that never moved.
        """
        return 2

    @abstractmethod
    def get(self) -> int | None:
        """Current volume 0-100, or None when it cannot be read."""

    @abstractmethod
    def set(self, percent: int) -> bool:
        """Absolute volume. True only means the command was accepted; the
        caller verifies by reading back."""

    @abstractmethod
    def set_muted(self, muted: bool) -> bool:
        """Explicit state, never a toggle: you cannot report what you toggled
        to when you do not know what you toggled from."""

    @abstractmethod
    def get_muted(self) -> bool | None:
        """Current mute state, or None when it cannot be read.

        None means unknown and must stay distinguishable from False: reporting
        an unreadable state as "not muted" is the exact class of lie this
        contract exists to kill.
        """


class MediaBackend(Backend):
    """Transport control for whatever is playing: the four buttons, plus the
    two questions that have to be answered before pressing one.

    `status()` exists for the same reason every audio mutation is paired with a
    reader: "I sent Pause" is not a result. `can()` exists because of a thing
    measured on a real desktop — a stopped player sits on the bus advertising
    `CanControl: true` while `CanPlay`, `CanPause`, `CanGoNext` and
    `CanGoPrevious` are all false. Gating on the general flag means every
    command is accepted and nothing happens, which is precisely the lie this
    slice replaced keystrokes to stop telling. So each action asks about
    itself, and a player that says it cannot obey is never commanded.
    """

    def target(self) -> str | None:
        """What this backend commands — the player, app or session it has
        settled on — or None when there is nothing to command.

        Optional, and the default says nothing: the interface reads that as
        "this backend cannot tell you" and words its refusals to cover both an
        idle machine and a hung player, rather than picking the likelier.

        A backend that overrides this must resolve once and keep the answer for
        its own lifetime — one object serves one action. Every mutation is
        verified by reading `status()` back, so `status()` has to be about the
        thing that was just commanded. Re-resolving there lets a second player
        that is still playing answer for the one that was paused, and a pause
        that worked is reported as a pause that failed.
        """
        return None

    @abstractmethod
    def status(self) -> str | None:
        """`"playing"`, `"paused"` or `"stopped"` — lowercase, so callers do
        not each learn one platform's spelling — or None when it cannot be
        read. None means unknown and must stay distinguishable from
        `"stopped"`."""

    @abstractmethod
    def can(self, action: str) -> bool:
        """Whether this specific action would be obeyed right now.

        False for an action this backend does not know, for no player, and for
        a capability that cannot be read: an unreadable capability is not
        permission.
        """

    @abstractmethod
    def play(self) -> bool:
        """Resume. True only means the player accepted it; the interface
        verifies by reading `status()` back."""

    @abstractmethod
    def pause(self) -> bool:
        """Pause, explicitly — never a play/pause toggle, which cannot report
        the state it left behind."""

    @abstractmethod
    def next(self) -> bool:
        """Skip forward one track."""

    @abstractmethod
    def previous(self) -> bool:
        """Skip back one track."""


class CommandMixin:
    """The plumbing shared by every backend that is really a command line.

    Kept apart from any one capability's contract because it is the same three
    steps whether the program being run is `wpctl` or `gdbus`: run it, take the
    output if it succeeded, or just ask whether it succeeded. Mixed in rather
    than inherited from so a media backend does not arrive carrying volume
    methods it has no meaning for.
    """

    def __init__(self, run: CommandRunner | None = None) -> None:
        if run is None:
            from core.run_cmd import run_cmd
            run = run_cmd
        self._run = run

    def _exec(self, argv: Sequence[str]) -> CompletedProcessLike | None:
        # `text=True` is load-bearing: `core.run_cmd.run_cmd` is a Popen wrapper
        # with no text default, and every parser downstream raises TypeError on
        # bytes while fake-backed tests go on passing.
        try:
            return self._run(list(argv), capture_output=True, timeout=5,
                             text=True)
        except Exception:
            return None

    def _stdout(self, argv: Sequence[str]) -> str | None:
        """Output of a command that succeeded, or None."""
        proc = self._exec(argv)
        if proc is None or proc.returncode != 0:
            return None
        return proc.stdout or ""

    def _accepted(self, argv: Sequence[str]) -> bool:
        """A mutation only cares that the command ran and exited 0. Whether it
        did anything is the interface's business, by reading back."""
        return self._stdout(argv) is not None


class CommandBackend(CommandMixin, AudioBackend):
    """An audio backend that is one command-line program.

    wpctl, pactl, amixer and osascript differ only in the argv they take and
    the shape of the text they print, so subclasses declare that below as data
    and inherit the four operations. Written out by hand they drifted apart in
    exactly the places that are easy to get wrong: the mute reader, the clamp,
    `text=True`. A subclass also supplies `available()` — how a program is
    found is the platform module's business, not this one's.
    """

    name: str  #: also the name of the program itself
    read_volume: Sequence[str]
    volume_re: str  #: group(1) is the number
    volume_scale: float  #: 1 if the tool speaks percent, 100 if it speaks 0.0-1.0
    write_volume: Sequence[str]  #: argv, with a `{}` in the slot for the value
    read_mute: Sequence[str]
    mute_re: str  #: matched case-insensitively; group(1) is the state word
    muted_word: str  #: the group(1), lowercased, that means muted
    mute_absent: bool | None  #: answer when the pattern does not match at all
    write_mute: Sequence[str]
    mute_words: tuple[str, str]  #: what to pass to mute, and to unmute

    def get(self) -> int | None:
        out = self._stdout(self.read_volume)
        match = re.search(self.volume_re, out) if out is not None else None
        if not match:
            return None
        return int(round(float(match.group(1)) * self.volume_scale))

    def set(self, percent: int) -> bool:
        return self._accepted(_render(self.write_volume,
                                      clamp(percent) / self.volume_scale))

    def set_muted(self, muted: bool) -> bool:
        return self._accepted(
            _render(self.write_mute, self.mute_words[0 if muted else 1]))

    def get_muted(self) -> bool | None:
        out = self._stdout(self.read_mute)
        if out is None:
            return None
        match = re.search(self.mute_re, out, re.I)
        if not match:
            # Unknown where the tool always prints a switch, plainly unmuted
            # where its marker only appears when muted. Never guess.
            return self.mute_absent
        return match.group(1).lower() == self.muted_word
