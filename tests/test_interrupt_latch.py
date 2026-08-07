"""The barge-in latch must never be able to silence the session permanently.

Run:  .venv/bin/python -m pytest tests/ -q

THE FREEZE THIS PINS DOWN
-------------------------
Observed live: the user barged in over a reply, and the eagle never spoke
again — not to the next request, not to "hello???". The mic stayed healthy
(mic_q=0/10, mic_drops=0) and telemetry showed `epoch` frozen at 2, proving no
turn ever completed after the interrupt.

`_interrupted` was a plain boolean whose ONLY exits were a `turn_complete`
message or a full reconnect. Every audio frame received while it was True got
discarded one line before reaching the playback queue. So a barge-in that was
never followed by a `turn_complete` silenced the eagle for the rest of the
session: still listening, still transcribing, still generating, permanently
mute. A latch whose only exit is a message that may never arrive is a deadlock.

The guard is still needed — straggler frames from a cancelled turn are tagged
at RECEIVE time, so they carry the new epoch and slip past the playback loop's
stale-frame filter. It just has to be time-bounded.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402


class Latch:
    """The latch logic in isolation — AethelarkLive.__init__ needs audio,
    a Qt UI and an API key, none of which belong in a unit test."""

    _discard_stragglers = main.AethelarkLive._discard_stragglers

    def __init__(self):
        self._interrupted = False
        self._interrupt_ts = 0.0

    def interrupt(self):
        self._interrupted = True
        self._interrupt_ts = time.monotonic()


def test_straggler_audio_is_discarded_right_after_a_barge_in():
    """The guard must still do its job — otherwise the tail of the interrupted
    sentence plays over the top of the next answer."""
    l = Latch()
    l.interrupt()
    assert l._discard_stragglers() is True


def test_latch_releases_itself_without_a_turn_complete():
    """THE regression. No turn_complete ever arrives; the eagle must recover
    anyway rather than going mute for the rest of the session."""
    l = Latch()
    l.interrupt()
    l._interrupt_ts = time.monotonic() - (main._INTERRUPT_LATCH_S + 0.1)
    assert l._discard_stragglers() is False, "latch stuck — session would stay silent forever"
    assert l._interrupted is False, "latch did not clear its own flag"


def test_latch_stays_closed_for_the_full_window():
    l = Latch()
    l.interrupt()
    l._interrupt_ts = time.monotonic() - (main._INTERRUPT_LATCH_S * 0.5)
    assert l._discard_stragglers() is True


def test_no_discarding_when_nothing_was_interrupted():
    assert Latch()._discard_stragglers() is False


def test_latch_is_idempotent_once_healed():
    """Repeated calls after healing must stay open, not re-arm."""
    l = Latch()
    l.interrupt()
    l._interrupt_ts = time.monotonic() - 10
    assert [l._discard_stragglers() for _ in range(5)] == [False] * 5


def test_a_second_barge_in_re_arms_the_latch():
    """Healing must not permanently disable the guard."""
    l = Latch()
    l.interrupt()
    l._interrupt_ts = time.monotonic() - 10
    assert l._discard_stragglers() is False
    l.interrupt()
    assert l._discard_stragglers() is True


def test_window_is_long_enough_for_stragglers_short_enough_for_speech():
    """Stragglers arrive in milliseconds; a human notices silence within a
    second or so. The window has to sit between those."""
    assert 0.5 <= main._INTERRUPT_LATCH_S <= 3.0


def test_stall_watchdog_threshold_is_sane():
    """Long enough that a slow-but-working turn is never killed, short enough
    that a wedged session recovers before the user gives up."""
    assert 10.0 <= main._TURN_STALL_S <= 60.0


# ------------------------------------------------- text-only turn handling

def test_thought_parts_are_never_surfaced():
    """`thought` parts are the model's private reasoning — never speak or show
    them. Asserted against the source because the receive loop needs a live
    session to exercise directly."""
    src = Path(main.__file__).read_text(encoding="utf-8")
    i = src.find("mt = getattr(sc, \"model_turn\", None)")
    assert i > 0, "text-part capture is missing"
    block = src[i:i + 400]
    assert 'getattr(_p, "thought", False)' in block and "continue" in block, \
        "thought parts are not excluded"


def test_text_only_turn_is_reported_not_revoiced():
    """A second TTS engine would swap voice mid-conversation and add a
    synthesis round-trip — worse than the problem it solves. Text-only turns
    must be surfaced loudly so the real fix lands in the session config."""
    src = Path(main.__file__).read_text(encoding="utf-8")
    assert "if _textonly and not self._turn_had_audio:" in src
    assert "TEXT-ONLY TURN" in src, "a silent turn must be visible in the log"
    assert "_speak_fallback" not in src, "the voice-switching fallback is back"


def test_discard_helper_exists():
    assert callable(getattr(main.AethelarkLive, "_discard_stragglers", None))


# ---------------------------------------------- session config (latency)

def test_thinking_is_not_streamed_back():
    """Streamed reasoning is where stray text/thought parts came from, and a
    turn that returns text instead of audio is a silent turn."""
    src = Path(main.__file__).read_text(encoding="utf-8")
    assert "include_thoughts=False" in src


def test_end_of_turn_silence_is_tuned_and_configurable():
    """This wait sits in front of EVERY reply — it is the biggest tunable
    latency in the product. Too low cuts the user off mid-thought."""
    src = Path(main.__file__).read_text(encoding="utf-8")
    assert "silence_duration_ms=_silence_ms" in src
    assert "end_of_turn_silence_ms" in src, "must be overridable per-person"


def test_default_silence_window_is_in_a_conversational_range():
    # Matches the ASSIGNMENT, not the first textual mention. The positional
    # version read the next 80 characters after the first occurrence of the
    # config key — which broke the moment a comment above the line explained
    # what the key was for, and would have failed while the value was fine.
    import re
    src = Path(main.__file__).read_text(encoding="utf-8")
    m = re.search(r'_cfg\.get\("end_of_turn_silence_ms"\)\s*or\s*(\d+)', src)
    assert m, "the end-of-turn default is no longer read from config"
    default = int(m.group(1))
    assert 300 <= default <= 900, f"{default}ms will feel laggy or cut people off"
