"""The eagle may work inside the user's own Chrome. Two things must hold.

Letting go of that Chrome closes only the window the eagle opened: closing
the context of an attached browser would close the user's own tabs.

A page that bounced the eagle's private browser to a sign-in is reopened in
the user's Chrome when they allowed it, and only then.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actions.web_agency as W  # noqa: E402
from actions.grounding.web import chrome_link  # noqa: E402
from core.tool_result import ToolResult  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _forget_routes():
    yield
    W._ACTIVE.clear()
    W._CHROME_SITES.clear()


class _Page:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    def is_closed(self):
        return self.closed


class _Ctx:
    def __init__(self):
        self.closed = False
        self.opened = _Page()

    def close(self):
        self.closed = True

    @contextmanager
    def expect_page(self):
        class _Ev:
            value = self.opened
        yield _Ev()


class _Remote:
    def __init__(self):
        self.contexts = [_Ctx()]
        self.closed = False

    def new_browser_cdp_session(self):
        class _Cdp:
            def send(self, *a, **k):
                return {"targetId": "t"}
        return _Cdp()

    def is_connected(self):
        return not self.closed

    def close(self):
        self.closed = True


def test_letting_go_never_closes_the_users_chrome():
    remote = _Remote()

    class _Pw:
        class chromium:
            @staticmethod
            def connect_over_cdp(ws, timeout=None):
                return remote

        def stop(self):
            pass

    found = (chrome_link.Installed("Chrome", Path("/nowhere"), ""), "ws://x")
    b = chrome_link.ChromeBrowser(find_fn=lambda: found, playwright_fn=_Pw)
    b.start()
    assert b.running
    b.close()

    ctx = remote.contexts[0]
    assert ctx.opened.closed, "the eagle's own window is closed"
    assert remote.closed, "the connection is dropped"
    assert not ctx.closed, "the user's Chrome context must never be closed"


class _Browser:
    def __init__(self, name, url):
        self.name, self.url, self.running = name, url, True

    def start(self):
        pass

    def page(self):
        url = self.url

        class _P:
            def url(self):
                return url
        return _P()


def _wire(monkeypatch, chrome_ok):
    eagle = _Browser("eagle", "https://accounts.example.com/signin?next=/inbox")
    chrome = _Browser("chrome", "https://mail.example.com/inbox")
    monkeypatch.setattr("actions.grounding.web.browser.default_browser", lambda: eagle)
    monkeypatch.setattr(W, "_chrome", lambda: chrome if chrome_ok else None)
    monkeypatch.setattr(W, "_dispatch",
                        lambda params, browser, action: ToolResult.success(browser.name))
    W._ACTIVE.clear()
    W._CHROME_SITES.clear()
    return eagle, chrome


def test_a_sign_in_bounce_moves_the_task_to_the_users_chrome(monkeypatch):
    eagle, chrome = _wire(monkeypatch, chrome_ok=True)
    r = W.web_agency({"action": "open", "url": "mail.example.com/inbox"})
    assert r.ok and r.data.get("in_chrome")
    assert W._ACTIVE["browser"] is chrome

    r = W.web_agency({"action": "open", "url": "https://mail.example.com/other"})
    assert r.message == "chrome", "a site that needed Chrome goes straight there"

    eagle.url = "https://news.example.org/"
    r = W.web_agency({"action": "open", "url": "https://news.example.org/"})
    assert r.message == "eagle" and W._ACTIVE["browser"] is eagle


def test_without_permission_it_stays_in_the_eagles_browser(monkeypatch):
    eagle, _ = _wire(monkeypatch, chrome_ok=False)
    r = W.web_agency({"action": "open", "url": "https://mail.example.com/inbox"})
    assert r.message == "eagle" and not r.data.get("in_chrome")
    assert W._ACTIVE["browser"] is eagle
