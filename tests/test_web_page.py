"""The DOM, read as structure rather than pixels.

The collector runs in the page and returns one record per *named* control.
These tests own the Python half: records in, WebNodes out, with the state
vocabulary the shared actionability layer already understands.
"""
import pytest

from actions.grounding.actionability import is_editable, is_enabled, is_visible
from actions.grounding.base import Element
from actions.grounding.web.page import (COLLECT_JS, HIT_TEST_JS, WebNode,
                                        element_from, nodes_from_records,
                                        ref_of)


def _rec(**over):
    base = {"ref": "e0", "name": "Sign in", "role": "button",
            "left": 10, "top": 20, "width": 80, "height": 30,
            "states": ["ENABLED", "SENSITIVE", "VISIBLE", "SHOWING"],
            "value": ""}
    base.update(over)
    return base


def test_a_record_becomes_a_node_with_its_ref_intact():
    (node,) = nodes_from_records([_rec()])
    assert isinstance(node, WebNode)
    assert node.name == "Sign in"
    assert node.role == "button"
    assert node.bounds_tuple == (10, 20, 80, 30)
    assert ref_of(node) == "e0"


def test_states_arrive_as_a_frozenset_the_shared_checks_understand():
    (node,) = nodes_from_records([_rec()])
    el = element_from(node)
    assert is_visible(el) is True
    assert is_enabled(el) is True
    assert is_editable(el) is False


def test_an_editable_field_satisfies_the_fill_checks():
    (node,) = nodes_from_records([_rec(
        role="textbox", name="Email", value="a@b.c",
        states=["ENABLED", "SENSITIVE", "VISIBLE", "SHOWING", "EDITABLE"])])
    el = element_from(node)
    assert is_editable(el) is True
    assert el.value == "a@b.c"


def test_a_disabled_control_is_not_enabled():
    (node,) = nodes_from_records([_rec(states=["VISIBLE", "SHOWING"])])
    assert is_enabled(element_from(node)) is False


def test_elements_are_marked_web_sourced_so_they_never_reach_the_mouse():
    (node,) = nodes_from_records([_rec()])
    assert element_from(node).source == "web"


def test_garbage_records_are_dropped_rather_than_raising():
    records = [_rec(), {"nonsense": True}, None, _rec(ref="e2", name="Help")]
    nodes = nodes_from_records(records)
    assert [n.name for n in nodes] == ["Sign in", "Help"]


def test_a_record_with_no_name_is_dropped():
    assert nodes_from_records([_rec(name="")]) == ()


def test_coordinates_are_coerced_from_floats_because_the_dom_reports_them():
    (node,) = nodes_from_records([_rec(left=10.6, top=20.4, width=80.9,
                                       height=30.2)])
    assert node.bounds_tuple == (10, 20, 80, 30)


def test_the_collector_script_clears_stale_refs_before_it_walks():
    # Refs from the previous snapshot must not survive into this one, or a
    # click will land on whatever used to be at that ref.
    assert "removeAttribute('data-ae-ref')" in COLLECT_JS


def test_the_collector_and_hit_test_are_expressions_playwright_can_evaluate():
    for script in (COLLECT_JS, HIT_TEST_JS):
        assert script.strip().startswith("(")
        assert "=>" in script


def test_the_collector_loop_body_is_wrapped_in_try_catch_for_exception_safety():
    # Custom elements and web components can throw on property reads.
    # One hostile element must not abort the entire walk.
    # The loop body must be wrapped in try { ... } catch (e) { continue; }
    # to skip a poisoned element rather than failing the whole snapshot.
    #
    # This is verified by checking that the first try block after the
    # MAX_NODES break comes *before* the "const explicit" line — proving
    # that the outer try wraps the loop body. Pre-fix, getComputedStyle's
    # inner try would be the first one found, appearing much later.
    break_pos = COLLECT_JS.find("if (n >= MAX_NODES) break;")
    explicit_pos = COLLECT_JS.find("const explicit = ", break_pos)
    try_pos = COLLECT_JS.find("try {", break_pos)

    assert break_pos > 0, "Should find the MAX_NODES break check"
    assert explicit_pos > break_pos, "Should find explicit check after break"
    assert break_pos < try_pos < explicit_pos, (
        "Outer try must wrap the loop body: first try after break must come "
        "before 'const explicit'. This proves exception-safe wrapping."
    )
