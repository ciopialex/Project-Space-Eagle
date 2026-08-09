"""The decoder, wired to the live turn.

`sc.input_transcription` streams the user's words WHILE they are still
speaking. That is the only moment early enough for a prediction to be worth
anything — by the time the model emits a tool call, the 350ms silence window
and the round trip have already been spent.

The first slice is deliberately one prediction: web-shaped means warm the
browser. Cold start was measured at ~310ms and the first open on a fresh
profile at 7165ms, it has no side effects whatsoever, and it is trivially
falsifiable — if `to_action` does not move on web requests, the design is
wrong and nothing else should be built on it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402


class Spec:
    """AethelarkLive's speculation, without its constructor's hardware."""
    _speculate = main.AethelarkLive._speculate
    _trace_mark = main.AethelarkLive._trace_mark

    def __init__(self):
        self.warmed = 0
        self._spoken = ""
        self._speculated = False
        self._trace = None

    def _warm_browser(self):
        self.warmed += 1


def test_a_web_request_warms_the_browser_while_the_user_talks():
    s = Spec()
    s._speculate("go to emag.ro and")          # mid-sentence
    assert s.warmed == 1, "did not start the safe work early"


def test_it_warms_at_most_once_per_turn():
    """Transcription streams in many chunks. Restarting the browser on each
    one would be far worse than never warming it."""
    s = Spec()
    for partial in ("go", "go to", "go to emag", "go to emag.ro and search"):
        s._speculate(partial)
    assert s.warmed == 1


def test_nothing_is_warmed_for_a_request_that_is_not_web_shaped():
    for said in ("what did i miss in my messages",
                 "turn the volume up",
                 "remind me in an hour"):
        s = Spec()
        s._speculate(said)
        assert s.warmed == 0, said


def test_nothing_is_warmed_for_an_irreversible_request():
    """The invariant, at the wiring level rather than only in the catalogue."""
    for said in ("send a message to mama saying i am late",
                 "shut down aethelark"):
        s = Spec()
        s._speculate(said)
        assert s.warmed == 0, said


def test_a_failing_prediction_never_breaks_the_turn():
    """It runs on the receive loop. A speculative optimisation that can throw
    is worse than no optimisation."""
    class Broken(Spec):
        def _warm_browser(self):
            raise RuntimeError("no browser today")

    s = Broken()
    s._speculate("go to emag.ro")     # must not raise
    assert True


def test_the_turn_resets_the_speculation():
    """Each turn gets one warm. A flag left set would silently disable the
    feature for the rest of the session."""
    s = Spec()
    s._speculate("go to emag.ro")
    s._speculated = False             # what the turn boundary does
    s._speculate("go to olx.ro")
    assert s.warmed == 2
