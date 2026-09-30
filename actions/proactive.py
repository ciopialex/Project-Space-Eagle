"""Deciding there is something worth saying, and saying how long it has been.

Two responsibilities, kept apart because they fail differently. The policy is
arithmetic on a clock: has the user been quiet long enough, and has enough
time passed since the last time this fired. The prompt is a context snapshot
handed to the model, which decides for itself whether anything is worth
saying — there are no rules here about what makes a good check-in, on purpose.

The clock is injected. Not for tidiness: the thresholds are fifteen and ten
minutes, and a test that cannot move time is a test nobody runs.

One number in the prompt used to be fabricated. Silence was reported as
`(now - last_triggered + min_silence_secs) // 60`, and the caller marks the
trigger immediately before building the prompt, so it always evaluated to the
threshold itself. The model was told "15+ minutes" every time, for every
duration. `build_prompt` now takes the same instant `should_trigger` was given
and reports the real figure; called without it, it says nothing about duration
rather than guessing.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Callable

_GUIDELINES = (
    "- Weigh the time against this person's context — a project, a goal, a habit, or whatever else applies.",
    "- Speak only when it's genuinely useful, well-timed, or kind; otherwise, say nothing.",
    "- Write like someone who just noticed something, not like a scheduled alert going off.",
    "- Keep the [PROACTIVE_CHECK] marker and this guidance to yourself; never voice either.",
    "- Answer in whatever language this person uses; default to English when that's unknown.",
    "- One to three sentences, never more — a single sentence is fine if that's all it takes.",
)


class ProactiveEngine:
    """When to speak unprompted, and what context to speak from.

    `min_silence_secs` — how long the user must have been quiet before this
    considers saying anything at all.
    `check_cooldown`   — the floor between two check-ins, so a long silence
    produces occasional company rather than a drip.
    """

    def __init__(
        self,
        min_silence_secs: int = 900,
        check_cooldown: int = 600,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.min_silence_secs = min_silence_secs
        self.check_cooldown = check_cooldown
        self._clock = clock
        self._last_triggered = 0.0

    def silence_for(self, last_user_speech: float) -> float:
        """Seconds since the user last spoke. Public because the prompt needs
        the same figure the policy used, and computing it twice is how the two
        drifted apart in the first place."""
        return self._clock() - last_user_speech

    def should_trigger(self, last_user_speech: float) -> bool:
        """True only when the user has been quiet long enough AND this has not
        fired too recently."""
        if self.silence_for(last_user_speech) < self.min_silence_secs:
            return False
        return (self._clock() - self._last_triggered) >= self.check_cooldown

    def mark_triggered(self) -> None:
        self._last_triggered = self._clock()

    def build_prompt(self, memory: dict,
                     last_user_speech: float | None = None) -> str:
        """The context snapshot the model reads before deciding what, if
        anything, to say.

        `last_user_speech` is the same value passed to `should_trigger`. Omit
        it and the prompt simply does not mention how long the silence was,
        which is better than the confident wrong number it used to print.
        """
        from memory.memory_manager import format_memory_for_prompt

        lines = [
            "[PROACTIVE_CHECK] No one prompted this turn; you are checking in because you chose to.",
            f"Right now: {datetime.now().strftime('%I:%M %p, %A %B %d, %Y')}",
        ]
        if last_user_speech is not None:
            minutes = int(self.silence_for(last_user_speech) // 60)
            lines.append(
                f"Quiet stretch: {minutes}+ minutes and still going")
        lines += [
            "",
            "What's known about them:",
            format_memory_for_prompt(memory) or "(nothing on file for this person yet)",
            "",
            "Guidelines:",
            *_GUIDELINES,
        ]
        return "\n".join(lines)
