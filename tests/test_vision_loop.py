"""Vision must not be askable in a loop.

From the session — five calls, each one succeeding, each returning the same
placeholder:

    [Tool] ▶ screen_process (epoch=1) {angle=screen, text=What is in the screenshot?}
    [Tool] ? screen_process no status reported (73ms)
        said: [VISION_ACTIVE] Screen captured. … the actual image arrives in
              the NEXT message.

and in the chat, the eagle said "I'm looking at your screen" four times in a
row. It had ALREADY described the page correctly on the first pass.

The existing guard is time-based (4s) and `_vision_busy` is cleared at every
turn_complete, so a model asking once per turn sails straight through it. The
missing idea is not a longer timer: it is that asking the SAME question about
the SAME screen twice means the answer is already in hand.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.vision_guard import VisionGuard  # noqa: E402


def test_the_first_request_is_allowed():
    g = VisionGuard(clock=lambda: 100.0)
    assert g.allow("screen", "what is on my screen") is None


def test_the_same_question_again_is_refused():
    now = [100.0]
    g = VisionGuard(clock=lambda: now[0])
    g.allow("screen", "what is on my screen")
    now[0] = 106.0                       # past the old 4s cooldown
    refusal = g.allow("screen", "what is on my screen")
    assert refusal is not None
    low = refusal.lower()
    assert "already" in low
    assert "do not call" in low or "answer from" in low


def test_the_refusal_tells_it_to_use_what_it_has():
    """The loop happened because the refusal read like a status update. It has
    to be an instruction: you have the image, answer from it."""
    g = VisionGuard(clock=lambda: 100.0)
    g.allow("screen", "what is on my screen")
    refusal = g.allow("screen", "what is on my screen")
    assert "screen_process" in refusal


def test_a_different_question_is_allowed():
    """Looking again for a genuinely new reason is legitimate — "is the button
    green" after "what is on screen"."""
    now = [100.0]
    g = VisionGuard(clock=lambda: now[0])
    g.allow("screen", "what is on my screen")
    now[0] = 106.0
    assert g.allow("screen", "is the checkout button visible") is None


def test_the_same_question_is_allowed_again_much_later():
    """The screen changes. After a real interval it is a new question."""
    now = [100.0]
    g = VisionGuard(clock=lambda: now[0])
    g.allow("screen", "what is on my screen")
    now[0] = 100.0 + VisionGuard.REPEAT_WINDOW_S + 1
    assert g.allow("screen", "what is on my screen") is None


def test_camera_and_screen_are_separate_questions():
    g = VisionGuard(clock=lambda: 100.0)
    g.allow("screen", "what do you see")
    assert g.allow("camera", "what do you see") is None


def test_an_in_flight_capture_blocks_everything():
    """Two captures at once inject two images and the model answers about the
    wrong one."""
    g = VisionGuard(clock=lambda: 100.0)
    g.allow("screen", "first")
    g.mark_in_flight()
    assert g.allow("camera", "totally different") is not None
    g.mark_delivered()
    assert g.allow("camera", "totally different") is None
