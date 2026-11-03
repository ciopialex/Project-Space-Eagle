"""What every native backend is. The command runner is injected, so Windows and macOS
backends can be exercised on a Linux CI box.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Callable, Protocol, Sequence


class CompletedProcessLike(Protocol):
    returncode: int
    stdout: str
    stderr: str


# How a backend talks to the outside world: core.run_cmd.run_cmd in production, a
# recording fake in tests.
CommandRunner = Callable[..., CompletedProcessLike]


def clamp(percent: int) -> int:
    """0-100, so a nonsense request reaches the OS as a legal one."""
    return max(0, min(100, int(percent)))


def _render(template: Sequence[str], value: object) -> list[str]:
    """argv from a template: only the element holding `{}` changes, and its format
    spec decides 0.65 vs 65%.
    """
    return [part.format(value) for part in template]


class Backend(ABC):
    """One platform's way of doing one capability."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier (e.g. 'wpctl'), reported in ToolResult data so the path
        that ran is visible.
        """

    @abstractmethod
    def available(self) -> bool:
        """True only when this backend can really run here. Never run the mutating
        command to find out.
        """


class AudioBackend(Backend):
    """The four operations every audio backend owes. Every mutation is paired with a
    reader, because the interface verifies what it changed.
    """

    @property
    def tolerance(self) -> float:
        """How far a read-back may sit from the requested volume and still count. Per
        backend: macOS quantises to 16 steps. Tight by default.
        """
        return 2

    @abstractmethod
    def get(self) -> int | None:
        """Current volume 0-100, or None when it cannot be read."""

    @abstractmethod
    def set(self, percent: int) -> bool:
        """Absolute volume. True means the command was accepted; the caller reads
        back.
        """

    @abstractmethod
    def set_muted(self, muted: bool) -> bool:
        """Explicit state, never a toggle."""

    @abstractmethod
    def get_muted(self) -> bool | None:
        """Current mute state, or None when unreadable. None must stay distinct from
        False.
        """


class MediaBackend(Backend):
    """Transport control: play, pause, next, previous, plus status() and can(). A
    stopped player can advertise CanControl while every specific Can* is false, so
    each action asks about itself and a player that cannot obey is never
    commanded.
    """

    def target(self) -> str | None:
        """What this backend commands (player, app, session), or None. Resolve once
        per object: status() must describe the thing just commanded.
        """
        return None

    @abstractmethod
    def status(self) -> str | None:
        """"playing", "paused" or "stopped" (lowercase), or None when unreadable.
        None stays distinct from "stopped".
        """

    @abstractmethod
    def can(self, action: str) -> bool:
        """Whether this action would be obeyed right now. False for an unknown
        action, no player, or an unreadable capability.
        """

    @abstractmethod
    def play(self) -> bool:
        """Resume. True means accepted; the interface reads status() back."""

    @abstractmethod
    def pause(self) -> bool:
        """Pause explicitly, never a toggle."""

    @abstractmethod
    def next(self) -> bool:
        """Skip forward one track."""

    @abstractmethod
    def previous(self) -> bool:
        """Skip back one track."""


class CommandMixin:
    """Plumbing shared by every backend that is a command line: run it, take its
    output, or ask whether it succeeded. A mixin, so media backends do not inherit
    volume methods.
    """

    def __init__(self, run: CommandRunner | None = None) -> None:
        if run is None:
            from core.run_cmd import run_cmd
            run = run_cmd
        self._run = run

    def _exec(self, argv: Sequence[str]) -> CompletedProcessLike | None:
        # text=True is required: run_cmd has no text default and every parser raises on
        # bytes.
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
        """A mutation only needs exit 0; the interface verifies by reading back."""
        return self._stdout(argv) is not None


class CommandBackend(CommandMixin, AudioBackend):
    """An audio backend that is one command-line program: subclasses declare argv and
    output patterns as data and inherit the four operations. They also supply
    available().
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
            # Unknown where the tool always prints a switch; unmuted where its marker
            # appears only when muted. Never guess.
            return self.mute_absent
        return match.group(1).lower() == self.muted_word
