"""Settling is where the eagle's web latency actually lives.

Measured on a local fixture: a page that is finished the moment it parses still
took 1239ms to be declared ready, of which ~1200ms was an unconditional sleep.
Snapshotting the same page costs 7ms. So the wait was ~99% of the cost of
looking at a page, and almost all of it was spent on pages that needed none
of it.

The opposite mistake is worse and has already happened once: youtube.com read
as **6 controls** because the collector ran while React was still mounting. A
fast wrong answer about what is on the page is the failure this whole module
exists to prevent, so every test here that makes settling cheaper is paired
with one that proves it still waits when waiting is the point.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.grounding.web import browser as B  # noqa: E402


class ScriptedPage:
    """A page whose DOM size follows a script, one entry per poll."""

    def __init__(self, counts, selector_ms=0):
        self.counts = list(counts)
        self.polls = 0
        self.waited_ms = 0
        self.selector_ms = selector_ms

    def wait_for_selector(self, _sel, timeout=0):
        self.waited_ms += self.selector_ms

    def wait_for_timeout(self, ms):
        self.waited_ms += ms

    def evaluate(self, _js):
        i = min(self.polls, len(self.counts) - 1)
        self.polls += 1
        return self.counts[i]


def test_a_finished_page_is_not_waited_on():
    """The 1200ms case. A static page's DOM never changes, so the second
    identical reading is enough to know it is done."""
    page = ScriptedPage([120] * 10)
    B._settle(page)
    assert page.waited_ms < 500, f"waited {page.waited_ms}ms on a static page"


def test_a_late_mounting_app_is_still_seen_whole():
    """The youtube-read-as-6-controls case. The DOM grows after
    domcontentloaded; settling must not conclude before it stops."""
    page = ScriptedPage([5, 5, 40, 300, 900, 900, 900, 900])
    B._settle(page)
    assert page.evaluate(None) == 900, "settled before the app finished mounting"
    assert page.polls >= 5


def test_a_page_that_never_settles_is_capped():
    """A DOM that changes forever — an animation, a live feed — must not hold
    the eagle hostage. Bounded, and the bound is not much worse than the fixed
    wait it replaces."""
    page = ScriptedPage([i * 10 for i in range(1000)])
    B._settle(page)
    assert page.waited_ms <= B._SETTLE_MAX_MS + B._SETTLE_POLL_MS


def test_settling_never_raises_on_a_hostile_page():
    """`evaluate` runs script in a page the eagle does not control. A page
    that throws must cost a fallback wait, not the whole action."""
    class Exploding(ScriptedPage):
        def evaluate(self, _js):
            raise RuntimeError("detached frame")

    page = Exploding([1])
    B._settle(page)                      # must not raise
    assert page.waited_ms > 0, "a page we cannot measure must still be waited on"


def test_a_page_with_no_evaluate_falls_back_to_waiting():
    """Older page doubles and any port that does not expose `evaluate` must
    keep the previous behaviour rather than skip settling entirely."""
    class NoEval:
        def __init__(self):
            self.waited_ms = 0

        def wait_for_selector(self, _s, timeout=0):
            pass

        def wait_for_timeout(self, ms):
            self.waited_ms += ms

    page = NoEval()
    B._settle(page)
    assert page.waited_ms >= B._SETTLE_MAX_MS


def test_the_body_wait_still_happens_first():
    """Polling an unparsed document reads 0 elements twice and settles
    instantly on an empty page."""
    seen = []

    class Recorder(ScriptedPage):
        def wait_for_selector(self, sel, timeout=0):
            seen.append(sel)

    B._settle(Recorder([50] * 5))
    assert seen and seen[0] == "body"
