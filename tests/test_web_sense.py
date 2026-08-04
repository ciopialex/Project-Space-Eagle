"""How much to look, and when looking harder is worth it.

A screenshot is one to two orders of magnitude more expensive than a snapshot,
in tokens and in latency. The default has to be the cheap sense; the escalation
has to be automatic when the cheap one is not answering.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.grounding.web.sense import (EscalationPolicy, PageSense,  # noqa: E402
                                         Sense)
from tests.web_fakes import FakePage, records as _records  # noqa: E402


def test_a_rich_page_is_read_as_structure_and_costs_no_screenshot():
    page = FakePage(_records(20))
    sense = PageSense().look(page)
    assert sense.tier == "snapshot"
    assert len(sense.nodes) == 20
    assert sense.screenshot is None
    assert page.shots_taken == 0


def test_a_thin_snapshot_escalates_to_pixels():
    page = FakePage(_records(1))
    sense = PageSense().look(page)
    assert sense.tier == "screenshot"
    assert sense.screenshot == b"PNG"
    assert "thin" in sense.reason
    # The nodes we did find are still carried — escalation adds, never replaces.
    assert len(sense.nodes) == 1


def test_an_empty_page_escalates():
    assert PageSense().look(FakePage([])).tier == "screenshot"


def test_the_model_can_ask_for_pixels_on_a_rich_page():
    page = FakePage(_records(20))
    sense = PageSense().look(page, want_pixels=True)
    assert sense.tier == "screenshot"
    assert "asked" in sense.reason


def test_two_failures_escalate_the_next_look():
    page = PageSense()
    rich = FakePage(_records(20))
    assert page.look(rich).tier == "snapshot"
    page.note_failure()
    assert page.look(rich).tier == "snapshot", "one failure is not a pattern"
    page.note_failure()
    assert page.look(rich).tier == "screenshot"
    assert "failed" in page.look(rich).reason


def test_success_clears_the_failure_count():
    sense = PageSense()
    sense.note_failure()
    sense.note_failure()
    sense.note_success()
    assert sense.failures == 0
    assert sense.look(FakePage(_records(20))).tier == "snapshot"


def test_the_policy_is_tunable_without_touching_perception():
    strict = PageSense(EscalationPolicy(min_nodes=50, max_failures=1))
    assert strict.look(FakePage(_records(20))).tier == "screenshot"


def test_a_page_that_explodes_while_collecting_degrades_to_pixels():
    class Broken(FakePage):
        def collect(self):
            raise RuntimeError("navigation in flight")

    sense = PageSense().look(Broken())
    assert sense.tier == "screenshot"
    assert sense.nodes == ()


def test_a_page_that_cannot_even_screenshot_still_returns_a_sense():
    class Dead(FakePage):
        def collect(self):
            raise RuntimeError("gone")

        def screenshot(self):
            raise RuntimeError("also gone")

    sense = PageSense().look(Dead())
    assert isinstance(sense, Sense)
    assert sense.nodes == () and sense.screenshot is None
