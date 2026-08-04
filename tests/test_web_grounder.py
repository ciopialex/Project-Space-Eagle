"""A fourth backend for the same protocol.

The value of this file is mostly what it does NOT contain: no new matching
rules, no new actionability logic, no new waiting loop. The web plugs into the
ones that already exist, and these tests prove it plugs in rather than
re-implements.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.grounding.base import Grounder            # noqa: E402
from actions.grounding.waiting import wait_for         # noqa: E402
from actions.grounding.web.grounder import WebGrounder  # noqa: E402
from actions.grounding.web.page import ref_of          # noqa: E402
from tests.web_fakes import TYPABLE, FakePage, record as _rec  # noqa: E402

PAGE = [
    _rec("e0", "Home", "link", top=0),
    _rec("e1", "Sign in", "button", top=40),
    _rec("e2", "Email", "textbox", top=80, states=TYPABLE),
    _rec("e3", "Search", "searchbox", top=120, states=TYPABLE),
    _rec("e4", "Settings", "button", top=160),
    _rec("e5", "Log out", "button", top=200),
]


def _grounder(records=PAGE, page=None):
    page = page or FakePage(records)
    return WebGrounder(lambda: page), page


def test_it_satisfies_the_grounder_protocol():
    g, _ = _grounder()
    assert isinstance(g, Grounder)
    assert g.name == "web" and g.cost == "fast"


def test_it_finds_a_control_by_the_words_a_person_would_use():
    g, _ = _grounder()
    el = g.find("the Sign in button")
    assert el is not None
    assert el.name == "Sign in"
    assert el.source == "web"


def test_it_finds_the_field_not_the_button_when_asked_for_a_field():
    g, _ = _grounder()
    el = g.find("the Email field")
    assert el is not None and el.name == "Email"


def test_a_description_matching_nothing_returns_none_rather_than_a_guess():
    g, _ = _grounder()
    assert g.find("the parachute") is None


def test_find_node_keeps_the_ref_that_actuation_needs():
    g, _ = _grounder()
    node = g.find_node("Sign in")
    assert node is not None and ref_of(node) == "e1"


def test_unavailable_when_there_is_no_page():
    g = WebGrounder(lambda: None)
    assert g.available() is False
    assert g.find("anything") is None


def test_available_when_a_page_is_open():
    g, _ = _grounder()
    assert g.available() is True


def test_a_page_that_explodes_never_raises_out_of_the_grounder():
    def boom():
        raise RuntimeError("browser died")

    g = WebGrounder(boom)
    assert g.available() is False
    assert g.find("Sign in") is None


def test_hit_test_reports_what_is_actually_at_a_point():
    page = FakePage(PAGE)
    page.hit_test = lambda x, y: _rec("e1", "Sign in", top=40)
    g = WebGrounder(lambda: page)
    hit = g.hit_test(45, 52)
    assert hit is not None and hit.name == "Sign in"


def test_hit_test_returns_none_when_nothing_is_there():
    g, _ = _grounder()
    assert g.hit_test(9999, 9999) is None


def test_it_drives_the_existing_wait_for_loop_unchanged():
    # The whole point of the protocol. wait_for was written for AT-SPI and is
    # not modified by this plan.
    page = FakePage(PAGE)
    page.hit_test = lambda x, y: _rec("e1", "Sign in", top=40)
    g = WebGrounder(lambda: page)

    result = wait_for("the Sign in button", "click", resolver=g,
                      hit_test=g.hit_test, timeout=0.2,
                      sleep=lambda _s: None)
    assert result.ok is True
    assert result.element is not None and result.element.name == "Sign in"


def test_wait_for_reports_the_real_reason_a_disabled_button_never_engages():
    greyed = _rec("e9", "Continue", top=240, states=["VISIBLE", "SHOWING"])
    page = FakePage(PAGE + [greyed])
    page.hit_test = lambda x, y: greyed
    g = WebGrounder(lambda: page)

    result = wait_for("the Continue button", "click", resolver=g,
                      hit_test=g.hit_test, timeout=0.05,
                      sleep=lambda _s: None)
    assert result.ok is False
    # Not "not_found" and not a silent timeout — the honest reason.
    assert result.failed_check == "enabled"
