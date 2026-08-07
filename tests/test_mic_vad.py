"""The VAD only has to be good enough to timestamp a turn, never to gate one.

Gating is the server's job (`AutomaticActivityDetection`). This exists so the
trace can answer "how long after I stopped talking did I hear anything", which
is unanswerable otherwise: the server sends no event when its silence window
fires. A wrong `speech_end` here corrupts every latency number that depends on
it, so the failure modes worth testing are the ones that shift the timestamp,
not the ones that misclassify a frame.
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.mic_vad import SpeechDetector  # noqa: E402

FRAME = 1024          # samples, matching CHUNK_SIZE
RATE = 16000


def pcm(amplitude: int, samples: int = FRAME) -> bytes:
    """A frame at a constant amplitude. Alternating sign so the mean is ~0 and
    RMS reflects amplitude rather than DC offset."""
    return struct.pack(f"<{samples}h",
                       *[amplitude if i % 2 else -amplitude
                         for i in range(samples)])


def feed_all(det, frames):
    return [det.feed(f) for f in frames]


def test_silence_alone_never_starts_a_turn():
    det = SpeechDetector(rate=RATE)
    assert set(feed_all(det, [pcm(0)] * 40)) == {None}


def test_speech_after_silence_reports_a_start():
    det = SpeechDetector(rate=RATE)
    feed_all(det, [pcm(30)] * 20)                 # room tone, sets the floor
    events = feed_all(det, [pcm(6000)] * 5)
    assert "start" in events


def test_end_is_reported_only_after_the_hangover(monkeypatch):
    """A pause mid-sentence must not end the turn. If it did, `speech_end`
    would land on the first breath and every latency number after it would be
    inflated by the rest of the sentence."""
    det = SpeechDetector(rate=RATE, hangover_ms=300)
    feed_all(det, [pcm(30)] * 20)
    feed_all(det, [pcm(6000)] * 5)                # talking

    frame_ms = 1000 * FRAME / RATE                # 64ms
    short_pause = int(200 // frame_ms)            # under the hangover
    assert "end" not in feed_all(det, [pcm(20)] * short_pause)

    feed_all(det, [pcm(6000)] * 3)                # talking again
    long_pause = int(300 // frame_ms) + 2
    assert "end" in feed_all(det, [pcm(20)] * long_pause)


def test_a_loud_room_does_not_read_as_perpetual_speech():
    """A fixed threshold makes a noisy room look like someone talking forever,
    which would put `speech_start` at the beginning of time."""
    det = SpeechDetector(rate=RATE)
    assert set(feed_all(det, [pcm(900)] * 60)) == {None}


def test_a_quiet_room_still_hears_a_quiet_voice():
    """The mirror of the above: adapting to the floor must not raise it so far
    that normal speech stops registering."""
    det = SpeechDetector(rate=RATE)
    feed_all(det, [pcm(5)] * 30)
    assert "start" in feed_all(det, [pcm(700)] * 5)


def test_start_then_end_pairs_exactly_once_per_utterance():
    det = SpeechDetector(rate=RATE, hangover_ms=200)
    feed_all(det, [pcm(30)] * 20)
    events = [e for e in
              feed_all(det, [pcm(6000)] * 8 + [pcm(20)] * 12) if e]
    assert events == ["start", "end"]


def test_reset_forgets_the_utterance_but_keeps_the_room():
    """Called on barge-in. The learned noise floor is a property of the room,
    not the turn — relearning it from scratch each turn would make the first
    frames of every turn unreliable."""
    det = SpeechDetector(rate=RATE)
    feed_all(det, [pcm(30)] * 25)
    floor = det.noise_floor
    feed_all(det, [pcm(6000)] * 5)
    det.reset()
    assert det.noise_floor == floor
    assert "start" in feed_all(det, [pcm(6000)] * 5)


def test_a_short_burst_is_not_an_utterance():
    """A door slam or a key press is one loud frame. Treating it as speech
    would start a turn that never ends."""
    det = SpeechDetector(rate=RATE, min_speech_ms=150)
    feed_all(det, [pcm(30)] * 20)
    assert "start" not in feed_all(det, [pcm(9000)] * 1 + [pcm(25)] * 10)


def test_malformed_frames_never_raise():
    """This runs on audio data. It must degrade, not crash the mic path."""
    det = SpeechDetector(rate=RATE)
    for bad in (b"", b"\x01", None):
        assert det.feed(bad) is None


def test_warmup_emits_nothing_while_it_learns_the_room():
    """The opening second is spent measuring, not judging. Events emitted
    before the floor is known are guesses against a default."""
    det = SpeechDetector(rate=RATE, warmup_ms=1000)
    warm = det.warmup_frames
    assert set(feed_all(det, [pcm(6000)] * warm)) == {None}


def test_warmup_is_what_saves_the_loud_room():
    """Pins the fix, not just the symptom: with no warm-up the detector has
    only its default floor to judge against, and a loud room reads as speech
    from frame one — the state it can never learn its way out of, because it
    only learns from frames it has already called quiet."""
    noisy = [pcm(900)] * 60

    # The pre-fix state, reconstructed: judging enabled, floor still at its
    # default. `warmup_ms=0` does not reproduce it — the frame count floors at
    # 1, and one frame is already enough to learn 900.
    unwarmed = SpeechDetector(rate=RATE)
    unwarmed._warmup_seen = unwarmed.warmup_frames
    assert unwarmed.noise_floor == 40.0, "precondition: floor is the default"
    assert "start" in feed_all(unwarmed, noisy), (
        "expected the old behaviour to misfire; if this stops failing, the "
        "loud-room test below no longer proves anything")

    assert "start" not in feed_all(SpeechDetector(rate=RATE), noisy)


def test_a_room_that_turns_loud_later_does_not_wedge_the_turn():
    """A fan switching on after warm-up looks exactly like someone who started
    talking and never stopped. A turn with no end produces no speech_end, and
    therefore no latency number at all — worse than a wrong one."""
    det = SpeechDetector(rate=RATE, max_utterance_ms=1000)
    feed_all(det, [pcm(30)] * (det.warmup_frames + 4))
    events = [e for e in feed_all(det, [pcm(6000)] * 40) if e]
    assert events[0] == "start"
    assert "end" in events[1:], "the safety valve never fired"
