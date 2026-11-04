"""What the eagle is trying to do and how far it has got. Pure state: no tools, no
I/O, no browser, so the rules that stop thrashing are testable; the world is
touched in mission_ladder and actions/mission.py.

No mid-mission approval prompts: a step is marked done only after the world is re-
observed, so advance() is called after verification, never before.
"""
from __future__ import annotations

from dataclasses import dataclass, field

PLANNING, RUNNING, BLOCKED, DONE, ABANDONED = (
    "planning", "running", "blocked", "done", "abandoned")


@dataclass
class Attempt:
    """One way of doing a step and what came of it. Kept when it failed: the handoff
    needs to know what was ruled out.
    """
    strategy: str
    ok: bool
    detail: str = ""


@dataclass
class Step:
    """One small thing that can be verified once done. "Find the search box" is a
    step; "search MakerWorld for a laptop stand" is three.
    """
    intent: str
    target: str = ""      # what to act on, in the words a person would use
    text: str = ""        # what to type, if anything
    url: str = ""         # where to go, if anything
    done: bool = False
    attempts: list[Attempt] = field(default_factory=list)
    # The mission's blackboard, the same dict as Mission.facts, so a runner writing into
    # it tells every later step.
    data: dict = field(default_factory=dict)
    # Mirrors Mission.authorized, so web runners can pass the user's up-front yes to
    # web_agency.
    authorized: bool = False


@dataclass
class Mission:
    goal: str
    steps: list[Step] = field(default_factory=list)
    cursor: int = 0
    status: str = RUNNING
    blocked_reason: str = ""
    # What this mission learned: file paths, contents read. Plain strings for the store.
    facts: dict = field(default_factory=dict)
    # Where this mission has been, most recent last. Plain strings for the store.
    places: list[str] = field(default_factory=list)
    # The user said yes once, up front, to whatever the goal takes, including a control
    # the gate would otherwise refuse. Set only from an explicit confirm=True in _start;
    # never inferred.
    authorized: bool = False

    def __post_init__(self) -> None:
        if not self.steps:
            self.status = PLANNING
        elif self.cursor >= len(self.steps):
            self.status = DONE


    def current(self) -> Step | None:
        if self.cursor >= len(self.steps):
            return None
        return self.steps[self.cursor]

    def progress(self) -> tuple[int, int]:
        """(done, total)."""
        return (sum(1 for s in self.steps if s.done), len(self.steps))

    def tried(self, strategy: str) -> bool:
        """Has this strategy already been tried on the current step? Per step:
        clicking twice in a plan is normal; failing the same way twice is the bug.
        """
        step = self.current()
        return bool(step) and any(a.strategy == strategy for a in step.attempts)


    def plan(self, steps: list[Step]) -> None:
        self.steps = list(steps)
        self.cursor = 0
        self.status = RUNNING if steps else PLANNING

    def record_attempt(self, strategy: str, ok: bool, detail: str = "") -> None:
        step = self.current()
        if step is not None:
            step.attempts.append(Attempt(strategy, ok, detail))

    def advance(self) -> None:
        """Mark the current step done. Called only after it was verified; the
        discipline lives at the call site.
        """
        step = self.current()
        if step is not None:
            step.done = True
            self.cursor += 1
        if self.current() is None:
            self.status = DONE

    # Two visits to a page is ordinary; three is a loop.
    CIRCLE_THRESHOLD = 3

    def note_place(self, signature) -> None:
        """Remember having been somewhere. An unreadable page is not a visit."""
        if signature is None or not getattr(signature, "worth_recording", False):
            return
        self.places.append(signature.key)

    def times_at(self, signature) -> int:
        if signature is None or not getattr(signature, "worth_recording", False):
            return 0
        return self.places.count(signature.key)

    def going_in_circles(self, signature) -> bool:
        """Been here enough times that arriving again is not progress."""
        return self.times_at(signature) >= self.CIRCLE_THRESHOLD

    def block(self, reason: str) -> None:
        """Out of ways to do the current step: a request for a better plan or a
        human.
        """
        self.status = BLOCKED
        self.blocked_reason = reason

    def abandon(self) -> None:
        self.status = ABANDONED
