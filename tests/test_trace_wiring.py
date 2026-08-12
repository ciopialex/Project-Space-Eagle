"""The instrumentation is only worth having if it fires on the real code paths.

`test_turn_trace.py` proves the recorder is correct in isolation and
`test_mic_vad.py` proves the detector is. Neither would notice if the marks
were never called, called in the wrong order, or wired to a branch that a live
turn does not take — which is the usual way instrumentation ends up reporting
confidently about nothing.

These drive AethelarkLive's own trace methods, borrowed the same way the
dispatch tests borrow the scheduler, because the real __init__ wants a sound
device, a Qt UI and an API key.
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402
from core.mic_vad import SpeechDetector  # noqa: E402
from core.turn_trace import TraceLog  # noqa: E402

FRAME = 1024


def pcm(amplitude: int) -> bytes:
    return struct.pack(f"<{FRAME}h",
                       *[amplitude if i % 2 else -amplitude
                         for i in range(FRAME)])


class Traced:
    """AethelarkLive's tracing, without its constructor's hardware."""

    _trace_mark      = main.AethelarkLive._trace_mark
    # speech_end is back-dated by the detector's hangover, so the stub needs
    # the same entry point the real class uses.
    _trace_mark_at   = main.AethelarkLive._trace_mark_at
    _trace_begin     = main.AethelarkLive._trace_begin
    _trace_finish    = main.AethelarkLive._trace_finish
    _trace_mic_frame = main.AethelarkLive._trace_mic_frame

    def __init__(self, tracing=True):
        self.lines = []
        self._turn_epoch = 0
        self._trace = None
        self._trace_log = TraceLog(emit=self.lines.append)
        self._vad = SpeechDetector(frame_samples=FRAME) if tracing else None

    def say(self, loud_frames=8, quiet_frames=12):
        """One complete utterance through the real mic hook."""
        warmup = self._vad.warmup_frames + 4 if self._vad else 20
        for _ in range(warmup):
            self._trace_mic_frame(pcm(30))          # room tone
        for _ in range(loud_frames):
            self._trace_mic_frame(pcm(6000))
        for _ in range(quiet_frames):
            self._trace_mic_frame(pcm(20))


def test_a_spoken_turn_produces_a_trace_line():
    t = Traced()
    t.say()
    t._trace_mark("first_token")
    t._trace_mark("first_audio")
    t._trace_mark("complete")
    t._trace_finish()

    assert len(t.lines) == 1, t.lines
    line = t.lines[0]
    assert "to_voice=" in line and "response=" in line and "spoken=" in line


def test_tracing_off_costs_nothing_and_emits_nothing():
    """The default. A latency tool that slows the pipeline down is a joke."""
    t = Traced(tracing=False)
    t.say()
    t._trace_mark("first_audio")
    t._trace_finish()
    assert t.lines == []
    assert t._trace is None


def test_the_mic_hook_is_what_opens_the_turn():
    """Marks arriving before any speech belong to no turn and must not create
    one — otherwise a session's idle chatter reports as a turn with a
    to_voice measured from nothing."""
    t = Traced()
    t._trace_mark("first_audio")
    t._trace_finish()
    assert t.lines == []

    t.say()
    assert t._trace is not None


def test_speech_end_precedes_the_response_marks():
    """The ordering the numbers depend on. If speech_end landed after
    first_token, to_voice would come out negative and nobody would notice
    because the summary prints whatever it is handed."""
    t = Traced()
    t.say()
    t._trace_mark("first_token")
    t._trace_mark("first_audio")
    assert t._trace.delta_ms("speech_end", "first_token") >= 0
    assert t._trace.delta_ms("speech_end", "first_audio") >= 0


def test_an_interrupted_turn_is_dropped_not_reported():
    """Mirrors what interrupt() does. A turn the user talked over never
    reached its own end; reporting it would mix how long the eagle took with
    how long the user was willing to listen."""
    t = Traced()
    t.say()
    t._trace_mark("first_audio")
    t._trace = None                      # what interrupt() does
    t._trace_finish()
    assert t.lines == []


def test_a_tool_turn_reports_to_action():
    t = Traced()
    t.say()
    t._trace_mark("first_tool")
    t._trace_mark("complete")
    t._trace_finish()
    assert "to_action=" in t.lines[0]


def test_a_conversational_turn_reports_no_tool_segment():
    t = Traced()
    t.say()
    t._trace_mark("first_token")
    t._trace_mark("first_audio")
    t._trace_mark("complete")
    t._trace_finish()
    assert "to_action=" not in t.lines[0]
    assert "tool=" not in t.lines[0]


def test_every_mark_the_code_sets_is_a_known_mark():
    """A typo'd mark name is silently accepted by `mark()` and then never
    appears in any segment — instrumentation that looks wired and reports
    nothing. This pins the call sites against the vocabulary."""
    import re
    source = Path(main.__file__).read_text()
    # Both call forms count. `speech_end` is stamped with `_trace_mark_at`,
    # back-dated by the detector's hangover — it is detected 300ms after the
    # user actually stopped, and marking it at detection time understated the
    # felt delay by exactly that much.
    used = set(re.findall(r'_trace_mark(?:_at)?\(["\'](\w+)["\']', source))
    assert used, "no _trace_mark call sites found — did the wiring move?"
    unknown = used - set(main.TurnTrace.__module__ and __import__(
        "core.turn_trace", fromlist=["MARKS"]).MARKS)
    assert unknown == set(), f"unknown marks in main.py: {unknown}"


def test_the_marks_that_matter_are_actually_wired():
    """Guards against a mark being dropped during a refactor of main.py. Each
    of these is the sole source of a reported segment."""
    import re
    source = Path(main.__file__).read_text()
    # Both call forms count. `speech_end` is stamped with `_trace_mark_at`,
    # back-dated by the detector's hangover — it is detected 300ms after the
    # user actually stopped, and marking it at detection time understated the
    # felt delay by exactly that much.
    used = set(re.findall(r'_trace_mark(?:_at)?\(["\'](\w+)["\']', source))
    for required in ("speech_start", "speech_end", "first_token",
                     "first_audio", "first_tool", "complete"):
        assert required in used, f"{required} is no longer marked anywhere"
