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


def _fastest(fn, runs=5):
    """The best of a few runs, in milliseconds, with the result.

    These budgets guard ALGORITHMIC cost — that structure stays orders of
    magnitude cheaper than vision, and that nobody reintroduces a quadratic
    walk. Wall-clock on a busy machine measures the scheduler as well, and
    that noise is unbounded: this suite goes red when a second test run (or a
    second agent) shares the CPU, at 8.6ms against a 50ms budget — 6x
    headroom. The best run is the one least contaminated by other processes,
    and a genuine complexity regression is slower in EVERY run, so taking the
    minimum costs the guard nothing.
    """
    best, result = float("inf"), None
    for _ in range(runs):
        start = time.perf_counter()
        result = fn()
        best = min(best, (time.perf_counter() - start) * 1000)
    return best, result


def test_structural_lookup_is_under_50ms_on_a_2000_node_tree():
    g = AtspiGrounder(walker=_big_tree)
    elapsed, el = _fastest(lambda: g.find("the Save button"))
    assert el is not None
    assert el.center == (540, 615)
    assert elapsed < 50, f"structural grounding took {elapsed:.1f}ms"


def test_structural_hit_never_pays_the_vision_cost():
    r = GroundingResolver([AtspiGrounder(walker=_big_tree), SlowVision()],
                          cache=ElementCache(), context_fn=lambda: "test|win")
    # No cache here between runs would defeat the point, so one run only: the
    # budget is 100ms against a 150ms sleep, so this fails on MECHANISM (vision
    # ran at all), not on timing margin.
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
