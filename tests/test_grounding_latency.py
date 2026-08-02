"""Pins the point of this whole plan: structure is orders of magnitude faster
than vision, and the cache is faster still. If someone reorders the resolver
chain or drops the cache, this fails."""
import time

from actions.grounding.atspi import AtspiGrounder, AtspiNode
from actions.grounding.cache import ElementCache
from actions.grounding.resolver import GroundingResolver


def _big_tree(n=2000):
    nodes = [AtspiNode(f"Widget{i}", "push button", i, i, 20, 20)
             for i in range(n)]
    nodes.append(AtspiNode("Save", "push button", 500, 600, 80, 30))
    return nodes


class SlowVision:
    name = "vision"

    def available(self):
        return True

    def find(self, description):
        time.sleep(0.15)          # stands in for a network round-trip
        return None


def test_structural_lookup_is_under_50ms_on_a_2000_node_tree():
    g = AtspiGrounder(walker=_big_tree)
    start = time.perf_counter()
    el = g.find("the Save button")
    elapsed = (time.perf_counter() - start) * 1000
    assert el is not None
    assert el.center == (540, 615)
    assert elapsed < 50, f"structural grounding took {elapsed:.1f}ms"


def test_structural_hit_never_pays_the_vision_cost():
    r = GroundingResolver([AtspiGrounder(walker=_big_tree), SlowVision()],
                          cache=ElementCache(), context_fn=lambda: "test|win")
    start = time.perf_counter()
    el = r.find("the Save button")
    elapsed = (time.perf_counter() - start) * 1000
    assert el.source == "atspi"
    assert elapsed < 100, f"resolver took {elapsed:.1f}ms — did vision run?"


def test_second_lookup_is_served_from_cache_and_is_faster():
    r = GroundingResolver([AtspiGrounder(walker=_big_tree)],
                          cache=ElementCache(), context_fn=lambda: "test|win")
    start = time.perf_counter()
    r.find("the Save button")
    cold = time.perf_counter() - start

    start = time.perf_counter()
    el = r.find("the Save button")
    warm = time.perf_counter() - start

    assert el.source == "cache"
    assert warm < cold


def test_a_name_that_is_a_role_noun_still_resolves_structurally():
    """Regression: "the Menu button" scored 0.0 and cost a 7.9s vision call."""
    tree = _big_tree(50) + [AtspiNode("Menu", "toggle button", 700, 80, 36, 46)]
    r = GroundingResolver([AtspiGrounder(walker=lambda: tree), SlowVision()],
                          cache=ElementCache(), context_fn=lambda: "test|win")
    start = time.perf_counter()
    el = r.find("the Menu button")
    elapsed = (time.perf_counter() - start) * 1000
    assert el is not None and el.source == "atspi"
    assert elapsed < 100, f"fell through to vision again ({elapsed:.1f}ms)"
