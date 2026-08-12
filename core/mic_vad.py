"""A client-side voice-activity estimate, used only to timestamp turns.

The eagle does not gate on this. End-of-turn is the server's decision
(`AutomaticActivityDetection`), and moving that responsibility here would trade
a well-tuned remote VAD for a hand-rolled one — a bad trade for a latency
project. What the server does NOT do is tell us when it decided: there is no
server->client activity event. So "how long after I stopped talking did I hear
anything" — the number the user actually feels — has no observable start point
without this.

Consequences of that narrow job:

* Being slightly late to call `speech_start` costs nothing. Being early or late
  on `speech_end` shifts every latency number that hangs off it, so the
  hangover is generous and the noise floor adapts.
* It must never crash the mic path. Every failure returns None.
* It must be cheap. One RMS per 64ms frame, and only when tracing is on.

The threshold adapts rather than sitting at a constant, because a constant is
wrong in both directions: a noisy room reads as perpetual speech (putting
`speech_start` at the beginning of time), and a raised constant stops hearing a
quiet voice in a quiet room.
"""
from __future__ import annotations

import math

#: Speech is this many times louder than the learned room tone. Low enough for
#: a quiet voice, high enough that fan noise and a fridge do not qualify.
_SPEECH_OVER_FLOOR = 3.5

#: Absolute floor under the adaptive one. Without it, a truly silent input
#: drives the floor toward zero and any dither reads as speech.
_MIN_FLOOR = 40.0

#: How fast the room tone estimate moves. Rising slowly and falling quickly
#: means a burst of speech barely lifts the floor, while moving to a quieter
#: room is picked up within a second or so.
_FLOOR_RISE = 0.02
_FLOOR_FALL = 0.25


#: Resolved once. `_rms` runs on every outgoing mic frame, and an import
#: statement there is a sys.modules lookup ~16 times a second on the audio path.
try:
    import numpy as _np
except Exception:                       # numpy absent: the VAD simply no-ops
    _np = None


def _rms(frame: bytes) -> float | None:
    """Root-mean-square of little-endian int16 PCM, or None if unusable."""
    if not frame or len(frame) < 2 or _np is None:
        return None
    try:
        np = _np
        samples = np.frombuffer(frame[:len(frame) - (len(frame) % 2)],
                                dtype="<i2")
        if samples.size == 0:
            return None
        return float(np.sqrt(np.mean(samples.astype("f4") ** 2)))
    except Exception:
        return None


class SpeechDetector:
    """Emits 'start' / 'end' / None per frame. Never raises."""

    def __init__(self, rate: int = 16000, frame_samples: int = 1024,
                 hangover_ms: int = 300, min_speech_ms: int = 120,
                 warmup_ms: int = 1000, max_utterance_ms: int = 30000) -> None:
        self.rate = rate
        self.frame_ms = 1000.0 * frame_samples / max(rate, 1)
        #: How long after speech really stops this detector takes to say so.
        #: Callers timestamping the END of speech must subtract it, or they
        #: measure from later than the user experienced.
        self.hangover_ms = hangover_ms
        self.hangover_frames = max(1, round(hangover_ms / self.frame_ms))
        self.min_speech_frames = max(1, round(min_speech_ms / self.frame_ms))
        self.warmup_frames = max(1, round(warmup_ms / self.frame_ms))
        self.max_speech_frames = max(1, round(max_utterance_ms / self.frame_ms))
        self.noise_floor = _MIN_FLOOR
        self._in_speech = False
        self._loud_run = 0
        self._quiet_run = 0
        self._speech_run = 0
        self._warmup_seen = 0
        self._warmup_sum = 0.0

    def reset(self) -> None:
        """Forget the current utterance, keep what was learned about the room.

        Called on barge-in. The noise floor describes the room, not the turn;
        relearning it every turn would make the opening frames of each turn —
        exactly the ones that set `speech_start` — the least reliable.
        """
        self._in_speech = False
        self._loud_run = 0
        self._quiet_run = 0
        self._speech_run = 0

    def feed(self, frame) -> str | None:
        try:
            return self._feed(frame)
        except Exception:
            return None            # never take the mic path down with us

    def _feed(self, frame) -> str | None:
        level = _rms(frame)
        if level is None or math.isnan(level):
            return None

        # Warm-up: you cannot judge whether a frame is loud before knowing what
        # the room sounds like. Learning only from quiet frames is circular in a
        # room that is loud from the very first frame — nothing ever counts as
        # quiet, so the floor stays at its minimum and the noise reads as one
        # unending utterance. So the opening second trains on every frame and
        # emits nothing. A user already talking at startup pollutes that
        # estimate; the cost is a wrong floor for one second, which is cheaper
        # than a detector that never recovers.
        if self._warmup_seen < self.warmup_frames:
            self._warmup_seen += 1
            self._warmup_sum += level
            self.noise_floor = max(_MIN_FLOOR,
                                   self._warmup_sum / self._warmup_seen)
            return None

        loud = level > max(self.noise_floor * _SPEECH_OVER_FLOOR, _MIN_FLOOR)

        # A room that turns loud *after* warm-up (a fan, an air conditioner)
        # would otherwise hold the detector in speech forever, and a turn that
        # never ends produces no `speech_end` and therefore no latency number
        # at all. Cut it off and relearn rather than trust it indefinitely.
        if self._in_speech and self._speech_run >= self.max_speech_frames:
            self._in_speech = False
            self._speech_run = self._loud_run = self._quiet_run = 0
            self._warmup_seen = 0
            self._warmup_sum = 0.0
            return "end"

        # Learn the room only from frames that are not speech, so a long
        # sentence cannot slowly train the detector into ignoring the speaker.
        if not loud:
            rate = _FLOOR_FALL if level < self.noise_floor else _FLOOR_RISE
            self.noise_floor = max(
                _MIN_FLOOR, self.noise_floor + (level - self.noise_floor) * rate)

        if self._in_speech:
            self._speech_run += 1

        if loud:
            self._loud_run += 1
            self._quiet_run = 0
            # A door slam is one loud frame. Requiring a run of them stops a
            # transient starting a turn that then never ends.
            if not self._in_speech and self._loud_run >= self.min_speech_frames:
                self._in_speech = True
                self._speech_run = 0
                return "start"
            return None

        self._loud_run = 0
        if not self._in_speech:
            return None

        self._quiet_run += 1
        if self._quiet_run >= self.hangover_frames:
            self._in_speech = False
            self._quiet_run = 0
            self._speech_run = 0
            return "end"
        return None
