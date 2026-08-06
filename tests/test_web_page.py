"""The DOM, read as structure rather than pixels.

The collector runs in the page and returns one record per *named* control.
These tests own the Python half: records in, WebNodes out, with the state
vocabulary the shared actionability layer already understands.
"""
import pytest

from actions.grounding.actionability import is_editable, is_enabled, is_visible
from actions.grounding.base import Element
from actions.grounding.web.page import (COLLECT_JS, HIT_TEST_JS, WebNode,
                                        collector_truncated, element_from,
                                        nodes_from_records, ref_of)


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


def test_the_truncation_sentinel_is_dropped_as_a_node_but_read_as_a_flag():
    # COLLECT_JS appends {"truncated": true} to say it stopped counting
    # early. It has no "name", so nodes_from_records must drop it exactly
    # like any other malformed record — and collector_truncated must still
    # see it, reading the same raw records before that drop happens.
    records = [_rec(), {"truncated": True}]
    assert [n.name for n in nodes_from_records(records)] == ["Sign in"]
    assert collector_truncated(records) is True


def test_no_truncation_sentinel_means_not_truncated():
    assert collector_truncated([_rec(), _rec(ref="e1", name="Help")]) is False
    assert collector_truncated([]) is False
    assert collector_truncated(None) is False


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
    # The candidate-collection loop body must be wrapped in
    # try { ... } catch (e) { continue; } to skip a poisoned element rather
    # than failing the whole snapshot.
    #
    # This is verified by checking that the first try block after the
    # CANDIDATE_CAP check comes *before* the "const role = roleOf(el)" line —
    # proving that the outer try wraps the loop body. Pre-fix, getComputedStyle's
    # inner try would be the first one found, appearing much later.
    #
    # `roleOf(el)` replaced the loop's own inline explicit-role check when
    # that logic moved into the shared `_ACCESSIBLE_NAME_JS` fragment (task
    # 13, blocker 1) — this marker was updated to match, same position in
    # the loop body as the old "const explicit = " line it replaced.
    cap_pos = COLLECT_JS.find("if (candidates.length >= CANDIDATE_CAP)")
    role_pos = COLLECT_JS.find("const role = roleOf(el)", cap_pos)
    try_pos = COLLECT_JS.find("try {", cap_pos)

    assert cap_pos > 0, "Should find the CANDIDATE_CAP check"
    assert role_pos > cap_pos, "Should find the role check after the cap check"
    assert cap_pos < try_pos < role_pos, (
        "Outer try must wrap the loop body: first try after the cap check "
        "must come before 'const role = roleOf(el)'. This proves "
        "exception-safe wrapping."
    )
