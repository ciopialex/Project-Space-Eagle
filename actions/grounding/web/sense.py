"""How much of a page to look at.

The accessibility snapshot is the default sense: roughly 50-200 lines for a
complex page, local, and addressable by name. A screenshot is the escalation,
not the baseline — it costs one to two orders of magnitude more and answers
fewer questions, because a picture cannot tell you that a button is disabled.

`page.screenshot()` renders from the browser compositor rather than the
display, so the escalation tier works on a background tab, an unfocused window
or a headless browser. That is what lets the eagle work while the user keeps
their screen.
"""
from __future__ import annotations

from dataclasses import dataclass

from actions.grounding.web.page import PageLike, WebNode, nodes_from_records


@dataclass(frozen=True)
class EscalationPolicy:
    """When the cheap sense stops being good enough.

    Separate from the grounder so these can be tuned — per site, per user, from
    a config file — without anyone editing perception to do it.
    """
    #: Below this many named controls, the page is not telling us enough.
    min_nodes: int = 5
    #: Consecutive failed actions before we stop trusting structure alone.
    max_failures: int = 2


@dataclass(frozen=True)
class Sense:
    """What the eagle currently perceives of a page."""
    tier: str                       # "snapshot" | "screenshot"
    nodes: tuple[WebNode, ...]
    screenshot: bytes | None
    reason: str

    @property
    def escalated(self) -> bool:
        return self.tier == "screenshot"


class PageSense:
    """Decides how hard to look, and remembers how badly it has been going."""

    def __init__(self, policy: EscalationPolicy | None = None) -> None:
        self._policy = policy or EscalationPolicy()
        self._failures = 0

    @property
    def policy(self) -> EscalationPolicy:
        return self._policy

    @property
    def failures(self) -> int:
        return self._failures

    def note_failure(self) -> None:
        """An action did not do what it should have. Two of these and we look
        harder — the same instinct as a person leaning in."""
        self._failures += 1

    def note_success(self) -> None:
        self._failures = 0

    def _escalation_reason(self, nodes: tuple[WebNode, ...],
                           want_pixels: bool) -> str:
        if want_pixels:
            return "asked for pixels"
        if len(nodes) < self._policy.min_nodes:
            return (f"snapshot came back thin ({len(nodes)} controls, "
                    f"under {self._policy.min_nodes})")
        if self._failures >= self._policy.max_failures:
            return f"acting failed {self._failures} times in a row"
        return ""

    def look(self, page: PageLike, *, want_pixels: bool = False) -> Sense:
        """Perceive `page`. Never raises — a page mid-navigation is normal."""
        try:
            nodes = nodes_from_records(page.collect())
        except Exception:
            nodes = ()

        reason = self._escalation_reason(nodes, want_pixels)
        if not reason:
            return Sense("snapshot", nodes, None,
                         f"read {len(nodes)} controls structurally")

        try:
            shot = page.screenshot()
        except Exception:
            shot = None
        return Sense("screenshot", nodes, shot, reason)
