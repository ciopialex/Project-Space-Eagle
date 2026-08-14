"""A mission must never leave a browser behind, and never open a blank one.

Reported live: "a blank page keeps opening out of nowhere."

The cause: `user_window()` called `_registry.get()`, which CREATES a session
and launches Chrome when none exists. `_user_click`, `_user_type` and
`_user_look` all went through it — so a CLICK step with no window open
launched an empty browser, did nothing with it, and left it running. Every
ladder that fell through to a `user_*` rung produced one.

Two rules, and the first is what actually stops the bleeding:

  Only a step that MEANS to open a page may create a window. Clicking,
  typing and reading act on a window that exists; if there is none, the
  honest answer is "there is no window", not a new empty one.

  A mission that finishes or is abandoned leaves nothing running that it
  started. Ghost processes also hold the profile lock, which is what
  previously made the eagle's own browser refuse to start and report
  "reinstall Playwright" — a wrong diagnosis caused by its own litter.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.mission_runners as R  # noqa: E402
from core.mission import Step  # noqa: E402
import pytest
from core.session_port import reset_launch_budget
from core.session_port import user_window  # noqa: E402


class _Reg:
    """A registry that records whether anyone asked it to create a session."""

    def __init__(self, exists=False):
        self.exists = exists
        self.created = 0
        self.closed = []

    def has(self, b=None):
        return self.exists

    def get(self, b=None):
        if not self.exists:
            self.created += 1
            self.exists = True
        return _Sess()

    def close_one(self, b=None):
        self.closed.append(b)
        self.exists = False
        return "closed"

    def close_all(self):
        self.closed.append("*")
        self.exists = False
        return "closed all"

    def pop_native_url(self):
        return None


class _Sess:
    def run(self, coro, timeout=None):
        try:
            coro.close()
        except Exception:
            pass
        return ""

    async def go_to(self, url):
        return ""


def _reg(monkeypatch, exists=False):
    import actions.browser_control as BC
    reg = _Reg(exists=exists)
    monkeypatch.setattr(BC, "_registry", reg)
    return reg


# ── nothing may conjure a blank window ──────────────────────────────────────

def test_clicking_with_no_window_open_does_not_launch_one(monkeypatch):
    """The reported bug, exactly."""
    reg = _reg(monkeypatch)
    ok, detail = R._user_click(Step(intent="Click the search box",
                                    target="the search box"))
    assert ok is False
    assert reg.created == 0, "opened a blank browser to click in"


def test_typing_with_no_window_open_does_not_launch_one(monkeypatch):
    reg = _reg(monkeypatch)
    ok, _ = R._user_type(Step(intent="Type laptop stand", text="laptop stand"))
    assert ok is False and reg.created == 0


def test_reading_with_no_window_open_does_not_launch_one(monkeypatch):
    reg = _reg(monkeypatch)
    ok, _ = R._user_look(Step(intent="Read the page"))
    assert ok is False and reg.created == 0


def test_browser_control_look_with_no_window_open_does_not_launch_one(monkeypatch):
    """Final whole-branch review, Finding 7: `look` was added to
    `browser_control`'s `_INTERACTIVE_ACTIONS`, but the interactive dispatch
    path called `_registry.get(browser)` — which launches a visible Chrome
    window if none exists — BEFORE the action-specific branch ever ran. A
    read-only "what's on this page" question with no browser open opened an
    empty visible window and then reported "0 controls" as a failure.
    Reachable in practice, not just theoretically: Task 5's prompt guidance
    routes read-only questions straight to `look`.
    """
    import actions.browser_control as BC
    reg = _reg(monkeypatch)
    r = BC.browser_control({"action": "look"})
    assert r.ok is False
    assert reg.created == 0, "opened a blank browser just to answer 'look'"


def test_observing_never_launches_a_window(monkeypatch):
    """The observer runs around EVERY step. If it could create, every mission
    would spawn one."""
    from core.session_port import peek_window
    reg = _reg(monkeypatch)
    port, _ = peek_window()
    assert port is None and reg.created == 0


def test_user_window_does_not_create_unless_asked(monkeypatch):
    reg = _reg(monkeypatch)
    assert user_window() == (None, None)
    assert reg.created == 0


# ── opening a page is the one thing allowed to create ───────────────────────

def test_an_open_step_may_create_a_window(monkeypatch):
    reg = _reg(monkeypatch)
    user_window(create=True)
    assert reg.created == 1


def test_an_existing_window_is_reused_rather_than_duplicated(monkeypatch):
    reg = _reg(monkeypatch, exists=True)
    user_window(create=True)
    assert reg.created == 0, "launched a second browser alongside the open one"


# ── a finished mission leaves nothing running ───────────────────────────────

def test_a_finished_mission_closes_what_it_started(monkeypatch, tmp_path):
    import actions.mission as M
    reg = _reg(monkeypatch, exists=True)
    monkeypatch.setattr(M, "_store_path", lambda: tmp_path / "m.json")
    monkeypatch.setattr(M, "_report_path", lambda: tmp_path / "s.md")
    monkeypatch.setattr(M, "_runners", lambda: {"web_click": lambda s: (True, "ok")})
    monkeypatch.setattr(M, "_observe", lambda: None)

    M.mission({"action": "start", "goal": "g", "steps": ["only step"]})
    M.mission({"action": "next"})            # completes the mission
    assert reg.closed, "the mission finished and left a browser running"


def test_an_abandoned_mission_closes_what_it_started(monkeypatch, tmp_path):
    import actions.mission as M
    reg = _reg(monkeypatch, exists=True)
    monkeypatch.setattr(M, "_store_path", lambda: tmp_path / "m.json")
    monkeypatch.setattr(M, "_report_path", lambda: tmp_path / "s.md")
    monkeypatch.setattr(M, "_runners", lambda: {})
    monkeypatch.setattr(M, "_observe", lambda: None)

    M.mission({"action": "start", "goal": "g", "steps": ["a"]})
    M.mission({"action": "abandon"})
    assert reg.closed, "abandoning left a browser running"


def test_cleanup_failing_does_not_fail_the_mission(monkeypatch, tmp_path):
    """Tidying up is best-effort. A mission that succeeded must not be
    reported as failed because a browser would not close."""
    import actions.mission as M

    class _Angry(_Reg):
        def close_all(self):
            raise RuntimeError("will not close")
        def close_one(self, b=None):
            raise RuntimeError("will not close")

    import actions.browser_control as BC
    monkeypatch.setattr(BC, "_registry", _Angry(exists=True))
    monkeypatch.setattr(M, "_store_path", lambda: tmp_path / "m.json")
    monkeypatch.setattr(M, "_report_path", lambda: tmp_path / "s.md")
    monkeypatch.setattr(M, "_runners", lambda: {"web_click": lambda s: (True, "ok")})
    monkeypatch.setattr(M, "_observe", lambda: None)

    M.mission({"action": "start", "goal": "g", "steps": ["only"]})
    assert M.mission({"action": "next"}).ok is True


@pytest.fixture(autouse=True)
def _fresh_launch_budget():
    """The launch cap is per PROCESS, and pytest runs one. Without this the
    tests exhaust each other's budget and fail for the wrong reason."""
    reset_launch_budget()
    yield
    reset_launch_budget()


