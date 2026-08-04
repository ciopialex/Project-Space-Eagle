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
import time
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


# ── review round 1 ────────────────────────────────────────────────────────
#
# The four tests below were added after the first coordinator review found
# real defects: the page() seam had no proof it marshals at all (the
# marshalling test above only exercises call()); PagePort.url() read the page
# inline and swallowed failure into ""; a failed launch permanently bricked
# the browser because start() treated "thread alive" as "already running";
# and _submit()'s timeout raced Playwright's own action timeout, so a caller
# told a click failed could still have that click fire afterwards.


def test_page_marshals_onto_the_owning_thread_not_just_call(tmp_path,
                                                             monkeypatch):
    """`page()` builds its own `call` closure independently of `.call()` — a
    seam that could silently fall back to running inline. Prove it can't:
    a `page()` that used the un-marshalled default would run `collect()` on
    this test's own thread, not "EagleBrowser"."""
    monkeypatch.setenv("AETHELARK_DATA_DIR", str(tmp_path))
    seen = {}

    class ThreadRecordingPage(FakePlaywrightPage):
        def evaluate(self, script, arg=None):
            seen["thread"] = threading.current_thread().name
            return super().evaluate(script, arg)

    browser = EagleBrowser(launcher=lambda p, d, h: ThreadRecordingPage(),
                           playwright_fn=lambda: object())
    browser.start()
    try:
        caller = threading.current_thread().name
        browser.page().collect()
        assert seen["thread"] == "EagleBrowser"
        assert seen["thread"] != caller
    finally:
        browser.close()


def test_pageport_url_also_marshals_and_no_longer_swallows_failure(tmp_path,
                                                                    monkeypatch):
    monkeypatch.setenv("AETHELARK_DATA_DIR", str(tmp_path))
    seen = {}

    class ThreadRecordingPage(FakePlaywrightPage):
        @property
        def url(self):
            seen["thread"] = threading.current_thread().name
            return "https://example.test/"

    browser = EagleBrowser(launcher=lambda p, d, h: ThreadRecordingPage(),
                           playwright_fn=lambda: object())
    browser.start()
    try:
        assert browser.page().url() == "https://example.test/"
        assert seen["thread"] == "EagleBrowser"
    finally:
        browser.close()


def test_goto_marshals_onto_the_owning_thread_and_returns_the_landed_url(
        tmp_path, monkeypatch):
    monkeypatch.setenv("AETHELARK_DATA_DIR", str(tmp_path))
    seen = {}

    class NavigablePage(FakePlaywrightPage):
        def goto(self, url, timeout=None, wait_until=None):
            seen["thread"] = threading.current_thread().name
            self.navigated_to = url

    browser = EagleBrowser(launcher=lambda p, d, h: NavigablePage(),
                           playwright_fn=lambda: object())
    browser.start()
    try:
        landed = browser.goto("https://example.test/page")
        assert landed == "https://example.test/"
        assert seen["thread"] == "EagleBrowser"
    finally:
        browser.close()


def test_start_retries_after_a_failed_launch(tmp_path, monkeypatch):
    """A failed start() must not brick the object forever — a stale profile
    lock or chromium mid-install is transient, and default_browser() is a
    process-wide singleton, so a permanent no-op here bricks web grounding
    for the whole process."""
    monkeypatch.setenv("AETHELARK_DATA_DIR", str(tmp_path))
    attempts = {"n": 0}

    def launcher(playwright, profile, headless):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("stale profile lock")
        return FakePlaywrightPage()

    browser = EagleBrowser(launcher=launcher, playwright_fn=lambda: object())
    browser.start()
    assert browser.running is False
    assert "stale profile lock" in browser.last_error

    browser.start()
    try:
        assert attempts["n"] == 2
        assert browser.running is True
    finally:
        browser.close()


def test_a_job_still_queued_when_its_caller_gives_up_does_not_run(tmp_path,
                                                                   monkeypatch):
    """The half of finding 1 that matters most: if _submit()'s wait() times
    out while a job is still sitting behind a slow one, that job must never
    fire — the caller has already been told it failed."""
    monkeypatch.setenv("AETHELARK_DATA_DIR", str(tmp_path))
    browser = EagleBrowser(launcher=lambda p, d, h: FakePlaywrightPage(),
                           playwright_fn=lambda: object())
    browser.start()
    try:
        unblock = threading.Event()
        ran = []

        def blocker(page):
            unblock.wait(5)
            return "blocker-done"

        def abandoned(page):
            ran.append("ran")
            return "should never happen"

        occupier = threading.Thread(
            target=lambda: browser.call(blocker, timeout=5))
        occupier.start()
        time.sleep(0.1)  # let `blocker` actually start on the browser thread

        with pytest.raises(TimeoutError):
            browser.call(abandoned, timeout=0.2)

        unblock.set()
        occupier.join(5)
        assert ran == []
    finally:
        browser.close()


def test_a_job_already_running_completes_even_though_its_caller_times_out(
        tmp_path, monkeypatch):
    """The other half: cancellation cannot un-fire a click. A job `_serve` has
    already started must run to completion even after its caller's wait()
    times out and raises."""
    monkeypatch.setenv("AETHELARK_DATA_DIR", str(tmp_path))
    browser = EagleBrowser(launcher=lambda p, d, h: FakePlaywrightPage(),
                           playwright_fn=lambda: object())
    browser.start()
    try:
        ran = threading.Event()

        def slow(page):
            time.sleep(0.3)
            ran.set()
            return "done"

        with pytest.raises(TimeoutError):
            browser.call(slow, timeout=0.05)

        assert ran.wait(5)
    finally:
        browser.close()
