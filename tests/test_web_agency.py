"""The tool the model actually calls.

The contract that matters is `ok`. A tool that says "I couldn't confirm it
clicked" and gets read as success is the exact bug ToolResult exists to make
impossible, and clicking is where it would bite hardest.

A second contract matters just as much and is easier to miss: this tool must
never *raise*. `EagleBrowser._submit` raises `TimeoutError` when a call
outlives its deadline — exactly what a slow click does — and `RuntimeError`
when the browser thread has died. A page that hangs mid-click must come back
as `ToolResult(ok=False)` with honest, actionable guidance, not as an
unhandled exception reaching whatever dispatches tool calls.
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actions.web_agency as web_agency_module           # noqa: E402
from actions.web_agency import web_agency                # noqa: E402
from core.tool_result import ToolResult                  # noqa: E402
from tests.web_fakes import TYPABLE, FakePage, record     # noqa: E402

PAGE = [
    record("e0", "Home", "link", top=0),
    record("e1", "Sign in", "button", top=40),
    record("e2", "Search", "searchbox", top=80, states=TYPABLE),
    record("e3", "Settings", "button", top=120),
    record("e4", "Complete purchase", "button", top=160),
    record("e5", "Log out", "button", top=200),
]


@pytest.fixture(autouse=True)
def _fresh_sense():
    """The escalation counter is process-wide on purpose — a person's suspicion
    carries between actions. Tests must not inherit each other's."""
    web_agency_module._SENSE.note_success()
    yield
    web_agency_module._SENSE.note_success()


class RaisingPage(FakePage):
    """A page whose click/fill always raises a given exception.

    Used to prove the actuation path is exception-safe: `EagleBrowser` raises
    `TimeoutError` and `RuntimeError` in real operation (see `_submit` in
    `actions/grounding/web/browser.py`), and nothing rules out some other
    unanticipated exception surfacing from a real browser call either.
    """

    def __init__(self, records, exc):
        super().__init__(records)
        self._exc = exc

    def click(self, ref):
        raise self._exc

    def fill(self, ref, text):
        raise self._exc


class FakeBrowser:
    def __init__(self, records=None, running=True, page=None):
        self._page = page if page is not None else FakePage(
            records if records is not None else PAGE)
        # Any hit-test resolves to the element itself, so "receives events"
        # passes and we are testing the tool, not the actionability layer.
        self._page.hit_test = self._hit
        self._running = running
        self.visited = []
        self.closed = False
        self.last_error = ""

    def _hit(self, x, y):
        for rec in self._page.collect():
            if (rec["left"] <= x <= rec["left"] + rec["width"]
                    and rec["top"] <= y <= rec["top"] + rec["height"]):
                return rec
        return None

    @property
    def running(self):
        return self._running

    def start(self):
        self._running = True

    def page(self):
        return self._page if self._running else None

    def goto(self, url):
        self.visited.append(url)
        return url

    def close(self):
        self.closed = True
        self._running = False


def _call(action, browser, **params):
    return web_agency({"action": action, **params}, browser=browser)


# ── the brief's original coverage ────────────────────────────────────────


def test_open_navigates_and_reports_what_it_found():
    b = FakeBrowser()
    result = _call("open", b, url="https://example.test/")
    assert isinstance(result, ToolResult) and result.ok is True
    assert b.visited == ["https://example.test/"]
    assert "6" in result.message or "controls" in result.message


def test_look_lists_the_controls_by_name_not_by_coordinate():
    result = _call("look", FakeBrowser())
    assert result.ok is True
    assert "Sign in" in result.message and "Search" in result.message
    # No coordinate pair anywhere in what the model is shown. A hallucinated
    # "(1420, 337)" on a YouTube page is the failure this design exists to end;
    # the model cannot invent a coordinate it was never given.
    assert re.search(r"\d+\s*,\s*\d+", result.message) is None


