"""Hearing the user talk over the eagle without hearing the eagle itself.

While the eagle speaks nothing is sent to Gemini (its VAD cannot tell the user
from the eagle's echo). This listens locally: each mic frame is compared with how
loud the speakers are now. The echo moves with the speakers; a person does not.
The echo ratio (mic level per unit of speaker level) is learned continuously, and
a frame is the user when clearly louder than that predicts. A quarter-second of
that is a person.

With echo cancellation (core/echo_cancel.py) a normal voice cuts in, about 300 ms
end to end. Without it the user must be louder than the eagle's echo.

Frames heard while deciding are sent once it decides, so the server hears the
first word.
"""
from __future__ import annotations

import math
from collections import deque

from core.mic_vad import _rms

# How much louder than the predicted echo a frame must be.
MARGIN = 2.2
# Never trigger below this.
ABS_FLOOR = 400.0
# The echo ratio before anything is learned. High: better not interruptible for two
# seconds than interrupting itself.
START_COUPLING = 0.8
# Speakers quieter than this teach nothing.
MIN_PLAYBACK = 150.0
# The echo arrives after the speakers made it, so compare with the loudest the speakers
# were over this window.
WINDOW_MS = 400.0
# Learned as a high percentile, so the threshold sits above nearly all of the echo.
QUANTILE = 0.9
STEP = 0.05          # log-domain step of the percentile tracker
# The tracker falls slowly, so the ratio is also capped at this multiple of the loudest
# recent echo.
SNAP = 1.5
SNAP_MS = 2000.0


class BargeInDetector:
    """Feed it mic frames while the eagle speaks. Never raises. Event loop only."""

    def __init__(self, frame_ms: float = 64.0, need_ms: float = 256.0,
                 preroll_ms: float = 450.0, margin: float = MARGIN) -> None:
        self.need = max(1, round(need_ms / frame_ms))
        self.margin = margin
        self.coupling = START_COUPLING
        # Recent verdicts: speech has gaps, so "N of the last N + 2", not N in a row.
        self._votes: deque[bool] = deque(maxlen=self.need + 2)
        # Each frame's echo ratio, None where the speakers were too quiet.
        self._vote_ratios: deque[float | None] = deque(maxlen=self.need + 2)
        self._frames = 0          # frames heard since this reply began
        self._run = 0
        self._preroll: deque[bytes] = deque(maxlen=max(1, round(preroll_ms / frame_ms)))
        self._played: deque[float] = deque(maxlen=max(1, round(WINDOW_MS / frame_ms)))
        self._ratios: deque[float] = deque(maxlen=max(1, round(SNAP_MS / frame_ms)))
        self.last_mic = 0.0
        self.last_threshold = 0.0

    def start_utterance(self) -> None:
        """The eagle started talking: forget the last attempt, keep the room."""
        self._run = 0
        self._frames = 0
        self._votes.clear()
        self._vote_ratios.clear()
        self._preroll.clear()
        self._played.clear()

    def feed(self, frame: bytes, playback: float, noise_floor: float = 0.0) -> bool:
        """True when this frame completes a stretch of the user talking."""
        try:
            return self._feed(frame, float(playback or 0.0), float(noise_floor or 0.0))
        except Exception:
            return False

    def _feed(self, frame: bytes, playback: float, noise_floor: float) -> bool:
        self._preroll.append(frame)
        self._played.append(playback)
        self._frames += 1
        level = _rms(frame)
        if level is None:
            return False
        reference = max(self._played)
        echo = self.coupling * reference
        threshold = max(ABS_FLOOR, noise_floor * 4.0, echo * self.margin)
        self.last_mic, self.last_threshold = level, threshold

        self._votes.append(level > threshold)
        self._vote_ratios.append(level / reference if reference > MIN_PLAYBACK else None)
        self._run = sum(self._votes)

        # A log-domain percentile tracker that settles where 90% of frames fall under
        # it. A short interruption barely moves it.
        if reference > MIN_PLAYBACK:
            ratio = level / reference
            up = ratio > self.coupling
            self.coupling *= math.exp(STEP * (QUANTILE if up else -(1.0 - QUANTILE)))
            self._ratios.append(ratio)
            if len(self._ratios) == self._ratios.maxlen:
                self.coupling = min(self.coupling, max(self._ratios) * SNAP)
            self.coupling = min(max(self.coupling, 0.005), 4.0)

        if self._run < self.need:
            return False
        if self._frames <= self._votes.maxlen:
            self._learn_from_early_trigger()
        return True

    def _learn_from_early_trigger(self) -> None:
        """Fired in the first breath of a reply: raise the ratio to what the firing
        frames showed, in case it was the room (speakers instead of headphones).
        Only then: an interruption mid-reply teaches nothing.
        """
        hot = [r for v, r in zip(self._votes, self._vote_ratios) if v and r is not None]
        if hot:
            self.coupling = min(max(self.coupling, max(hot) / self.margin), 4.0)

    def take_preroll(self) -> list[bytes]:
        """The frames heard while deciding, oldest first, and forget them."""
        frames = list(self._preroll)
        self._preroll.clear()
        self._votes.clear()
        self._vote_ratios.clear()
        self._run = 0
        return frames

    def describe(self) -> str:
        return (f"mic {self.last_mic:.0f} over threshold {self.last_threshold:.0f}, "
                f"echo ratio {self.coupling:.2f}")
