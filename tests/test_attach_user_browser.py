"""Act on the browser the eagle already opened, instead of squinting at it.

The gap, from a real MakerWorld session: `web_agency` hit a Cloudflare wall in
the eagle's own browser, correctly fell back to `browser_control` — which
opens a SEPARATE Chrome — and thereby lost every structural tool it had. The
page rendered perfectly on screen, and the eagle was reduced to `screen_find`
(blind, because Chrome publishes nothing to the accessibility bus) and vision
(5.8s, ~650px off). It could see the search bar and had no way to touch it.

`prompt.txt` already warns about this trap. Nothing made it avoidable.

The fix is in Playwright, which is already installed: launch that Chrome with
a debug port and `connect_over_cdp` to it. Measured on a real window — 715
controls and the search box, straight out of the DOM.

Checked here rather than assumed: this is CDP, so it is the same mechanism a
separate Go daemon would use. No new runtime, no second binary to install on
three operating systems.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions import browser_control as BC  # noqa: E402
from actions.grounding.web import attach  # noqa: E402


# ── the launch side ─────────────────────────────────────────────────────────

def test_the_user_facing_chrome_is_launched_with_a_debug_port():
    """Without this the window is unreachable and the eagle is back to
    guessing at pixels."""
    src = (Path(__file__).resolve().parent.parent /
           "actions" / "browser_control.py").read_text()
    assert "--remote-debugging-port" in src


def test_the_port_is_a_named_constant_not_scattered_literals():
    assert isinstance(attach.DEBUG_PORT, int)
    assert 1024 < attach.DEBUG_PORT < 65536


def test_the_debug_port_binds_to_localhost_only():
    """A debug port is full control of the browser. It must not be reachable
    off the machine."""
    src = (Path(__file__).resolve().parent.parent /
           "actions" / "browser_control.py").read_text()
    assert "--remote-debugging-address=127.0.0.1" in src


# ── the attach side ─────────────────────────────────────────────────────────

class _Page:
    def __init__(self, url): self._url = url
    @property
    def url(self): return self._url


class _Ctx:
    def __init__(self, pages): self.pages = pages


class _Browser:
    def __init__(self, contexts): self.contexts = contexts
    def close(self): pass


def test_attaching_returns_the_page_that_is_actually_open(monkeypatch):
    page = _Page("https://makerworld.com/en")
    monkeypatch.setattr(attach, "_connect",
                        lambda port: _Browser([_Ctx([page])]))
    got = attach.attached_page()
    assert got is not None
    assert got.url == "https://makerworld.com/en"


def test_nothing_listening_returns_none_rather_than_raising(monkeypatch):
    def refuse(port):
        raise ConnectionRefusedError("nothing on 9222")
    monkeypatch.setattr(attach, "_connect", refuse)
    assert attach.attached_page() is None


def test_a_browser_with_no_pages_returns_none(monkeypatch):
    monkeypatch.setattr(attach, "_connect", lambda port: _Browser([_Ctx([])]))
    assert attach.attached_page() is None


def test_a_browser_with_no_contexts_returns_none(monkeypatch):
    monkeypatch.setattr(attach, "_connect", lambda port: _Browser([]))
    assert attach.attached_page() is None


def test_the_last_page_wins_because_it_is_the_one_just_opened(monkeypatch):
    """browser_control opens a new tab for the thing being worked on."""
    pages = [_Page("https://old.example"), _Page("https://makerworld.com/en")]
    monkeypatch.setattr(attach, "_connect", lambda port: _Browser([_Ctx(pages)]))
    assert attach.attached_page().url == "https://makerworld.com/en"


def test_available_is_false_when_nothing_is_listening(monkeypatch):
    monkeypatch.setattr(attach, "_connect",
                        lambda port: (_ for _ in ()).throw(OSError("refused")))
    assert attach.available() is False


def test_available_is_true_when_a_page_is_reachable(monkeypatch):
    monkeypatch.setattr(attach, "_connect",
                        lambda port: _Browser([_Ctx([_Page("https://x")])]))
    assert attach.available() is True
