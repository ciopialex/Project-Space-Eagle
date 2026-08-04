"""The eagle's browser is its own, and it is not the user's.

Inheriting the user's Chrome profile would hand the eagle every session the
user has ever opened, silently, and would tie it to one visible window it has
to fight the user for. One persistent profile of its own costs one login per
site, and that login is the moment the user grants access deliberately.

These tests do not launch a browser. They pin the decisions: where the profile
lives, that it is private, that work is marshalled onto the owning thread, and
that a dead browser reports rather than raises.
"""
import threading
from pathlib import Path

import pytest

from actions.grounding.web.browser import EagleBrowser, PagePort
from core import user_paths


def test_the_profile_lives_under_the_user_data_dir_never_the_repo(tmp_path,
                                                                  monkeypatch):
    monkeypatch.setenv("AETHELARK_DATA_DIR", str(tmp_path))
    path = user_paths.browser_profile_dir()
    assert path == tmp_path / "browser"
    assert "Space-Eagle" not in str(path)


def test_the_profile_directory_is_created_owner_only(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHELARK_DATA_DIR", str(tmp_path))
    path = user_paths.browser_profile_dir()
    assert path.is_dir()
    import os
    import sys
    if sys.platform != "win32":
        assert oct(os.stat(path).st_mode & 0o777) == oct(0o700)


class FakePlaywrightPage:
    """Stands in for a Playwright Page — only the methods PagePort touches."""

    def __init__(self):
        self.evaluated = []
        self.clicked = []
        self.filled = []

    def evaluate(self, script, arg=None):
        self.evaluated.append((script, arg))
        if "elementFromPoint" in script:
            return {"ref": "e1", "name": "Sign in", "role": "button",
                    "left": 0, "top": 0, "width": 10, "height": 10,
                    "states": [], "value": ""}
        return [{"ref": "e0", "name": "Home", "role": "link", "left": 0,
                 "top": 0, "width": 10, "height": 10,
                 "states": ["ENABLED"], "value": ""}]

    def screenshot(self, **kwargs):
        return b"PNG-BYTES"

    def click(self, selector, **kwargs):
        self.clicked.append(selector)

    def fill(self, selector, value, **kwargs):
        self.filled.append((selector, value))

    @property
    def url(self):
        return "https://example.test/"


def test_pageport_actuates_by_ref_through_a_real_selector():
    raw = FakePlaywrightPage()
    port = PagePort(raw, call=lambda fn: fn())
    port.click("e7")
    assert raw.clicked == ['[data-ae-ref="e7"]']


def test_pageport_fills_by_ref():
    raw = FakePlaywrightPage()
    port = PagePort(raw, call=lambda fn: fn())
    port.fill("e2", "hello@example.test")
    assert raw.filled == [('[data-ae-ref="e2"]', "hello@example.test")]


def test_pageport_collect_returns_the_collector_records():
    port = PagePort(FakePlaywrightPage(), call=lambda fn: fn())
    assert port.collect()[0]["name"] == "Home"


def test_pageport_hit_test_passes_the_point_as_one_argument():
    raw = FakePlaywrightPage()
    port = PagePort(raw, call=lambda fn: fn())
    assert port.hit_test(4, 9)["name"] == "Sign in"
    script, arg = raw.evaluated[-1]
    assert arg == [4, 9]


def test_pageport_screenshot_comes_from_the_compositor_not_the_display():
    port = PagePort(FakePlaywrightPage(), call=lambda fn: fn())
    assert port.screenshot() == b"PNG-BYTES"


def test_every_call_is_marshalled_onto_the_owning_thread(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHELARK_DATA_DIR", str(tmp_path))
    seen = {}

    def launcher(playwright, profile, headless):
        seen["thread"] = threading.current_thread().name
        return FakePlaywrightPage()

    browser = EagleBrowser(launcher=launcher, playwright_fn=lambda: object())
    browser.start()
    try:
        caller = threading.current_thread().name
        ran_on = browser.call(lambda page: threading.current_thread().name)
        assert ran_on == seen["thread"]
        assert ran_on != caller
    finally:
        browser.close()


def test_a_browser_that_fails_to_launch_reports_rather_than_raises(tmp_path,
                                                                   monkeypatch):
    monkeypatch.setenv("AETHELARK_DATA_DIR", str(tmp_path))

    def launcher(playwright, profile, headless):
        raise RuntimeError("chromium is not installed")

    browser = EagleBrowser(launcher=launcher, playwright_fn=lambda: object())
    browser.start()
    try:
        assert browser.running is False
        assert browser.page() is None
        assert "chromium is not installed" in browser.last_error
    finally:
        browser.close()


def test_headless_defaults_to_visible_so_a_human_can_finish_a_login(monkeypatch):
    monkeypatch.delenv("AETHELARK_BROWSER_HEADLESS", raising=False)
    assert EagleBrowser().headless is False


def test_headless_can_be_switched_on_by_environment(monkeypatch):
    monkeypatch.setenv("AETHELARK_BROWSER_HEADLESS", "1")
    assert EagleBrowser().headless is True


def test_close_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHELARK_DATA_DIR", str(tmp_path))
    browser = EagleBrowser(launcher=lambda p, d, h: FakePlaywrightPage(),
                           playwright_fn=lambda: object())
    browser.start()
    browser.close()
    browser.close()
    assert browser.running is False
