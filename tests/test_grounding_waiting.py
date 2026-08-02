from actions.grounding.base import Element
from actions.grounding.waiting import WaitResult, wait_for

LIVE = frozenset({"ENABLED", "SENSITIVE", "VISIBLE", "SHOWING"})


def _el(states=LIVE, left=100):
    return Element.from_bounds("Save", "push button", left, 200, 80, 40,
                               "atspi", states=states)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class ScriptedResolver:
    """Returns a different element on each successive lookup."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def find(self, description):
        self.calls += 1
        return self._script[min(self.calls - 1, len(self._script) - 1)]


def test_succeeds_when_already_actionable():
    clock = FakeClock()
    r = ScriptedResolver([_el(), _el()])
    res = wait_for("Save button", "click", resolver=r,
                   hit_test=lambda x, y: _el(), clock=clock, sleep=clock.sleep)
    assert res.ok is True
    assert res.failed_check == ""
    assert res.element is not None


def test_waits_for_an_element_that_appears_late():
    clock = FakeClock()
    r = ScriptedResolver([None, None, _el(), _el()])
    res = wait_for("Save button", "click", resolver=r,
                   hit_test=lambda x, y: _el(), clock=clock, sleep=clock.sleep)
    assert res.ok is True
    assert r.calls >= 3


def test_waits_for_an_element_that_is_still_animating():
    clock = FakeClock()
    r = ScriptedResolver([_el(left=100), _el(left=120), _el(left=140),
                          _el(left=140), _el(left=140)])
    res = wait_for("Save button", "click", resolver=r,
                   hit_test=lambda x, y: _el(left=140),
                   clock=clock, sleep=clock.sleep)
    assert res.ok is True


def test_times_out_and_names_the_failing_check():
    clock = FakeClock()
    disabled = _el(states=frozenset({"VISIBLE", "SHOWING"}))
    r = ScriptedResolver([disabled])
    res = wait_for("Save button", "click", resolver=r, timeout=1.0,
                   hit_test=lambda x, y: disabled, clock=clock,
                   sleep=clock.sleep)
    assert res.ok is False
    assert res.failed_check == "enabled"


def test_times_out_when_element_never_appears():
    clock = FakeClock()
    r = ScriptedResolver([None])
    res = wait_for("Ghost button", "click", resolver=r, timeout=1.0,
                   clock=clock, sleep=clock.sleep)
    assert res.ok is False
    assert res.failed_check == "not_found"
    assert res.element is None


def test_force_skips_the_checks_but_still_needs_an_element():
    clock = FakeClock()
    r = ScriptedResolver([_el(states=frozenset())])
    res = wait_for("Save button", "click", resolver=r, force=True,
                   clock=clock, sleep=clock.sleep)
    assert res.ok is True
    assert res.element is not None


def test_element_is_re_resolved_on_every_poll():
    """Playwright's lesson: a handle held across a redraw is a stale handle."""
    clock = FakeClock()
    r = ScriptedResolver([None, None, None, _el(), _el()])
    wait_for("Save button", "click", resolver=r,
             hit_test=lambda x, y: _el(), clock=clock, sleep=clock.sleep)
    assert r.calls >= 4


def test_a_resolver_that_raises_does_not_break_the_loop():
    clock = FakeClock()

    class Exploding:
        def find(self, d):
            raise RuntimeError("kaboom")

    res = wait_for("Save", "click", resolver=Exploding(), timeout=0.3,
                   clock=clock, sleep=clock.sleep)
    assert res.ok is False
    assert res.failed_check == "not_found"


def test_result_records_attempts_and_elapsed():
    clock = FakeClock()
    r = ScriptedResolver([None])
    res = wait_for("Ghost", "click", resolver=r, timeout=0.5, poll=0.1,
                   clock=clock, sleep=clock.sleep)
    assert isinstance(res, WaitResult)
    assert res.attempts >= 2
    assert res.elapsed_ms > 0


class RecordingResolver:
    def __init__(self, element):
        self.element = element
        self.fast_only_flags = []

    def find(self, description, fast_only=False):
        self.fast_only_flags.append(fast_only)
        return self.element


def test_polling_uses_the_fast_path_only():
    """Vision costs seconds per attempt; polling it blew past the timeout."""
    clock = FakeClock()
    r = RecordingResolver(_el())
    wait_for("Save button", "click", resolver=r,
             hit_test=lambda x, y: _el(), clock=clock, sleep=clock.sleep)
    assert all(r.fast_only_flags), r.fast_only_flags


def test_falls_back_for_resolvers_that_do_not_accept_fast_only():
    clock = FakeClock()
    r = ScriptedResolver([_el(), _el()])      # find(description) only
    res = wait_for("Save button", "click", resolver=r,
                   hit_test=lambda x, y: _el(), clock=clock, sleep=clock.sleep)
    assert res.ok is True
