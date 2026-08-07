"""The instrumentation has to be trustworthy before its numbers are.

Written first, per the brief: the assumption that the model is the slow part
is the expensive one to act on and is usually wrong, so the measurement comes
before the optimisation.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.turn_trace import TraceLog, TurnTrace  # noqa: E402


def test_disabled_costs_nothing_and_says_nothing():
    t = TurnTrace(enabled=False)
    t.mark("speech_end")
    t.mark("first_audio")
    assert t.at("speech_end") is None
    assert t.summary() == ""


def test_first_write_wins_so_first_audio_means_first():
    """`first_token` and `first_audio` are called from per-frame loops. A
    last-write-wins mark would report the END of the response as its start —
    the exact opposite of what is being measured."""
    t = TurnTrace(enabled=True)
    t.mark("first_audio")
    first = t.at("first_audio")
    for _ in range(5):
        t.mark("first_audio")
    assert t.at("first_audio") == first


def test_a_segment_that_never_happened_is_absent_not_zero():
    """A conversational reply has no tool call. Reporting `tool=0ms` would put
    a zero into an average that then hides the turns where it was seconds."""
    t = TurnTrace(enabled=True)
    t.mark("speech_end")
    t.mark("turn_detected")
    t.mark("first_audio")
    assert t.delta_ms("turn_detected", "first_tool") is None
    assert "tool=" not in t.summary()
    assert "to_voice=" in t.summary()


def test_deltas_measure_the_gap_the_user_feels(monkeypatch):
    # The constructor takes no clock reading of its own — it used to store a
    # _t0 that nothing ever read.
    clock = iter([100.0, 100.55, 101.30])          # end -> vad -> audio
    monkeypatch.setattr("core.turn_trace.time.monotonic", lambda: next(clock))
    t = TurnTrace(enabled=True)
    t.mark("speech_end")
    t.mark("turn_detected")
    t.mark("first_audio")
    assert round(t.delta_ms("speech_end", "turn_detected")) == 550
    assert round(t.delta_ms("speech_end", "first_audio")) == 1300


def test_the_log_reports_a_tail_not_just_an_average():
    """A pipeline that is usually fast and occasionally terrible is worse than
    one that is consistently middling, and a mean hides exactly that."""
    log = TraceLog(emit=lambda _line: None)
    for i, gap in enumerate([0.1] * 9 + [4.0]):
        t = TurnTrace(turn=i, enabled=True)
        t._marks["speech_end"] = 0.0
        t._marks["first_audio"] = gap
        log.finish(t)

    stats = log.stats("to_voice")
    assert stats["n"] == 10
    assert round(stats["median_ms"]) == 100
    assert round(stats["max_ms"]) == 4000       # the tail is visible
    assert stats["p90_ms"] > stats["median_ms"]


def test_only_the_kept_window_is_retained():
    log = TraceLog(keep=3, emit=lambda _l: None)
    for i in range(10):
        t = TurnTrace(turn=i, enabled=True)
        t._marks["speech_end"] = 0.0
        t._marks["first_audio"] = 0.2
        log.finish(t)
    assert log.stats("to_voice")["n"] == 3


def test_marks_from_several_threads_do_not_corrupt_the_trace():
    """Marks arrive from the mic thread, the receive loop, the playback thread
    and the tool executor."""
    import threading
    t = TurnTrace(enabled=True)
    names = ["speech_start", "speech_end", "turn_detected", "model_request",
             "first_token", "first_audio", "complete"]
    threads = [threading.Thread(target=t.mark, args=(n,)) for n in names * 8]
    for th in threads: th.start()
    for th in threads: th.join()
    assert all(t.at(n) is not None for n in names)


def test_report_is_readable_with_no_turns():
    assert "no turns recorded" in TraceLog(emit=lambda _l: None).report()
