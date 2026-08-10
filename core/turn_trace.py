"""Where a voice turn actually spends its time.

Written before any latency change, deliberately: the assumption that the model
is the slow part is the one that costs the most time to act on and is usually
wrong. The eagle's own telemetry already contradicts it — `mic_q=0` and
`mic_drops=0` on every sampled tick, so capture is healthy, while `play_q`
reached 348 frames (~17s of speech queued). Neither number is a model latency.

This records the timeline the *user* experiences, not the one the code finds
convenient. The gap between "you stopped talking" and "you heard something" is
made of several independent delays that get blamed on each other:

    speech_start -> speech_end      how long the user talked. Context, not a
                                    cost - but a turn's other numbers cannot
                                    be read without it.
    speech_end   -> first_token     the server's silence window (a fixed cost
                                    paid on EVERY turn before any work starts)
                                    plus network and model prefill.
    first_token  -> first_audio     our playback path: buffering, format
                                    conversion, device open.
    speech_end   -> first_tool      how long until something real happens,
                                    which is the number that actually matters
                                    for a mission.
    first_audio  -> complete        how long the reply talked FOR. The eagle's
                                    own telemetry showed 17s queued; a reply
                                    nobody wanted to sit through is a latency
                                    problem wearing a verbosity costume.

Marks are recorded from several threads (mic, receive loop, playback, tool
executor), so writes are locked. Cost when disabled is one attribute read.

Nothing here changes behaviour. It only makes the next decision an informed
one, and stays behind the diagnostics flag afterwards so a regression can be
caught rather than argued about.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Callable

#: The marks that make up a turn, in the order they should occur. Ordering is
#: documentation, not enforcement — a turn that skips marks (a conversational
#: reply never reaches `first_tool`) is normal and must not distort the report.
MARKS = (
    # Set BEFORE speech_end when it fires: the hypothesis is formed while the
    # user is still talking, which is the only point early enough to matter.
    "speculated",        # safe work began on a prediction
    "speech_start",      # user began speaking (client-side VAD onset)
    "speech_end",        # user stopped speaking (client-side VAD offset)
    "first_token",       # first byte of any kind back from the server
    "first_audio",       # first sample actually handed to the speaker
    "first_tool",        # first tool call started executing
    "complete",          # server declared turn_complete
)

#: The segments worth reporting, as (label, from_mark, to_mark). These are the
#: independent delays — reporting only a total invites blaming whichever
#: component is most recently suspected.
#:
#: There is deliberately no `vad` segment. End-of-turn detection is server-side
#: (`AutomaticActivityDetection`) and the server sends no event when it fires:
#: `ActivityStart`/`ActivityEnd` in the SDK are client->server messages for
#: MANUAL VAD, not notifications we receive. So the silence window is not
#: separately observable, and a mark for it would sit permanently unset while
#: looking like a measurement. It is folded into `response` instead, where the
#: configured floor (see VAD_FLOOR_MS) accounts for a known part of the total.
SEGMENTS = (
    ("speech",   "speech_start", "speech_end"),   # how long the user talked
    ("response", "speech_end",   "first_token"),  # VAD window + network + prefill
    ("audio",    "first_token",  "first_audio"),  # our own playback path
    ("tool",     "speech_end",   "first_tool"),
    # Negative on purpose when speculation worked: the browser started BEFORE
    # the user stopped talking. That number is the whole point of the layer.
    ("headstart", "speculated",  "speech_end"),
    ("spoken",   "first_audio",  "complete"),     # how long the reply talked FOR
)

#: The two numbers a user would recognise. `to_voice` is "how long after I
#: stopped talking did I hear anything"; `to_action` is "how long until it
#: started doing the thing".
HEADLINES = (
    ("to_voice",  "speech_end", "first_audio"),
    ("to_action", "speech_end", "first_tool"),
)

#: The server's silence window before it will admit the user has stopped
#: talking. Not measured — configured, and therefore a known constant floor
#: under every `response` number. Recorded here so a reader can subtract it
#: instead of mistaking a config choice for a slow model.
VAD_FLOOR_MS = 550


def _enabled_by_default() -> bool:
    """On unless explicitly switched off.

    This was opt-in behind `AETHELARK_TRACE=1` for weeks, and in that time it
    was never once switched on — so every conversation about voice latency was
    conducted on guesses while the instrument that answers it sat inert. Four
    separate sessions were reported as "really slow" and not one produced a
    `[Trace]` line.

    A diagnostic the user must remember to enable is a diagnostic that does not
    exist. It is one line per turn and a handful of timestamps, so it earns its
    place permanently; `AETHELARK_TRACE=0` turns it off for a clean demo.
    """
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
        """Record `name` at now. First write wins.

        First-write-wins matters: `first_token` and `first_audio` fire once per
        turn conceptually but are called from loops that run per frame. A last
        -write-wins mark would silently report the *end* of the response as its
        beginning, which is the opposite of what is being measured.
        """
        if not self.enabled:
            return
        now = time.monotonic()
        with self._lock:
            self._marks.setdefault(name, now)

    def at(self, name: str) -> float | None:
        with self._lock:
            return self._marks.get(name)

    def delta_ms(self, start: str, end: str) -> float | None:
        """Milliseconds between two marks, or None if either never happened."""
        with self._lock:
            a, b = self._marks.get(start), self._marks.get(end)
        return None if a is None or b is None else (b - a) * 1000.0

    def summary(self) -> str:
        """One compact line. Absent segments are omitted, never zero-filled.

        Omitting rather than zero-filling is the point: a turn with no tool call
        has no tool latency, and printing `tool=0ms` would put a zero into an
        average that then hides the turns where it was seconds.
        """
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
    """Keeps the last N turns so medians and tails can be read off directly.

    A single turn's numbers are noise — the brief asks for median *and* tail,
    because a pipeline that is usually fast and occasionally terrible is a
    worse experience than one that is consistently middling, and an average
    hides exactly that.
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
            # Tail, not mean: the brief asks for slower-tail latency because
            # that is what the user remembers.
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
