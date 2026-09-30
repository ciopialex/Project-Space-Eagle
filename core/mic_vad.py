"""A client-side voice-activity estimate. Two jobs, and neither gates sending.

End-of-turn is the server's decision (`AutomaticActivityDetection`), and what
Gemini hears is every mic frame while the eagle is not talking. This detector
only answers "is a person talking right now" for two things the server does
not tell us:

* The island's hearing state (`_hearing_vad` in main.py, always on). The
  ambient island used to open to "listening" for keystrokes, a chair squeak or
  a door -- anything loud. It must react to a human voice and nothing else.
  Hearing speech is also what lets a spoken request act after an unprompted
  turn, so a false start here is not only cosmetic.
* The latency tracer (`_vad`, only when tracing is on): "how long after I
  stopped talking did I hear anything" has no observable start point without
  a local estimate of when speech ended.

Two engines behind one interface:

* Silero VAD, a small neural network run through ONNX Runtime, when the model
  file and `onnxruntime` are both present. It scores each 32 ms of audio for
  how much it sounds like a human voice, so a keystroke as loud as speech does
  not qualify. Cost measured 2026-09-24: ~0.1 ms per 32 ms chunk, one thread,
  ~0.3% of a core per detector.
* The adaptive-RMS detector below, when either is missing. It is loudness
  against a learned room tone, so it cannot tell a voice from a thud -- which
  is the behaviour Silero replaced. Kept because a missing model must leave
  the mic path working, not take it down.

Neither is faster than the frame: main.py delivers 1024 samples (64 ms) at a
time and `feed` answers once per frame, so an onset is reported up to 64 ms
after it began however the frame is split inside.

Nothing here removes sound the computer itself plays. Music or a video from
this machine sounds like a voice because it often is one; subtracting it is
echo cancellation's job (core/echo_cancel.py), which works on the signal before
it reaches this code. The eagle's own voice is kept out by main.py, which stops
feeding the mic while it speaks and for its speaker's tail after.

It must never crash the mic path: every failure returns None.
"""
from __future__ import annotations

import collections
import math
from pathlib import Path

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

#: Silero VAD v6.2, byte-identical to upstream snakers4/silero-vad commit
#: bfdc019 ("add v6.2 model", 2025-11-06), sha256 1a153a22f4509e29...
#: MIT; see SILERO_LICENSE.txt beside it.
_MODEL_PATH = (Path(__file__).resolve().parent.parent
               / "assets" / "models" / "silero_vad.onnx")

#: The model only runs at 16 kHz, on exactly 512 new samples (32 ms) plus the
#: last 64 samples of the previous chunk as context.
_SILERO_RATE = 16000
_SILERO_CHUNK = 512
_SILERO_CONTEXT = 64
_SILERO_CHUNK_MS = 1000.0 * _SILERO_CHUNK / _SILERO_RATE

#: Resolved once. `_rms` runs on every outgoing mic frame, and an import
#: statement there is a sys.modules lookup ~16 times a second on the audio path.
try:
    import numpy as _np
except Exception:                       # numpy absent: the VAD simply no-ops
    _np = None

try:
    import onnxruntime as _ort
