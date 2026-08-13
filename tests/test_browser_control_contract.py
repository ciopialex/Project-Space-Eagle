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


def test_the_mission_runner_now_accepts_a_successful_open(monkeypatch):
    """End to end on the thing that actually broke: the runner must read the
    verdict and pass the step."""
    from core.mission import Step
    from core.mission_runners import build_runners

    _fake_registry(monkeypatch, result="Opened in chrome: https://x.test")

    ok, detail = build_runners()["browser_open"](
        Step(intent="open it", url="https://x.test"))
    assert ok is True, f"a successful open still reads as failure: {detail}"
