"""Where a voice turn spends its time, as the user experiences it:

    speech_start -> speech_end    how long the user talked (context)
    speech_end   -> first_token   server silence window + network + prefill
    first_token  -> first_audio   our playback path
    speech_end   -> first_tool    until something real happens
    first_audio  -> complete      how long the reply talked for

Marks come from several threads, so writes are locked. Disabled, a mark costs one
attribute read. Changes no behaviour.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Callable

# The marks of a turn, in their usual order. A turn may skip marks.
MARKS = (
    # Set before speech_end: the hypothesis forms while the user is still talking.
    "speculated",        # safe work began on a prediction
    "speech_start",      # user began speaking (client-side VAD onset)
    "speech_end",        # user stopped speaking (client-side VAD offset)
    "first_token",       # first byte of any kind back from the server
    "first_audio",       # first sample actually handed to the speaker
    "first_tool",        # first tool call started executing
    "complete",          # server declared turn_complete
)

# Reported segments as (label, from_mark, to_mark). No `vad` segment: the server sends
# no event when its end-of-turn fires, so the silence window is folded into `response`
# (see VAD_FLOOR_MS).
SEGMENTS = (
    ("speech",   "speech_start", "speech_end"),   # how long the user talked
    ("response", "speech_end",   "first_token"),  # VAD window + network + prefill
    ("audio",    "first_token",  "first_audio"),  # our own playback path
    ("tool",     "speech_end",   "first_tool"),
    # Negative when speculation worked: the browser started before the user stopped
    # talking.
    ("headstart", "speculated",  "speech_end"),
    ("spoken",   "first_audio",  "complete"),     # how long the reply talked FOR
)

# The two numbers a user would recognise: to_voice (until I heard something) and
# to_action (until it started doing the thing).
HEADLINES = (
    ("to_voice",  "speech_end", "first_audio"),
    ("to_action", "speech_end", "first_tool"),
)

# The server's configured silence window: a known floor under every `response`.
VAD_FLOOR_MS = 550


def _enabled_by_default() -> bool:
    """On unless AETHELARK_TRACE=0: one line per turn."""
    raw = os.environ.get("AETHELARK_TRACE", "").strip().lower()
    return raw not in ("0", "false", "no", "off")


class TurnTrace:
    """Timestamps for one voice turn. Cheap, thread-safe, and inert when off."""

    __slots__ = ("_marks", "_lock", "enabled", "turn")

    def __init__(self, turn: int = 0, enabled: bool | None = None) -> None:
        self.enabled = _enabled_by_default() if enabled is None else enabled
        self.turn = turn
        self._marks: dict[str, float] = {}
        self._lock = threading.Lock()

    def mark(self, name: str) -> None:
        """Record `name` now. First write wins: some marks are called from per-frame
        loops.
        """
        if not self.enabled:
            return
        now = time.monotonic()
        with self._lock:
            self._marks.setdefault(name, now)

    def mark_at(self, name: str, when: float) -> None:
        """Record `name` at `when`, for marks detected after the fact (the VAD's
        hangover). First write wins.
        """
        if not self.enabled:
            return
        with self._lock:
            self._marks.setdefault(name, when)

    def at(self, name: str) -> float | None:
        with self._lock:
            return self._marks.get(name)

    def delta_ms(self, start: str, end: str) -> float | None:
        """Milliseconds between two marks, or None if either never happened."""
        with self._lock:
            a, b = self._marks.get(start), self._marks.get(end)
        return None if a is None or b is None else (b - a) * 1000.0

    def summary(self) -> str:
        """One compact line. Absent segments are omitted, never zero-filled."""
        if not self.enabled:
            return ""
        parts = []
        for label, start, end in HEADLINES:
            value = self.delta_ms(start, end)
            if value is not None:
                parts.append(f"{label}={value:.0f}ms")
        for label, start, end in SEGMENTS:
            value = self.delta_ms(start, end)
            if value is not None:
                parts.append(f"{label}={value:.0f}ms")
        if not parts:
            return ""
        return f"[Trace] turn={self.turn} " + " ".join(parts)


class TraceLog:
    """The last N turns, for medians and tails: an average hides the occasional
    terrible turn.
    """

    def __init__(self, keep: int = 50,
                 emit: Callable[[str], None] | None = None) -> None:
        self._turns: list[TurnTrace] = []
        self._keep = keep
        self._emit = emit or print
        self._lock = threading.Lock()

    def finish(self, trace: TurnTrace) -> None:
        if not trace.enabled:
            return
        line = trace.summary()
        if line:
            self._emit(line)
        with self._lock:
            self._turns.append(trace)
            del self._turns[:-self._keep]

    def stats(self, label: str) -> dict | None:
        """Median and p90 for one segment across the kept turns."""
        pair = {name: (a, b) for name, a, b in SEGMENTS + HEADLINES}.get(label)
        if pair is None:
            return None
        start, end = pair
        with self._lock:
            values = sorted(
                v for v in (t.delta_ms(start, end) for t in self._turns)
                if v is not None)
        if not values:
            return None
        return {
            "n": len(values),
            "median_ms": values[len(values) // 2],
            # Tail, not mean: the slow turns are what the user remembers.
            "p90_ms": values[min(len(values) - 1, int(len(values) * 0.9))],
            "max_ms": values[-1],
        }

    def report(self) -> str:
        rows = []
        for label, _s, _e in HEADLINES + SEGMENTS:
            s = self.stats(label)
            if s:
                rows.append(f"  {label:10} n={s['n']:<4} "
                            f"median={s['median_ms']:7.0f}ms  "
                            f"p90={s['p90_ms']:7.0f}ms  "
                            f"max={s['max_ms']:7.0f}ms")
        return "Voice latency (last %d turns)\n%s" % (
            len(self._turns), "\n".join(rows) or "  (no turns recorded)")
