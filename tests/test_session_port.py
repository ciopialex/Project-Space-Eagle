"""Acting on the browser the eagle opened FOR the user.

The chain that blocked every real mission, traced to the end:

  1. `web_open` fails — makerworld bot-walls the eagle's headless browser.
  2. `browser_open` succeeds — the page opens in the user's Chrome.
  3. Nothing could touch it. `web_click` looks in the eagle's browser,
     `screen_click` is blind (Chrome publishes nothing to the a11y bus), and
     vision guesses at ~650px off.

Two wrong turns before the right one, both worth keeping:

- Launching that Chrome with `--remote-debugging-port` and connecting over
  CDP. No listener ever appears — Playwright's `launch_persistent_context`
  runs its own CDP channel.
- Asking `_registry.has()`. It is False after a NATIVE open, so a mission that
  had just successfully opened MakerWorld was told "no browser window is open
  for the user". `browser_control` opens natively (fast, visible,
  uncontrollable) and creates a controllable window lazily, syncing it to the
  noted url. This does the same.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from core.session_port import reset_launch_budget
from core.session_port import SessionPort, user_window  # noqa: E402


class _Sess:
    """A browser_control session, minus the browser."""

    def __init__(self, url="https://example.test", nodes=None, fail=None):
        self._url, self._nodes, self._fail = url, nodes or [], fail
        self.navigated, self.clicked, self.filled = [], [], []

    def run(self, coro, timeout=None):
        # asyncio.run, not get_event_loop().run_until_complete — the latter is
        # deprecated and warns on every call.
        import asyncio
        if self._fail:
            raise self._fail
        return asyncio.run(coro) if asyncio.iscoroutine(coro) else coro

    async def _get_page(self):
        sess = self

        class _Page:
            url = sess._url
            async def evaluate(self, script, arg=None): return sess._nodes
            async def click(self, sel, timeout=0): sess.clicked.append(sel)
            async def fill(self, sel, text, timeout=0): sess.filled.append((sel, text))
            async def goto(self, u, **k): sess.navigated.append(u)
            async def screenshot(self): return b"PNG"
            async def eval_on_selector(self, sel, script): sess.clicked.append(sel)
        return _Page()

    async def go_to(self, url):
        self.navigated.append(url)
        self._url = url
        return f"Opened {url}"


def test_a_native_open_still_yields_a_window(monkeypatch):
    """The exact bug: has() is False after a native open, and the mission was
    told there was no window while one was plainly on screen."""
    sess = _Sess()
    class _Reg:
        def has(self, b=None): return False        # native open -> False
        def get(self, b=None): return sess
        def pop_native_url(self): return "https://makerworld.com"
    import actions.browser_control as BC
    monkeypatch.setattr(BC, "_registry", _Reg())

    port, grounder = user_window()
    assert port is not None, "reported no window while one was open"
    assert grounder is not None


def test_it_follows_the_url_the_native_open_left_behind(monkeypatch):
    sess = _Sess()
    class _Reg:
        def has(self, b=None): return False
        def get(self, b=None): return sess
        def pop_native_url(self): return "https://makerworld.com"
    import actions.browser_control as BC
    monkeypatch.setattr(BC, "_registry", _Reg())

    user_window()
    assert sess.navigated == ["https://makerworld.com"], \
        "left the controllable window on about:blank"


def test_it_does_not_re_navigate_when_there_is_nothing_noted(monkeypatch):
    """`pop` clears it, so a later step is not yanked back to step one."""
    sess = _Sess()
    class _Reg:
        def has(self, b=None): return True
        def get(self, b=None): return sess
        def pop_native_url(self): return None
    import actions.browser_control as BC
    monkeypatch.setattr(BC, "_registry", _Reg())

    user_window()
    assert sess.navigated == []


def test_no_session_at_all_returns_nothing_rather_than_raising(monkeypatch):
    class _Reg:
        def has(self, b=None): return False
        def get(self, b=None): return None
        def pop_native_url(self): return None
    import actions.browser_control as BC
    monkeypatch.setattr(BC, "_registry", _Reg())
    assert user_window() == (None, None)


def test_a_registry_that_explodes_returns_nothing(monkeypatch):
    class _Reg:
        def get(self, b=None): raise RuntimeError("no browser")
        def has(self, b=None): return False
        def pop_native_url(self): return None
    import actions.browser_control as BC
    monkeypatch.setattr(BC, "_registry", _Reg())
    assert user_window() == (None, None)


# ── the port itself ─────────────────────────────────────────────────────────

def test_the_port_reads_the_url():
    assert SessionPort(_Sess(url="https://x.test")).url() == "https://x.test"


def test_the_port_collects_nodes():
    assert len(SessionPort(_Sess(nodes=[{"ref": "e0"}])).collect()) == 1


def test_the_port_clicks_by_ref():
    s = _Sess()
    SessionPort(s).click("e7")
    assert s.clicked == ['[data-ae-ref="e7"]']


def test_the_port_fills_by_ref():
    s = _Sess()
    SessionPort(s).fill("e3", "laptop stand")
    assert s.filled == [('[data-ae-ref="e3"]', "laptop stand")]


def test_a_wedged_session_does_not_raise_out_of_url():
    assert SessionPort(_Sess(fail=RuntimeError("wedged"))).url() == ""


@pytest.fixture(autouse=True)
def _fresh_launch_budget():
    """The launch cap is per PROCESS, and pytest runs one. Without this the
    tests exhaust each other's budget and fail for the wrong reason."""
    reset_launch_budget()
    yield
    reset_launch_budget()
