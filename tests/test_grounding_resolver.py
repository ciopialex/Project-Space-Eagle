from actions.grounding.base import Element
from actions.grounding.cache import ElementCache
from actions.grounding.resolver import GroundingResolver


class StubGrounder:
    def __init__(self, name, element=None, avail=True):
        self.name = name
        self._element = element
        self._avail = avail
        self.calls = 0

    def available(self):
        return self._avail

    def find(self, description):
        self.calls += 1
        return self._element


def _el(source="atspi", x=100, y=200):
    return Element.from_bounds("Save", "push button", x, y, 0, 0, source)


def test_first_grounder_wins_and_later_ones_are_not_called():
    fast = StubGrounder("atspi", _el("atspi"))
    slow = StubGrounder("vision", _el("vision"))
    r = GroundingResolver([fast, slow], context_fn=lambda: "app|win")
    el = r.find("the Save button")
    assert el.source == "atspi"
    assert slow.calls == 0
    assert r.last_source == "atspi"


def test_falls_through_to_next_grounder_on_miss():
    fast = StubGrounder("atspi", None)
    slow = StubGrounder("vision", _el("vision"))
    r = GroundingResolver([fast, slow], context_fn=lambda: "app|win")
    assert r.find("the Save button").source == "vision"
    assert slow.calls == 1


def test_returns_none_when_all_grounders_miss():
    r = GroundingResolver([StubGrounder("atspi", None),
                           StubGrounder("vision", None)],
                          context_fn=lambda: "app|win")
    assert r.find("the Save button") is None
    assert r.last_source is None


def test_unavailable_grounders_are_skipped():
    dead = StubGrounder("atspi", _el("atspi"), avail=False)
    live = StubGrounder("vision", _el("vision"))
    r = GroundingResolver([dead, live], context_fn=lambda: "app|win")
    assert r.find("the Save button").source == "vision"
    assert dead.calls == 0


def test_result_is_cached_and_second_lookup_skips_grounders():
    slow = StubGrounder("vision", _el("vision"))
    cache = ElementCache()
    r = GroundingResolver([slow], cache=cache, context_fn=lambda: "app|win")
    assert r.find("the Save button").source == "vision"
    assert r.find("the Save button").source == "cache"
    assert slow.calls == 1


def test_cache_is_scoped_to_window_context():
    ctx = {"v": "app|one"}
    slow = StubGrounder("vision", _el("vision"))
    r = GroundingResolver([slow], cache=ElementCache(),
                          context_fn=lambda: ctx["v"])
    r.find("the Save button")
    ctx["v"] = "app|two"
    r.find("the Save button")
    assert slow.calls == 2


def test_a_grounder_that_raises_does_not_break_the_chain():
    class Exploding:
        name = "boom"

        def available(self):
            return True

        def find(self, description):
            raise RuntimeError("kaboom")

    r = GroundingResolver([Exploding(), StubGrounder("vision", _el("vision"))],
                          context_fn=lambda: "app|win")
    assert r.find("the Save button").source == "vision"


def test_context_fn_failure_does_not_break_lookup():
    def boom():
        raise RuntimeError("no window manager")

    r = GroundingResolver([StubGrounder("atspi", _el("atspi"))],
                          cache=ElementCache(), context_fn=boom)
    assert r.find("the Save button").source == "atspi"


def test_context_uses_window_id_not_title():
    """Titles change constantly (spinners, modified dots, tab counts), so a
    title-keyed cache had a 0% hit rate against a live terminal."""
    import inspect
    from actions.grounding import resolver
    src = inspect.getsource(resolver._default_context)
    assert "getwindowname" not in src
    assert "getactivewindow" in src


class SlowStub(StubGrounder):
    cost = "slow"


class FastStub(StubGrounder):
    cost = "fast"


def test_fast_only_skips_slow_grounders():
    """A polling loop must not pay a network round-trip per attempt."""
    fast = FastStub("atspi", None)
    slow = SlowStub("vision", _el("vision"))
    r = GroundingResolver([fast, slow], context_fn=lambda: "app|win")
    assert r.find("the Save button", fast_only=True) is None
    assert slow.calls == 0
    assert fast.calls == 1


def test_fast_only_false_still_uses_slow_grounders():
    slow = SlowStub("vision", _el("vision"))
    r = GroundingResolver([slow], context_fn=lambda: "app|win")
    assert r.find("the Save button").source == "vision"
    assert slow.calls == 1


def test_grounders_without_a_cost_attribute_are_treated_as_slow():
    plain = StubGrounder("legacy", _el("legacy"))
    r = GroundingResolver([plain], context_fn=lambda: "app|win")
    assert r.find("the Save button", fast_only=True) is None