except Exception:                       # absent: the RMS detector runs instead
    _ort = None


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
    """Emits 'start' / 'end' / None per frame. Never raises.

    Silero when it loads, the adaptive-RMS detector when it does not; `engine`
    says which. `noise_floor` is the room's loudness in RMS either way, because
    barge-in (core/barge_in.py) compares against it.
    """

    def __init__(self, rate: int = 16000, frame_samples: int = 1024,
                 hangover_ms: int = 450, min_speech_ms: int = 120,
                 warmup_ms: int = 1000, max_utterance_ms: int = 30000,
                 model_path: Path | str | None = None,
                 threshold: float = 0.22, neg_threshold: float = 0.15,
                 preroll_ms: int = 128) -> None:
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

        # Pre-roll ring buffer and keystroke counter for zero-latency acoustic shielding
        self.preroll_ms = preroll_ms
        self.preroll_frames = max(1, round(preroll_ms / self.frame_ms))
        self._preroll: collections.deque[bytes] = collections.deque(maxlen=self.preroll_frames)
        self.blocked_keystrokes = 0

        # Silero. A chunk scoring at or above `threshold` is voice; speech ends
        # only after `hangover_ms` of chunks below `neg_threshold`. The gap
        # between the two keeps a voice that dips between words from flickering
        # the island. Set by hand against a live island on 2026-09-24 --
        # keystrokes, chair, room noise, talking over music -- not derived.
        self.threshold = threshold
        self.neg_threshold = neg_threshold
        self._hangover_chunks = max(1, round(hangover_ms / _SILERO_CHUNK_MS))
        self._max_voice_chunks = max(1, round(max_utterance_ms / _SILERO_CHUNK_MS))
        self._session = None
        self._load_silero(model_path)
        self.reset()

    @property
    def engine(self) -> str:
        return "silero" if self._session is not None else "rms"

    def _load_silero(self, model_path: Path | str | None) -> None:
        if _ort is None or _np is None or self.rate != _SILERO_RATE:
            return
        target = Path(model_path) if model_path is not None else _MODEL_PATH
        if not target.is_file():
            return
        try:
            opts = _ort.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1
            self._session = _ort.InferenceSession(
                str(target), sess_options=opts,
                providers=["CPUExecutionProvider"])
        except Exception:
            self._session = None

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
        self._voice = False          # Silero: inside an utterance (incl. hangover)
        self._hang = None            # Silero: chunks of hangover left, None while talking
        self._voice_chunks = 0       # Silero: length of the current utterance
        self._armed = True           # Silero: False after a cutoff, until a quiet chunk
        self._preroll.clear()
        if self._session is not None:
            self._state = _np.zeros((2, 1, 128), dtype=_np.float32)
            self._context = _np.zeros((1, _SILERO_CONTEXT), dtype=_np.float32)
            self._sr = _np.array(_SILERO_RATE, dtype=_np.int64)

    def take_preroll(self) -> list[bytes]:
        """Frames heard immediately before speech start, oldest first."""
        frames = list(self._preroll)
        self._preroll.clear()
        return frames

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
        # than a detector that never recovers. Silero needs no floor, but
        # barge-in reads this one whichever engine runs.
        if self._warmup_seen < self.warmup_frames:
            self._warmup_seen += 1
            self._warmup_sum += level
            self.noise_floor = max(_MIN_FLOOR,
                                   self._warmup_sum / self._warmup_seen)
            self._preroll.append(frame)
            return None

        if self._session is not None:
            event = self._feed_silero(frame, level)
        else:
            event = self._feed_rms(level)

        if event == "start":
            return event

        if not self._in_speech and not self._voice:
            # Idle or typing: check for acoustic keystroke impulse transients.
            # Mechanical switches produce sharp Dirac shocks with crest factor > 5.0.
            is_transient = False
            if _np is not None:
                samples = _np.frombuffer(frame[:len(frame) - (len(frame) % 2)], dtype="<i2")
                if samples.size > 0:
                    raw_x = samples.astype(_np.float32)
                    peak = float(_np.max(_np.abs(raw_x)))
                    crest = peak / (level + 1e-6)
                    if crest > 5.0 and level > 180.0:
                        is_transient = True
                        self.blocked_keystrokes += 1
            if is_transient:
                self._preroll.append(b"\x00" * len(frame))
            else:
                self._preroll.append(frame)

        return event

    def _learn_floor(self, level: float) -> None:
        rate = _FLOOR_FALL if level < self.noise_floor else _FLOOR_RISE
        self.noise_floor = max(
            _MIN_FLOOR, self.noise_floor + (level - self.noise_floor) * rate)

    def _feed_silero(self, frame, level: float) -> str | None:
        # The room is whatever is not a voice: a fan, a keyboard, music with no
        # singing. Judged by the model rather than by loudness, which is the
        # point -- a loud keystroke is room, not speaker.
        if not self._voice:
            self._learn_floor(level)

        samples = _np.frombuffer(frame[:len(frame) - (len(frame) % 2)],
                                 dtype="<i2")
        was = self._voice
        for offset in range(0, samples.size, _SILERO_CHUNK):
            chunk = samples[offset:offset + _SILERO_CHUNK]
            if chunk.size < _SILERO_CHUNK:
                chunk = _np.pad(chunk, (0, _SILERO_CHUNK - chunk.size))
            self._step(self._score(chunk))

        # Report the change across the whole frame, not each chunk's: a voice
        # that ended and restarted inside one 64 ms frame never stopped.
        self._in_speech = self._voice
        if self._voice and not was:
            return "start"
        if was and not self._voice:
            return "end"
        return None

    def _score(self, chunk) -> float:
        x = chunk.astype(_np.float32).reshape(1, -1) / 32768.0
        x = _np.concatenate([self._context, x], axis=1)
        out, self._state = self._session.run(
            None, {"input": x, "state": self._state, "sr": self._sr})
        self._context = x[:, -_SILERO_CONTEXT:]
        return float(out[0][0])

    def _step(self, prob: float) -> None:
        if not self._voice:
            if not self._armed:
                # After a cutoff, a chunk that is not voice must pass before
                # the next utterance can start, or the same sound restarts one
                # 32 ms later and the cutoff achieved nothing.
                self._armed = prob < self.neg_threshold
                return
            if prob >= self.threshold:
                self._voice, self._hang, self._voice_chunks = True, None, 0
            return

        self._voice_chunks += 1
        # A sound that never stops -- vocals in music with no echo cancelling,
        # a TV -- would hold the island on "hearing" forever and leave the
        # tracer with no speech_end. Same rule as the RMS detector: cut it off.
        if self._voice_chunks >= self._max_voice_chunks:
            self._voice, self._armed = False, False
            return
        if self._hang is None:
            # Talking. Only a clearly-not-voice chunk starts the hangover; one
            # between the thresholds is a voice dipping between words.
            if prob < self.neg_threshold:
                self._hang = self._hangover_chunks
        elif prob >= self.threshold:
            self._hang = None                 # they carried on
        else:
            self._hang -= 1
            if self._hang <= 0:
                self._voice = False

    def _feed_rms(self, level: float) -> str | None:
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
            self._learn_floor(level)

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
