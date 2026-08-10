"""What the eagle is trying to do, and how far it has got.

This is the object that did not exist. Without it every turn starts from
nothing, so the eagle cannot decompose a goal, cannot tell that it has already
failed one way, and has nowhere to escalate except to the human. Measured on a
real MakerWorld attempt: it ran the identical `screen_click "Search bar"`
twice — 81 attempts and five seconds each — then guessed coordinates (500,500),
and the user ended up voice-commanding every individual keystroke.

Deliberately pure state. No tools, no I/O, no browser: the rules that stop
thrashing are worth testing without a network, and the parts that touch the
world live in `mission_ladder` and `actions/mission.py`.

The design constraint that shapes everything here: **there are no mid-mission
approval prompts.** A leader delegates and trusts. What earns that trust is
that a step is only ever marked done after the world has been re-observed —
so `advance()` is called by the executor *after* verification, never before.
"""
from __future__ import annotations

from dataclasses import dataclass, field

PLANNING, RUNNING, BLOCKED, DONE, ABANDONED = (
    "planning", "running", "blocked", "done", "abandoned")


@dataclass
class Attempt:
    """One way of doing a step, and what came of it.

    Kept even when it failed — this is the raw material for the handoff to an
    outside agent, which cannot suggest something better without knowing what
    has already been ruled out.
    """
    strategy: str
    ok: bool
    detail: str = ""


@dataclass
class Step:
    """One small thing, chosen so it can be VERIFIED once done.

    "Find the search box" is a step: you can look afterwards and say whether
    it is there. "Search MakerWorld for a laptop stand" is not — it is three
    steps wearing one sentence, and there is no single observation that
    confirms it. Steps that cannot be checked are how a mission reports
    success it never had.
    """
    intent: str
    target: str = ""      # what to act on, in the words a person would use
    text: str = ""        # what to type, if anything
    url: str = ""         # where to go, if anything
    done: bool = False
    attempts: list[Attempt] = field(default_factory=list)


@dataclass
class Mission:
    goal: str
    steps: list[Step] = field(default_factory=list)
    cursor: int = 0
    status: str = RUNNING
    blocked_reason: str = ""

    def __post_init__(self) -> None:
        if not self.steps:
            self.status = PLANNING
        elif self.cursor >= len(self.steps):
            self.status = DONE

    # ── reading ─────────────────────────────────────────────────────────────

    def current(self) -> Step | None:
        if self.cursor >= len(self.steps):
            return None
        return self.steps[self.cursor]

    def progress(self) -> tuple[int, int]:
        """(done, total) — the two numbers a person would actually say."""
        return (sum(1 for s in self.steps if s.done), len(self.steps))

    def tried(self, strategy: str) -> bool:
        """Has this strategy already been attempted ON THE CURRENT STEP?

        Scoped to the step rather than the mission on purpose: clicking twice
        in a plan is ordinary, clicking the same control the same failed way
        twice is the bug.
        """
        step = self.current()
        return bool(step) and any(a.strategy == strategy for a in step.attempts)

    # ── writing ─────────────────────────────────────────────────────────────

    def plan(self, steps: list[Step]) -> None:
        self.steps = list(steps)
        self.cursor = 0
        self.status = RUNNING if steps else PLANNING

    def record_attempt(self, strategy: str, ok: bool, detail: str = "") -> None:
        step = self.current()
        if step is not None:
            step.attempts.append(Attempt(strategy, ok, detail))

    def advance(self) -> None:
        """Mark the current step done and move on.

        Called by the executor only AFTER the step has been verified. Nothing
        in this class checks that; the discipline lives at the call site, and
        the tests there pin it.
        """
        step = self.current()
        if step is not None:
            step.done = True
            self.cursor += 1
        if self.current() is None:
            self.status = DONE

    def block(self, reason: str) -> None:
        """Out of ways to do the current step. Not a failure of the mission —
        a request for a better plan, or for a human."""
        self.status = BLOCKED
        self.blocked_reason = reason

    def abandon(self) -> None:
        self.status = ABANDONED
