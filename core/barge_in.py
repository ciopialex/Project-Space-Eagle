"""The user talking over the eagle: hearing it without hearing the eagle itself.

While the eagle speaks, the microphone is not sent to Gemini. It cannot be:
the server's voice detection cannot tell the user from the eagle's own voice
coming back out of the speakers, so every reply would interrupt itself. What
was missing was any way to be interrupted at all -- the mic was simply ignored
until the eagle finished.

This listens locally instead. Every mic frame is compared with how loud the
speakers are right now. The part of the mic that is the eagle's own echo moves
with the speakers; a person talking does not. So the echo is measured as a
ratio -- mic level per unit of speaker level, learned continuously -- and a
frame is the user only when it is clearly louder than that ratio predicts.
A quarter of a second of that is a person, not a click.

With the system's echo cancellation active (core/echo_cancel.py) the learned
ratio falls toward zero and a normal speaking voice cuts in: 300 ms, measured
end to end against Gemini. Without it, the user has to be louder than the
eagle's echo in the room -- several times louder while it talks, or they get
in at its next pause -- which is how people interrupt each other anyway.

The frames heard while deciding are kept, and sent once it decides, so the
server hears the start of what the user said rather than its second word.
"""
from __future__ import annotations

import math
from collections import deque

from core.mic_vad import _rms

#: How much louder than the predicted echo a frame must be to be the user.
MARGIN = 2.2
#: Never trigger on anything quieter than this, whatever the echo says.
ABS_FLOOR = 400.0
#: The echo ratio assumed before anything is learned. Deliberately high: an
#: eagle that cannot be interrupted for its first two seconds beats one that
#: interrupts itself.
START_COUPLING = 0.8
#: Speakers quieter than this teach nothing about echo.
MIN_PLAYBACK = 150.0
#: The echo reaches the mic after the speakers made it -- device buffer plus
#: the room, measured in simulation as the difference between working and
#: interrupting itself 16 times in 20. So the mic is compared with the LOUDEST
#: the speakers have been over this window, which covers the lag and the tail.
WINDOW_MS = 400.0
#: The echo ratio is learned as a high percentile, not an average: the
#: threshold has to sit above nearly all of the eagle's own echo.
QUANTILE = 0.9
STEP = 0.05          # log-domain step of the percentile tracker
#: The tracker comes down slowly -- half a percent a frame -- which from the
#: cautious start takes most of a minute of the eagle talking. So the ratio is
#: also never allowed above this multiple of the loudest echo actually heard
#: in the last couple of seconds: two seconds into the first reply, it knows
#: the room.
SNAP = 1.5
SNAP_MS = 2000.0


class BargeInDetector:
    """Feed it mic frames while the eagle is speaking. Never raises.

    One thread only: everything here is called from the event loop.
    """

    def __init__(self, frame_ms: float = 64.0, need_ms: float = 256.0,
                 preroll_ms: float = 450.0, margin: float = MARGIN) -> None:
        self.need = max(1, round(need_ms / frame_ms))
        self.margin = margin
        self.coupling = START_COUPLING
        #: Recent verdicts, one per frame. Speech is syllables with gaps
        #: between them, so "N frames in a row" misses a real voice and
        #: "N of the last N + 2" does not.
        self._votes: deque[bool] = deque(maxlen=self.need + 2)
        #: The echo ratio of each of those frames, None where the speakers
        #: were too quiet to measure one.
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

        # A percentile tracker in the log domain: up by q when the frame is
        # above the estimate, down by (1 - q) when below, so it settles where
        # 90% of frames fall under it. A person interrupting is a short burst
        # that barely moves it; the echo is every frame of every reply.
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
        """It fired in the first breath of a reply: make sure that was not the room.

        Level alone cannot tell a person from the echo getting louder -- the
        headphones come out, the volume goes up. The tracker learns a jump
        like that only from the few frames before each cut, so in simulation
        the eagle cut ITSELF off in 5 of the next 12 replies after going from
        headphones to speakers, 11 of 12 in a loud room. Raising the ratio to
        what the firing frames showed takes that to one or two.

        Only when it fires this early, because that is where the room's cuts
        land: the echo is there from the first word. A person cutting in
        mid-reply teaches nothing, so their next interruption needs no more
        voice than this one did -- learning from every trigger made a second
        interruption within a second of the next reply miss, every time.
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
