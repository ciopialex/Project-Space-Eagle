from actions.grounding.base import Element
from actions.grounding.cache import ElementCache


def _el(name="Save"):
    return Element.from_bounds(name, "push button", 10, 20, 30, 40, "atspi")


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_get_returns_none_when_empty():
    cache = ElementCache()
    assert cache.get("app|win", "Save button") is None


def test_put_then_get_roundtrips_and_marks_source_cache():
    cache = ElementCache()
    cache.put("app|win", "Save button", _el())
    got = cache.get("app|win", "Save button")
    assert got is not None
    assert got.center == (25, 40)
    assert got.source == "cache"


def test_different_context_is_a_miss():
    cache = ElementCache()
    cache.put("app|win", "Save button", _el())
    assert cache.get("app|OTHER", "Save button") is None


def test_entry_expires_after_ttl():
    clock = FakeClock()
    cache = ElementCache(ttl=30.0, clock=clock)
    cache.put("app|win", "Save button", _el())
    clock.now = 29.9
    assert cache.get("app|win", "Save button") is not None
    clock.now = 30.1
    assert cache.get("app|win", "Save button") is None


def test_invalidate_context_clears_only_that_context():
    cache = ElementCache()
    cache.put("a|w", "Save button", _el())
    cache.put("b|w", "Save button", _el())
    cache.invalidate("a|w")
    assert cache.get("a|w", "Save button") is None
    assert cache.get("b|w", "Save button") is not None


def test_invalidate_all_clears_everything():
    cache = ElementCache()
    cache.put("a|w", "Save button", _el())
    cache.put("b|w", "Save button", _el())
    cache.invalidate()
    assert cache.get("a|w", "Save button") is None
    assert cache.get("b|w", "Save button") is None


def test_description_matching_is_case_and_space_insensitive():
    cache = ElementCache()
    cache.put("app|win", "Save Button", _el())
    assert cache.get("app|win", "  save   button ") is not None


def test_cached_element_keeps_its_states():
    cache = ElementCache()
    live = Element.from_bounds("Save", "push button", 10, 20, 30, 40, "atspi",
                               states=frozenset({"ENABLED", "SHOWING"}))
    cache.put("app|win", "Save button", live)
    got = cache.get("app|win", "Save button")
    assert got.has("ENABLED") is True