# ── the runaway ceiling ─────────────────────────────────────────────────────

def test_a_loop_that_keeps_opening_windows_is_capped(monkeypatch):
    """The reported shape: something loops, opens a window each pass, and the
    machine ends up with a pile of Chromes holding the profile lock.

    Every other guard here is about not opening one WRONGLY. This is the one
    that does not need to know why — it just refuses after the cap, whatever
    the bug is.
    """
    from core.session_port import MAX_LAUNCHES, launches_used

    class _AlwaysFresh(_Reg):
        def has(self, b=None):
            return False          # never reuses, so every call wants a launch
        def pop_native_url(self):
            return None

    import actions.browser_control as BC
    reg = _AlwaysFresh()
    monkeypatch.setattr(BC, "_registry", reg)

    for _ in range(50):
        user_window(create=True)

    assert reg.created <= MAX_LAUNCHES, (
        f"opened {reg.created} browsers; the cap is {MAX_LAUNCHES}")
    assert launches_used() <= MAX_LAUNCHES


def test_the_cap_resets_when_a_mission_releases_its_browsers(monkeypatch, tmp_path):
    """A cap that never resets would break the SECOND mission of a session."""
    import actions.mission as M
    from core.session_port import launches_used

    _reg(monkeypatch, exists=True)
    monkeypatch.setattr(M, "_store_path", lambda: tmp_path / "m.json")
    monkeypatch.setattr(M, "_report_path", lambda: tmp_path / "s.md")
    monkeypatch.setattr(M, "_runners", lambda: {"web_click": lambda s: (True, "ok")})
    monkeypatch.setattr(M, "_observe", lambda: None)

    import core.session_port as SP
    SP._launches = SP.MAX_LAUNCHES          # pretend the budget is spent
    M.mission({"action": "start", "goal": "g", "steps": ["only"]})
    M.mission({"action": "next"})           # completes -> releases -> resets
    assert launches_used() == 0, "the next mission would start with no budget"