def test_look_on_a_thin_page_escalates_and_says_so():
    b = FakeBrowser(records=PAGE[:1])
    result = _call("look", b)
    assert result.ok is True
    assert result.data.get("tier") == "screenshot"


def test_click_acts_and_confirms():
    b = FakeBrowser()
    result = _call("click", b, description="the Sign in button")
    assert result.ok is True
    assert "Sign in" in result.message


def test_clicking_something_that_is_not_there_fails_with_guidance():
    result = _call("click", FakeBrowser(), description="the parachute")
    assert result.ok is False
    assert result.guidance      # a concrete next step, not just an apology
    assert "look" in result.guidance.lower()


def test_a_committing_control_is_refused_and_the_reason_is_returned():
    b = FakeBrowser()
    result = _call("click", b, description="Complete purchase")
    assert result.ok is False
    assert "purchase" in result.message.lower()
    assert "user" in result.guidance.lower()
    # The guard fires BEFORE anything is sent to the browser. A refusal that
    # arrives after the click already happened is not a refusal.
    assert b._page.clicked == []


def test_type_fills_a_field():
    b = FakeBrowser()
    result = _call("type", b, description="the Search field", text="eagles")
    assert result.ok is True


def test_typing_into_something_that_is_not_a_field_fails_honestly():
    b = FakeBrowser()
    result = _call("type", b, description="the Sign in button", text="x")
    assert result.ok is False
    assert "editable" in (result.message + result.guidance).lower()


def test_an_auth_wall_is_reported_rather_than_worked_around():
    b = FakeBrowser(records=[
        {"ref": "e0", "name": "Email", "role": "textbox", "left": 0, "top": 0,
         "width": 60, "height": 20, "states": ["ENABLED", "SENSITIVE",
                                               "VISIBLE", "SHOWING",
                                               "EDITABLE"], "value": ""},
        {"ref": "e1", "name": "Password", "role": "password", "left": 0,
         "top": 40, "width": 60, "height": 20,
         "states": ["ENABLED", "SENSITIVE", "VISIBLE", "SHOWING", "EDITABLE"],
         "value": ""},
    ])
    result = _call("look", b)
    assert result.data.get("needs_human")
    assert "sign in" in result.message.lower()


def test_a_browser_that_will_not_start_fails_with_a_usable_next_step():
    b = FakeBrowser(running=False)
    b.start = lambda: None          # refuses to come up
    b.last_error = "chromium is not installed"
    result = _call("look", b)
    assert result.ok is False
    assert "playwright install" in result.guidance.lower()


def test_an_unknown_action_is_refused_with_the_list_of_real_ones():
    result = _call("teleport", FakeBrowser())
    assert result.ok is False
    assert "look" in result.guidance


def test_close_shuts_the_browser_down():
    b = FakeBrowser()
    assert _call("close", b).ok is True
    assert b.closed is True


def test_two_failed_clicks_arm_the_escalation_for_the_next_look():
    b = FakeBrowser()
    _call("click", b, description="the parachute")
    _call("click", b, description="the submarine")
    result = _call("look", b)
    assert result.data.get("tier") == "screenshot"


# ── Correction 1: the actuation path must never raise ───────────────────
#
# These are the tests that discriminate the brief's `lambda _el: page.click
# (ref)` (passed unwrapped into `act_and_verify`, which calls `act(element)`
# without a try/except) from the corrected version. Against the brief's
# code, each of these raises out of `web_agency()` — pytest reports it as an
# ERROR, not a clean assertion failure — because nothing between
# `page.click()` and this test catches the exception. Against the fix, each
# one returns a `ToolResult` with `ok=False`.


def test_actuation_timeout_is_reported_as_unknown_not_as_failure():
    """A TimeoutError means the browser call was abandoned by the caller,
    not cancelled at the browser (see `EagleBrowser._submit`) — the click may
    still have landed. The message must say the outcome is unknown, not
    assert it failed, and the guidance must send the model back to look."""
    b = FakeBrowser(page=RaisingPage(
        PAGE, TimeoutError("browser call exceeded 5.0s")))
    result = _call("click", b, description="the Sign in button")
    assert isinstance(result, ToolResult)
    assert result.ok is False
    assert "unknown" in result.message.lower()
    # The message must not claim the click definitely failed — it must say,
    # explicitly, that failure is not what happened here.
    assert "not a failure" in result.message.lower()
    assert "look" in result.guidance.lower()


