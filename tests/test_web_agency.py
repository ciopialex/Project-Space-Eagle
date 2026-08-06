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


def test_look_says_so_when_the_collector_had_to_stop_counting():
    # ToolResult.data never reaches the model (see this module's docstring)
    # — the truncation note has to live in `message`, or it does not exist
    # as far as the model is concerned.
    b = FakeBrowser(records=PAGE + [{"truncated": True}])
    result = _call("look", b)
    assert result.ok is True
    assert result.data.get("truncated") is True
    assert "stopped at" in result.message.lower()


def test_look_says_nothing_about_truncation_when_nothing_was_dropped():
    result = _call("look", FakeBrowser())
    assert result.data.get("truncated") is False
    assert "stopped at" not in result.message.lower()


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


class _DyingPage(RaisingPage):
    """Records that it raised, so the browser can report its thread as gone."""

    def __init__(self, records, exc):
        super().__init__(records, exc)
        self.raised = False

    def click(self, ref):
        self.raised = True
        raise self._exc

    def fill(self, ref, text):
        self.raised = True
        raise self._exc


def test_a_dead_browser_thread_may_claim_nothing_was_sent():
    """When the worker thread dies mid-call, nothing reached the page and the
    tool is entitled to say so — a stronger, more useful claim than a
    timeout's "unknown".

    The browser must read as running while the tool sets the call up (or
    `_ready` short-circuits before actuation is ever attempted) and as dead
    once the page has raised. That ordering is what `EagleBrowser` actually
    produces when its thread goes away in flight.
    """
    class Dying(FakeBrowser):
        @property
        def running(self):
            return not self._page.raised

        def page(self):
            return self._page

    dead = Dying(page=_DyingPage(
        PAGE, RuntimeError("browser thread is not running")))
    result = _call("click", dead, description="the Sign in button")

    assert result.ok is False
    assert "nothing was sent" in result.guidance.lower()
    assert "open" in result.guidance.lower()
    assert dead._page.clicked == []


def test_a_runtime_error_from_a_live_browser_must_not_claim_nothing_was_sent():
    """The critical distinction. `EagleBrowser` re-raises worker-side
    exceptions with their original types, so a RuntimeError from *inside* the
    page call is indistinguishable by type from a dead thread. Claiming
    "nothing was sent to the page" there would be a specific, potentially
    false statement about whether the action landed — the exact class of lie
    `core/tool_result.py` exists to prevent."""
    live = FakeBrowser(page=RaisingPage(
        PAGE, RuntimeError("something failed inside the page call")))
    timeout = FakeBrowser(page=RaisingPage(
        PAGE, TimeoutError("browser call exceeded 5.0s")))

    live_result = _call("click", live, description="the Sign in button")
    timeout_result = _call("click", timeout, description="the Sign in button")

    assert live_result.ok is False
    assert "nothing was sent" not in live_result.guidance.lower()
    assert "nothing was sent" not in live_result.message.lower()
    # Still a different situation from a timeout, and still actionable.
    assert live_result.guidance != timeout_result.guidance
    assert live_result.guidance.strip()


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


# ── the stale-ref bug: a ref that goes bad between resolve and act ─────────
#
# Found live: a navigation (redirect, SPA route change, form submit) between
# `find_node` stamping a ref and the browser actually using it leaves that
# ref pointing at nothing. `_act_with_reresolve` (actions/web_agency.py) is
# meant to catch that failure and retry once against a freshly re-resolved
# node. This pins the retry itself, deterministically and instantly, against
# a fake — the live version in test_web_live_smoke.py proves the same thing
# is true against real Playwright timeouts and real elapsed time.


class StaleThenFreshPage(FakePage):
    """`collect()` reports ref "e1" for the Sign in button until a click on
    "e1" is actually attempted and fails — from that point on it reports
    "e1-fresh" instead, and only that ref can be clicked. Mirrors a page
    whose DOM has moved on since the node was first resolved: the ref that
    was correct at resolve time is wrong by the time it is used, and the
    *next* resolve is what would have found the ref that now works.
    """

    def __init__(self, records):
        super().__init__(records)
        self.invalidated = False

    def collect(self):
        recs = super().collect()
        if not self.invalidated:
            return recs
        return [dict(r, ref="e1-fresh") if r["ref"] == "e1" else r
                for r in recs]

    def click(self, ref):
        if ref == "e1" and not self.invalidated:
            self.invalidated = True
            raise TimeoutError("stale ref: element detached from the DOM")
        if ref == "e1-fresh":
            self.clicked.append(ref)
            return
        raise AssertionError(f"unexpected ref reached click(): {ref!r}")


def test_a_stale_ref_is_retried_once_against_a_fresh_resolve():
    b = FakeBrowser(page=StaleThenFreshPage(PAGE))
    result = _call("click", b, description="the Sign in button")

    assert result.ok is True
    # The stale "e1" was attempted and failed; the click that actually
    # landed used the re-resolved "e1-fresh" ref, not the original one.
    assert b._page.clicked == ["e1-fresh"]


# ── blocker 2: the retry path must re-gate, not just re-resolve ────────────
#
# Whole-branch review finding: `_act_with_reresolve`'s retry used to
# re-resolve `description` and actuate the fresh ref WITHOUT re-running the
# consent gate at all. A page that swaps in a different, irreversible
# control between the first failed attempt and the retry — the ref-reuse
# bug's shape, since every `collect()` renumbers `data-ae-ref` from scratch
# — would get that new control clicked, ungated. This pins the retry itself
# against a fake grounder that returns a DIFFERENT node on each call,
# instantly and deterministically; the live version in
# test_web_live_smoke.py proves the same thing end to end through the real
# `web_agency()` entry point against a real browser.

class _SequenceGrounder:
    """The narrow slice of `WebGrounder` `_act_with_reresolve` needs:
    `find_node()`, returning one prearranged node per call. No `best_match`
    fuzzy text matching involved — this test is about the retry re-running
    the gate, not about description matching, so the fake makes that
    explicit rather than relying on real matching happening to behave."""

    def __init__(self, nodes):
        self._nodes = list(nodes)
        self.calls = 0

    def find_node(self, _description):
        node = self._nodes[min(self.calls, len(self._nodes) - 1)]
        self.calls += 1
        return node


def test_the_retry_path_re_gates_the_freshly_resolved_node():
    from actions.grounding.web.consent import irreversible_reason
    from actions.grounding.web.page import nodes_from_records
    from actions.web_agency import _ConsentBlocked, _act_with_reresolve, ref_of

    benign, irreversible = nodes_from_records([
        record(ref="e1", name="Continue", role="button"),
        record(ref="e9", name="Complete purchase", role="button"),
    ])
    grounder = _SequenceGrounder([benign, irreversible])
    clicked: list[str] = []

    def actuate(ref):
        if ref == ref_of(benign):
            raise TimeoutError("stale ref: element detached from the DOM")
        clicked.append(ref)          # would only fire on a real bypass

    def gate_check(node):
        reason = irreversible_reason(node.name, node.role)
        if reason:
            raise _ConsentBlocked(f"Refused: {reason}", "ask the user")

    with pytest.raises(_ConsentBlocked):
        _act_with_reresolve(grounder, "the Continue button", gate_check,
                            actuate)

    assert clicked == [], (
        "the retry actuated the freshly re-resolved irreversible control "
        "without ever consulting the gate")
