"""browser_control on the contract — because a working tool that cannot say so
blocks a mission.

From a real voice session, twice:

    [Tool] ? browser_control no status reported (1ms)
            said: Opened in chrome: https://www.youtube.com

It WORKED. It opened the page. And because it returned a bare string, the
mission runner — which treats "no verdict" as a failure, deliberately, since a
wrong reading advances a mission past a step that never happened — marked the
step failed and blocked the whole goal.

That is the cost of the last unmigrated tools, made concrete: not a cosmetic
gap in a log, a task that cannot finish.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actions.browser_control as BC  # noqa: E402
from core.tool_result import ToolResult  # noqa: E402


def _fake_registry(monkeypatch, result="Opened in chrome: https://www.youtube.com",
                   raises=None):
    """Stand in for the session registry. Only the attributes the go_to path
    actually touches — anything more would be testing the stub."""
    class _Sess:
        def run(self, *a, **k):
            if raises:
                raise raises
            return result
        def go_to(self, *a, **k): return None

    class _Reg:
        _active_browser = "chrome"
        def has(self, b): return True
        def get(self, b=None): return _Sess()
        def note_native_url(self, u): pass
        def pop_native_url(self): return None
    monkeypatch.setattr(BC, "_registry", _Reg())


def test_an_unknown_action_is_an_explicit_failure():
    r = BC.browser_control({"action": "teleport"})
    assert isinstance(r, ToolResult) and r.ok is False
    assert "teleport" in r.message
    assert r.guidance


def test_no_action_at_all_is_a_failure():
    r = BC.browser_control({})
    assert isinstance(r, ToolResult) and r.ok is False


def test_an_unknown_action_never_opens_a_browser(monkeypatch):
    """The actual bug, caught live: this test used to call the REAL,
    unmocked `_registry`, which meant checking that a garbage action name
    was rejected also popped open a real, visible Chrome window first — a
    user watching their screen saw it flash open and close for no reason
    they could connect to anything. `_registry.get()` is what launches a
    browser; if the fix holds, a call that only ever raises must never be
    reached for an action nothing recognises.
    """
    class _RegistryThatMustNotBeTouched:
        def get(self, *a, **k):
            raise AssertionError(
                "an unknown action reached _registry.get() — this is what "
                "pops a real browser window open before discovering the "
                "action was never valid")
    monkeypatch.setattr(BC, "_registry", _RegistryThatMustNotBeTouched())

    r = BC.browser_control({"action": "teleport"})
    assert r.ok is False


def test_every_dispatched_interactive_action_is_in_the_allowlist():
    """`_INTERACTIVE_ACTIONS` is checked BEFORE the browser opens; the elif
    chain after it is checked once the browser is already running. If a new
    action is added to one and not the other, either it is refused before
    it can ever run (added to the elif chain but not the allowlist) or it
    reaches a live browser without having been vetted (added to the elif
    chain but the allowlist forgot it) — this pins the two together."""
    import inspect
    src = inspect.getsource(BC.browser_control)
    dispatched = set(re.findall(r'action == "(\w+)"', src)) - {
        "switch", "set_default", "set_default_browser", "list_browsers",
        "close_all", "close", "go_to", "search", "new_tab"}
    assert dispatched == set(BC._INTERACTIVE_ACTIONS), (
        f"the elif chain and the allowlist disagree: "
        f"{dispatched ^ set(BC._INTERACTIVE_ACTIONS)}")


def test_a_successful_open_says_ok_on_the_wire(monkeypatch):
    """The exact call from the log. `ok` must reach the model, not just the
    object — to_response() is the only thing it ever sees."""
    _fake_registry(monkeypatch)

    r = BC.browser_control({"action": "go_to", "url": "https://www.youtube.com"})
    resp = r.to_response()
    assert "ok" in resp, "success reached the model with no verdict at all"
    assert resp["ok"] is True
    assert "youtube" in resp["result"]


def test_the_word_timeout_in_an_ordinary_result_is_not_a_failure(monkeypatch):
    """A bare "timeout" substring marker used to live in `_FAILED_ANYWHERE`,
    added to catch "Type error: Timeout 30000ms exceeded." — but as an
    ANYWHERE match it also flagged perfectly ordinary results that just
    happen to contain the word, confirmed live: a search for the query
    "playwright timeout error" and a go_to to a URL with "timeout" in the
    path both came back ok=False. Both failure cases the marker was added
    for are already caught by the "type error"/"element not found"
    PREFIXES, so the bare substring was removed rather than narrowed."""
    _fake_registry(
        monkeypatch,
        result="Opened: https://example.com/docs/timeout")

    r = BC.browser_control({"action": "go_to", "url": "https://example.com/docs/timeout"})
    assert r.ok is True


def test_a_timeout_is_a_failure_not_a_result(monkeypatch):
    import concurrent.futures

    _fake_registry(monkeypatch, raises=concurrent.futures.TimeoutError())

    r = BC.browser_control({"action": "go_to", "url": "https://x.test"})
    assert r.ok is False
    assert "timed out" in r.message.lower()
    assert r.guidance


def test_an_exception_is_a_failure(monkeypatch):
    _fake_registry(monkeypatch, raises=RuntimeError("chrome died"))

    r = BC.browser_control({"action": "go_to", "url": "https://x.test"})
    assert r.ok is False and "chrome died" in r.message


def test_click_action_uses_the_dom_grounder_when_a_window_is_open(monkeypatch):
    from core.tool_result import ToolResult
    called = {}
    def fake_user_click(description):
        called["description"] = description
        return ToolResult.success(f"clicked {description!r} in the user's window")
    import actions.grounding.web.user_actions as UA
    monkeypatch.setattr(UA, "user_click", fake_user_click)
    _fake_registry(monkeypatch)
    r = BC.browser_control({"action": "click", "description": "Search"})
    assert r.ok is True
    assert called["description"] == "Search"


def test_type_action_uses_the_dom_grounder_when_a_window_is_open(monkeypatch):
    from core.tool_result import ToolResult
    called = {}
    def fake_user_type(description, text):
        called["args"] = (description, text)
        return ToolResult.success("typed into 'Search' in the user's window")
    import actions.grounding.web.user_actions as UA
    monkeypatch.setattr(UA, "user_type", fake_user_type)
    _fake_registry(monkeypatch)
    r = BC.browser_control({"action": "type", "description": "Search", "text": "watch stand"})
    assert r.ok is True
    assert called["args"] == ("Search", "watch stand")


def test_click_with_only_a_selector_uses_the_selector_path_not_an_empty_description(monkeypatch):
    """Final whole-branch review, Finding 1: `main.py`'s own tool
    declaration still documents `selector` as how to target `click`/`type`,
    unchanged since before Task 1. A caller following that documented
    interface passes `selector`, never `description` — so the dispatch must
    not silently call `user_click("")`, which can only ever fail
    (`find_node("")` matches nothing). It must reach the selector-based
    path instead.
    """
    import actions.grounding.web.user_actions as UA
    def _must_not_be_called(description):
        raise AssertionError(
            "user_click() was called with an empty/absent description — "
            "the selector-only path must not fall through to the DOM guess")
    monkeypatch.setattr(UA, "user_click", _must_not_be_called)

    clicked = {}
    class _Sess:
        def run(self, coro, timeout=None):
            try:
                coro.close()
            except Exception:
                pass
            return "Clicked selector: #search"
        def go_to(self, *a, **k): return None
        def click(self, selector=None, text=None):
            clicked["selector"] = selector
            return "coro"

    class _Reg:
        def has(self, b=None): return True
        def get(self, b=None): return _Sess()
        def pop_native_url(self): return None
    monkeypatch.setattr(BC, "_registry", _Reg())

    r = BC.browser_control({"action": "click", "selector": "#search"})
    assert clicked.get("selector") == "#search"
    assert r.ok is True


def test_type_with_only_a_selector_never_guesses_a_focused_field(monkeypatch):
    """Same regression, the unsafe half: `user_type(None, text)` falls
    through to `focus_and_type`'s best-guess-at-a-text-field heuristic,
    which can silently type into a COMPLETELY DIFFERENT field than the one
    `selector` named while still reporting ok=True. A selector-only call
    must reach the selector-based `sess.type_text` path, never `user_type`.
    """
    import actions.grounding.web.user_actions as UA
    def _must_not_be_called(description, text):
        raise AssertionError(
            "user_type() was called on a selector-only request — this is "
            "the DOM-guess path that can type into the wrong field while "
            "reporting success")
    monkeypatch.setattr(UA, "user_type", _must_not_be_called)

    typed = {}
    class _Sess:
        def run(self, coro, timeout=None):
            try:
                coro.close()
            except Exception:
                pass
            return "Text typed."
        def go_to(self, *a, **k): return None
        def type_text(self, selector=None, text="", clear_first=True):
            typed["selector"] = selector
            typed["text"] = text
            return "coro"

    class _Reg:
        def has(self, b=None): return True
        def get(self, b=None): return _Sess()
        def pop_native_url(self): return None
    monkeypatch.setattr(BC, "_registry", _Reg())

    r = BC.browser_control({"action": "type", "selector": "#search", "text": "eagle"})
    assert typed.get("selector") == "#search"
    assert typed.get("text") == "eagle"
    assert r.ok is True


def test_click_with_no_target_at_all_is_a_failure_not_a_success(monkeypatch):
    """Re-review of the `selector` fallback fix: that path was DEAD CODE
    before the fallback existed, so `click()`'s own refusal prose
    ("No selector or text provided.") was never checked against a verdict
    and fell through to `settled()` as ok=True.

    The stub's `click` implements the method and returns the exact refusal
    prose a real `BrowserSession.click` gives back when neither a selector
    nor text is named. `_fake_registry`'s bare stub (no `click` at all)
    can't be used here: calling a method that doesn't exist raises
    `AttributeError` before `sess.run` is ever reached, and
    `browser_control`'s generic `except Exception` reports that as a
    failure too — matching this test's assertion for entirely the wrong
    reason, even with `_is_refusal` never wired into the dispatch."""
    class _Sess:
        def run(self, coro, timeout=None):
            try:
                coro.close()
            except Exception:
                pass
            return "No selector or text provided."
        def go_to(self, *a, **k): return None
        def click(self, selector=None, text=None): return "coro"

    class _Reg:
        def has(self, b=None): return True
        def get(self, b=None): return _Sess()
        def pop_native_url(self): return None
    monkeypatch.setattr(BC, "_registry", _Reg())

    r = BC.browser_control({"action": "click"})
    assert r.ok is False
    assert "No selector or text provided." in r.message


def test_click_at_a_selector_the_page_does_not_have_is_a_failure(monkeypatch):
    """Same gap: `click(selector=...)` against a selector the page doesn't
    have returns "Element not found (timeout)." — prose, not an exception —
    and that also used to read as ok=True.

    As above, the stub's `click` is a real method returning real refusal
    prose, not an absent one that would fail via `AttributeError` instead
    of via the `_is_refusal` check this test exists to pin."""
    class _Sess:
        def run(self, coro, timeout=None):
            try:
                coro.close()
            except Exception:
                pass
            return "Element not found (timeout)."
        def go_to(self, *a, **k): return None
        def click(self, selector=None, text=None): return "coro"

    class _Reg:
        def has(self, b=None): return True
        def get(self, b=None): return _Sess()
        def pop_native_url(self): return None
    monkeypatch.setattr(BC, "_registry", _Reg())

    r = BC.browser_control({"action": "click", "selector": "#nonexistent-thing"})
    assert r.ok is False
    assert "Element not found (timeout)." in r.message


def test_type_with_neither_description_nor_selector_refuses_outright(monkeypatch):
    """`type_text(selector=None, ...)` falls through to
    `page.locator(":focus")` — a guess at whatever currently has focus,
    which can type into the wrong field while still reporting success. A
    request naming no target must be refused before it ever reaches that
    guess, not merely reported as failed after the fact.

    `sess.type_text` is evaluated as an argument to `sess.run(...)` before
    `run` is ever called — so a stub missing `type_text` entirely raises
    `AttributeError` on the argument expression, which also happens to
    read as ok=False, "proving" the session was never touched for the
    wrong reason. Give the stub a real `type_text` (in addition to `run`)
    that also raises if called, so a regression that reaches `type_text`
    first is caught cleanly instead of accidentally passing via a missing
    method.

    Checking `r.ok is False` alone is not enough here: `browser_control`'s
    outer `except Exception` catches ANY exception from the dispatch,
    including the `AssertionError` this stub raises if touched, and
    reports it as an ordinary failure too — so a regression that removes
    the outright-refuse guard and lets `type_text` be reached would still
    read ok=False, just via the stub's assertion instead of the guard's
    own refusal. Asserting on the exact refusal message is what tells
    the two apart."""
    class _SessionThatMustNotBeTouched:
        def run(self, *a, **k):
            raise AssertionError(
                "type_text() was reached with neither description nor "
                "selector — this is the page.locator(':focus') guess path")
        def go_to(self, *a, **k): return None
        def type_text(self, *a, **k):
            raise AssertionError(
                "type_text() was reached with neither description nor "
                "selector — this is the page.locator(':focus') guess path")

    class _Reg:
        def has(self, b=None): return True
        def get(self, b=None): return _SessionThatMustNotBeTouched()
        def pop_native_url(self): return None
    monkeypatch.setattr(BC, "_registry", _Reg())

    r = BC.browser_control({"action": "type", "text": "eagle"})
    assert r.ok is False
    assert r.message == (
        "Type error: no description or selector given — refusing to guess "
        "a focused field.")


def test_type_at_a_selector_the_page_does_not_have_is_a_failure(monkeypatch):
    """`type_text(selector=...)` against a selector that never resolves
    times out inside Playwright and returns "Type error: ..." prose — also
    used to read as ok=True.

    The stub's `type_text` is a real method returning the real timeout
    prose, not an absent one — see the two `click` tests above for why an
    absent method makes this pass for the wrong reason."""
    class _Sess:
        def run(self, coro, timeout=None):
            try:
                coro.close()
            except Exception:
                pass
            return "Type error: Timeout 30000ms exceeded."
        def go_to(self, *a, **k): return None
        def type_text(self, selector=None, text="", clear_first=True): return "coro"

    class _Reg:
        def has(self, b=None): return True
        def get(self, b=None): return _Sess()
        def pop_native_url(self): return None
    monkeypatch.setattr(BC, "_registry", _Reg())

    r = BC.browser_control({"action": "type", "selector": "#nonexistent-thing", "text": "eagle"})
    assert r.ok is False
    assert "Type error: Timeout 30000ms exceeded." in r.message


def test_look_action_reports_whats_on_the_page(monkeypatch):
    from core.tool_result import ToolResult
    import actions.grounding.web.user_actions as UA
    monkeypatch.setattr(UA, "user_look", lambda: ToolResult.success("3 controls: Home; Search; Download"))
    _fake_registry(monkeypatch)
    r = BC.browser_control({"action": "look"})
    assert r.ok is True
    assert "Home" in r.message


def test_the_mission_runner_now_accepts_a_successful_open(monkeypatch):
    """End to end on the thing that actually broke: the runner must read the
    verdict and pass the step."""
    from core.mission import Step
    from core.mission_runners import build_runners

    _fake_registry(monkeypatch, result="Opened in chrome: https://x.test")

    ok, detail = build_runners()["browser_open"](
        Step(intent="open it", url="https://x.test"))
    assert ok is True, f"a successful open still reads as failure: {detail}"