def test_actuation_dead_browser_thread_gets_distinct_guidance():
    """A RuntimeError from a dead browser thread is a different situation
    from a timeout — nothing was even sent to the browser — and must get
    guidance that says so, not the same "go look" text a timeout gets."""
    timeout_result = _call("click", FakeBrowser(page=RaisingPage(
        PAGE, TimeoutError("browser call exceeded 5.0s"))),
        description="the Sign in button")
    dead_result = _call("click", FakeBrowser(page=RaisingPage(
        PAGE, RuntimeError("browser thread is not running"))),
        description="the Sign in button")

    assert dead_result.ok is False
    assert dead_result.guidance != timeout_result.guidance
    assert "open" in dead_result.guidance.lower()


def test_actuation_unexpected_exception_never_escapes_as_a_raise():
    """Anything unanticipated the actuation raises must still come back as a
    ToolResult — a bare `except Exception` at the boundary that reports
    honestly, not one that hides what happened."""
    b = FakeBrowser(page=RaisingPage(PAGE, ValueError("boom, unforeseen")))
    result = _call("click", b, description="the Sign in button")
    assert isinstance(result, ToolResult)
    assert result.ok is False
    assert "boom" in result.message.lower()


def test_typing_actuation_timeout_also_never_raises():
    """The same exception safety applies to `fill`, not only `click`."""
    b = FakeBrowser(page=RaisingPage(
        PAGE, TimeoutError("browser call exceeded 5.0s")))
    result = _call("type", b, description="the Search field", text="eagles")
    assert isinstance(result, ToolResult)
    assert result.ok is False
    assert "unknown" in result.message.lower()


def test_a_completely_unexpected_failure_anywhere_still_returns_a_toolresult():
    """The whole tool, not just the actuation path, must be exception-safe:
    a browser whose `.page()` itself misbehaves must not crash the caller."""
    class ExplodingBrowser(FakeBrowser):
        def page(self):
            raise KeyError("no such page, somehow")

    result = _call("look", ExplodingBrowser())
    assert isinstance(result, ToolResult)
    assert result.ok is False


# ── Correction 3: the consent guard in Romanian and Spanish ─────────────


def test_a_romanian_committing_label_is_refused_before_actuation():
    b = FakeBrowser(records=[record("e0", "Plătește acum", "button", top=0)])
    result = _call("click", b, description="Plătește acum")
    assert result.ok is False
    assert "pay" in result.message.lower()
    assert "user" in result.guidance.lower()
    assert b._page.clicked == []


def test_a_spanish_committing_label_is_refused_before_actuation():
    b = FakeBrowser(records=[record("e0", "Pagar ahora", "button", top=0)])
    result = _call("click", b, description="Pagar ahora")
    assert result.ok is False
    assert "pay" in result.message.lower()
    assert "user" in result.guidance.lower()
    assert b._page.clicked == []


def test_a_romanian_benign_label_is_clicked_normally():
    b = FakeBrowser(records=[
        record("e0", "Adaugă în coș", "button", top=0),
    ])
    result = _call("click", b, description="Adaugă în coș")
    assert result.ok is True
    assert b._page.clicked == ["e0"]


def test_look_on_a_password_page_surfaces_the_handoff_reason():
    b = FakeBrowser(records=[
        record("e0", "Password", "password", top=0,
               states=["ENABLED", "SENSITIVE", "VISIBLE", "SHOWING",
                      "EDITABLE"]),
    ])
    result = _call("look", b)
    assert result.ok is True
    assert result.data.get("needs_human")
    assert "sign in" in result.message.lower()
