"""The intent decoder: a hypothesis, a confidence, and permission to prepare.

It does NOT route. The model still chooses the tool — this narrows what it is
choosing between and starts the safe half early, which is where the speed
comes from. Removing the model from the decision would trade a well-tuned
chooser for a keyword matcher, which is a bad trade nobody asked for.

Authority is inverted from core/prompt_reflex.py, the closest prior art here.
That layer may STOP on its own and may only GO from a proven-safe list, because
blocking is cheap and allowing is not. This one is the mirror:

    it may PRE-WARM on its own authority,
    it may only ACT once the model has committed.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.intent import HIGH, LOW, decode  # noqa: E402


def test_a_clear_request_is_decoded_with_high_confidence():
    got = decode("tell me my latest liked video on youtube")
    assert got.capability.id == "youtube.liked"
    assert got.confidence >= HIGH


def test_a_vague_request_decodes_to_nothing_rather_than_a_guess():
    """Silence is a valid answer and the common one. Anything unrecognised
    falls through to today's behaviour: the model decides alone."""
    for said in ("mhm", "okay cool", "thanks", "hold on"):
        assert decode(said).capability is None, said


def test_confidence_falls_when_two_capabilities_are_both_plausible():
    """"Open my messages" could be messages_brief or open_app. A decoder that
    reports high confidence on a genuine ambiguity is worse than one that
    abstains, because the pre-warm is then usually wasted."""
    clear = decode("what did i miss in my messages")
    ambiguous = decode("open my messages")
    assert ambiguous.confidence < clear.confidence


def test_only_read_only_work_is_ever_offered_to_start():
    """The invariant. Confidence never unlocks a side effect."""
    for said in ("send a message to mama saying i am late",
                 "shut down aethelark",
                 "buy it now",
                 "delete that file"):
        for cap in decode(said).prewarm:
            assert cap.effect == "read_only", f"{cap.id} on '{said}'"


def test_a_compound_request_prepares_the_safe_half():
    """The commonest shape: navigate THEN act."""
    got = decode("go to emag.ro and search for wireless headphones")
    assert {c.id for c in got.prewarm} == {"web.open"}
    assert got.capability.effect != "read_only", "the ACT half stays with the model"


def test_the_narrowed_candidates_include_the_answer():
    """What is handed to the model. It must never exclude the right tool —
    a narrowing that drops the answer is worse than no narrowing at all."""
    got = decode("tell me my latest liked video")
    assert "youtube_api" in got.candidates


def test_decoding_is_free_at_the_timescale_it_runs_in():
    """It runs inside the 350ms end-of-turn window, on the event loop. If the
    hypothesis costs more than the work it saves it is a net loss."""
    said = "go to emag.ro and search for wireless headphones and then click buy"
    start = time.perf_counter()
    for _ in range(200):
        decode(said)
    per_call_ms = (time.perf_counter() - start) * 1000 / 200
    assert per_call_ms < 2.0, f"{per_call_ms:.2f}ms per decode is too slow to be free"


def test_a_partial_utterance_still_decodes():
    """The whole point of being early: this runs while the user is still
    talking, so it must work on half a sentence."""
    got = decode("go to emag.ro and sear")
    assert any(c.id == "web.open" for c in got.prewarm)


def test_it_never_raises_whatever_it_is_handed():
    for junk in (None, "", "   ", 5, "!!!", "a" * 5000):
        assert decode(junk) is not None
