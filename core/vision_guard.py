"""Stops the eagle asking to look at the screen it has already looked at.

From a live session: five `screen_process` calls in a row, each succeeding,
each returning the same placeholder, and the eagle saying "I'm looking at your
screen" four times — after it had already described the page correctly on the
first pass.

The guard that existed was a 4-second cooldown plus an in-flight flag cleared
at every `turn_complete`. A model that asks once per turn walks straight
through both. The missing idea was never a longer timer: it is that asking the
SAME question about the SAME surface twice means the answer is already in hand
and the right move is to use it.

Two separate refusals, because they are different problems:

  * IN FLIGHT — a capture is already on its way to the model. A second one
    injects a second image and the answer ends up describing the wrong one.
  * ALREADY ANSWERED — the same question about the same surface, recently. The
    image was delivered; asking again is the loop.

Looking again for a genuinely new reason stays allowed. "Is the checkout
button visible" after "what is on my screen" is a real second question, and so
is the same question minutes later, because screens change.
"""
from __future__ import annotations

import time
from typing import Callable


class VisionGuard:
    """Decides whether a vision request should proceed. Never raises."""

    #: How long the same question about the same surface counts as answered.
    #: Long enough to break the loop (which repeats within seconds), short
    #: enough that a genuine second look is not blocked for a whole
    #: conversation.
    REPEAT_WINDOW_S = 45.0

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._in_flight = False
        self._last: dict[tuple[str, str], float] = {}

    @staticmethod
    def _key(angle: str, text: str) -> tuple[str, str]:
        return ((angle or "screen").strip().lower(),
                " ".join((text or "").lower().split()))

    def allow(self, angle: str, text: str) -> str | None:
        """None if the request may proceed, else the refusal to hand back.

        The refusal is written as an INSTRUCTION rather than a status. The loop
        happened because "vision is still processing" reads as something to
        wait out and try again, so the model tried again.
        """
        try:
            now = self._clock()
            if self._in_flight:
                return ("A screen capture is already on its way to you. Do NOT "
                        "call screen_process again — wait for the image in the "
                        "next message and answer from it.")

            key = self._key(angle, text)
            seen = self._last.get(key)
            if seen is not None and (now - seen) < self.REPEAT_WINDOW_S:
                surface = key[0]
                return (f"You have already been shown this {surface} and asked "
                        f"this same question. Answer from the image you were "
                        f"given. Do NOT call screen_process again for this — if "
                        f"you need something specific, ask a DIFFERENT question "
                        f"about it, or tell the user what you saw.")

            self._last[key] = now
            return None
        except Exception:
            return None      # a guard that breaks must not block the feature

    def mark_in_flight(self) -> None:
        self._in_flight = True

    def mark_delivered(self) -> None:
        """The image reached the model; a new question is allowed again."""
        self._in_flight = False
