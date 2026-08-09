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


# ── blocker 4: headless by default ──────────────────────────────────────
#
# `web_agency` is declared non-exclusive in main.py specifically because it
# is meant to run while the user is doing something else — but that
# guarantee only holds if the browser it drives never puts a window on
# screen unasked, and headed-by-default contradicted it: the first call
# would open a visible Chromium window over whatever the user was doing,
# concurrently with another tool. Headless is now the default; a visible
# window remains available (for local debugging, until a real handoff
# mechanism exists — see web_agency.py's `_NO_HANDOFF_WINDOW`) by explicitly
# opting out via the environment.

def test_headless_defaults_to_true_so_a_non_exclusive_tool_cannot_steal_the_screen(
        monkeypatch):
    monkeypatch.delenv("AETHELARK_BROWSER_HEADLESS", raising=False)
    assert EagleBrowser().headless is True


def test_headless_can_be_switched_off_by_environment_for_local_debugging(
        monkeypatch):
    monkeypatch.setenv("AETHELARK_BROWSER_HEADLESS", "0")
    assert EagleBrowser().headless is False


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


def test_pageport_url_marshals_onto_the_owning_thread(tmp_path, monkeypatch):
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


def test_pageport_url_propagates_failure_instead_of_returning_empty_string():
    """The defect finding 2 caught: url() used to read the page inline and
    swallow any exception into "" — a silent wrong answer a caller could
    mistake for a real, empty URL, rather than the failure every other
    PagePort method surfaces."""
    class BrokenPage:
        @property
        def url(self):
            raise RuntimeError("page is closed")

    port = PagePort(BrokenPage(), call=lambda fn: fn())
    with pytest.raises(RuntimeError, match="page is closed"):
        port.url()


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
        # `abandoned` was queued strictly before this call, on the same
        # single-consumer queue, so its dequeue - and the cancellation check
        # that decides whether to run it - is guaranteed to have already
        # happened by the time this synchronous call returns. Without this,
        # the assertion below would rest on thread-scheduling luck instead of
        # a guarantee.
        assert browser.call(lambda page: "sync", timeout=5) == "sync"
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


# ── review round 2 ────────────────────────────────────────────────────────
#
# Round 1's cancellation fix introduced its own regression: close() submitted
# its teardown jobs (context.close(), playwright.stop()) the same way any
# ordinary call is submitted — cancellable. If the browser thread was still
# occupied by a slow job when close() gave up waiting, the still-queued
# teardown job was cancelled and dropped exactly like an abandoned click
# would be, except dropping *this* job leaks a live chromium process holding
# the profile lock for the rest of the process — precisely the state
# start()'s round-1 retry fix would then relaunch straight into.


class FakeContext:
    """Records which thread actually ran close() — the seam this section
    exists to prove is exercised even when close() itself gives up early."""

    def __init__(self):
        self.closed_on = []

    def close(self):
        self.closed_on.append(threading.current_thread().name)


class PageWithContext(FakePlaywrightPage):
    def __init__(self):
        super().__init__()
        self.context = FakeContext()


def test_close_still_tears_down_even_after_it_gives_up_waiting(tmp_path,
                                                                monkeypatch):
    """Occupy the browser thread well past the 10s teardown timeout the
    pre-fix close() used, then call close(). The teardown job cannot possibly
    finish before close() gives up waiting on it — that is exactly the
    condition that used to cancel it and drop it forever. It must still run
    once the thread is free.

    10.5s is deliberately not tied to _TEARDOWN_TIMEOUT (now 5.0s): it is
    chosen to exceed the specific 10s value the pre-fix close() hard-coded,
    so this test discriminates against that code, not just against whatever
    the constant happens to be tuned to today."""
    monkeypatch.setenv("AETHELARK_DATA_DIR", str(tmp_path))
    page = PageWithContext()
    occupier_seconds = 10.5

    browser = EagleBrowser(launcher=lambda p, d, h: page,
                           playwright_fn=lambda: object())
    browser.start()

    occupier = threading.Thread(
        target=lambda: browser.call(
            lambda page: time.sleep(occupier_seconds), timeout=60))
    occupier.start()
    time.sleep(0.1)  # let the occupier actually start on the browser thread

    browser.close()  # must give up waiting on context.close() here...
    occupier.join(occupier_seconds + 5)

    # ...but the job must not have been dropped. Poll rather than assume the
    # exact instant it lands: close() giving up on its own wait tells us
    # nothing about when the still-queued job actually runs, only that it
    # eventually must.
    deadline = time.monotonic() + 5.0
    while not page.context.closed_on and time.monotonic() < deadline:
        time.sleep(0.02)

    assert page.context.closed_on == ["EagleBrowser"]


# ── Blocker 3: unsynchronised browser start under concurrent tool calls ────
#
# main.py declares `web_agency` non-exclusive, and `default_browser()` is a
# process-wide singleton shared by every call — so two `web_agency` calls in
# one model turn can both reach `EagleBrowser.start()` concurrently. Without
# a lock, both would see `self._thread` as None/dead at the same instant and
# each spawn its own `EagleBrowser` thread, each calling the launcher — two
# `launch_persistent_context()` calls racing on the SAME profile directory
# (the reproduction measured this 5/5, and one run left two orphaned threads
# sharing one `_jobs` queue that `close()` only ever posts one sentinel to).


def test_concurrent_start_calls_launch_the_browser_only_once(tmp_path,
                                                              monkeypatch):
    monkeypatch.setenv("AETHELARK_DATA_DIR", str(tmp_path))
    launches: list[str] = []
    launch_started = threading.Event()
    proceed = threading.Event()

    def launcher(playwright, profile, headless):
        launches.append(threading.current_thread().name)
        launch_started.set()
        # Held open deliberately: without the lock, every concurrent
        # start() call would reach this point before any of them finishes,
        # so holding it open widens the race window rather than closing it.
        proceed.wait(5)
        return FakePlaywrightPage()

    browser = EagleBrowser(launcher=launcher, playwright_fn=lambda: object())

    threads = [threading.Thread(target=browser.start) for _ in range(5)]
    for t in threads:
        t.start()
    assert launch_started.wait(5), "no start() call ever reached the launcher"
    proceed.set()
    for t in threads:
        t.join(5)

    try:
        assert len(launches) == 1, (
            f"{len(launches)} concurrent start() calls each launched a "
            "browser against the same profile directory — the lock did not "
            "hold")
        assert browser.running is True
    finally:
        browser.close()


def test_default_browser_returns_one_instance_under_concurrent_first_calls(
        monkeypatch):
    from actions.grounding.web import browser as browser_module

    # Widen the check-then-create race deliberately, the same way the test
    # above widens start()'s: without this, CPython's GIL makes the
    # unguarded version of default_browser() likely (but not guaranteed) to
    # look safe anyway, which would make this test flaky in the direction
    # that hides the bug rather than catches it.
    original_init = browser_module.EagleBrowser.__init__

    def slow_init(self, *a, **kw):
        time.sleep(0.05)
        original_init(self, *a, **kw)

    monkeypatch.setattr(browser_module.EagleBrowser, "__init__", slow_init)
    monkeypatch.setattr(browser_module, "_DEFAULT", None)

    results: list[object] = []

    def get():
        results.append(browser_module.default_browser())

    threads = [threading.Thread(target=get) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)

    assert len(results) == 10
    assert len({id(r) for r in results}) == 1, (
        "concurrent first calls to default_browser() constructed more than "
        "one EagleBrowser — callers would end up scattered across separate "
        "browsers, profiles, and job queues")


# ── The keyring, which is why every imported login was silently useless ─────
# Chrome on Linux encrypts cookie values with a key held in the system keyring
# (scheme "v11"). Playwright launches Chrome with --password-store=basic to
# avoid keyring prompts, and basic cannot decrypt v11 — so Chrome discarded
# every imported cookie and wrote fresh empty ones. Measured on the real
# profile: 0 cookies visible with basic, 61 with gnome-libsecret, including
# SID/SAPISID/__Secure-1PSID.
#
# Nothing reported an error. The import said it worked, the database was
# correct, and the eagle simply appeared signed out.

class _FakePW:
    def __init__(self):
        self.kwargs = None
        self.chromium = self

    def launch_persistent_context(self, path, **kwargs):
        self.kwargs = kwargs
        class Ctx:
            pages = []
            def new_page(self_inner): return object()
            def set_default_timeout(self_inner, _ms): pass
        return Ctx()


def test_a_chrome_profile_is_launched_against_the_system_keyring(tmp_path, monkeypatch):
    import actions.grounding.web.browser as B
    monkeypatch.setattr(B.sys, "platform", "linux")
    (tmp_path / B._CHANNEL_MARKER).write_text("chrome")

    pw = _FakePW()
    B._default_launcher(pw, tmp_path, True)

    assert "--password-store=basic" in (pw.kwargs.get("ignore_default_args") or []), (
        "Playwright's default basic store cannot decrypt Chrome's v11 cookies")
    assert any("password-store=gnome" in a for a in pw.kwargs.get("args", []))


def test_the_bundled_chromium_is_left_alone(tmp_path, monkeypatch):
    """Only a profile imported FROM Chrome carries v11 values. The bundled
    browser has nothing to decrypt, and forcing a keyring it may not have can
    only cost a prompt or a failed launch."""
    import actions.grounding.web.browser as B
    monkeypatch.setattr(B.sys, "platform", "linux")

    pw = _FakePW()
    B._default_launcher(pw, tmp_path, True)          # no channel marker
    assert not pw.kwargs.get("ignore_default_args")


def test_the_keyring_flag_is_linux_only(tmp_path, monkeypatch):
    """macOS and Windows use their own credential stores and the flag means
    nothing there."""
    import actions.grounding.web.browser as B
    (tmp_path / B._CHANNEL_MARKER).write_text("chrome")
    for plat in ("darwin", "win32"):
        monkeypatch.setattr(B.sys, "platform", plat)
        pw = _FakePW()
        B._default_launcher(pw, tmp_path, True)
        assert not pw.kwargs.get("ignore_default_args"), plat


# ── surface(): the check that meant the window never opened ────────────────
# `if self.running and self.headless == bool(visible): return True` reads as
# "already in the requested state, nothing to do" and means the opposite.
# headless == visible is TRUE exactly when the browser is hidden and the caller
# asked for it visible — the case that needs work. So surface() no-opped and
# returned success whenever the browser was already running, which in the real
# sign-in flow it always is.
#
# Caught by calling it for real and watching `headless` stay False after
# surface(False) put it "away". Nothing failed; the window simply stayed on
# screen and the eagle reported it had gone.

class _Recording:
    """EagleBrowser's surface() over a fake lifecycle."""
    surface = EagleBrowser.surface

    def __init__(self, headless=True, running=True):
        self.headless = headless
        self._running = running
        self.restarts = 0
        self._lifecycle_lock = threading.RLock()

    @property
    def running(self):
        return self._running

    def close(self):
        self._running = False

    def start(self):
        self._running = True
        self.restarts += 1


def test_showing_a_hidden_browser_actually_restarts_it():
    """The bug. A running headless browser asked to become visible must come
    down and go back up — Playwright fixes headless at launch."""
    b = _Recording(headless=True, running=True)
    assert b.surface(True) is True
    assert b.headless is False, "reported success while staying hidden"
    assert b.restarts == 1


def test_hiding_a_visible_browser_actually_restarts_it():
    b = _Recording(headless=False, running=True)
    assert b.surface(False) is True
    assert b.headless is True, "reported success while staying on screen"
    assert b.restarts == 1


def test_a_browser_already_in_the_requested_state_is_left_alone():
    """The early return is worth having — restarting a correct browser costs
    the user a visible flicker and drops the page."""
    for headless, want in ((True, False), (False, True)):
        b = _Recording(headless=headless, running=True)
        assert b.surface(want) is True
        assert b.restarts == 0, "restarted a browser that was already correct"


def test_a_stopped_browser_is_started_into_the_requested_state():
    b = _Recording(headless=True, running=False)
    assert b.surface(True) is True
    assert b.headless is False
    assert b.restarts == 1


# ── Sticky headers, which is why clicks failed on real sites ───────────────
# Live on eu.store.bambulab.com: the eagle opened the page (69 controls),
# resolved "P2S" to "Bambu Lab P2S 3D Printer" correctly, and then could not
# click it — "covered by something else (waited 5009ms across 76 tries)".
#
# The cover was the site's own sticky navigation bar. Playwright scrolls an
# element into view before clicking, but "in view" can still mean "underneath
# the fixed header", and it then waits out the whole timeout for a hit test
# that will never pass. A person solves this without thinking: scroll so the
# thing is not under the bar.

class _ClickPage:
    """Records what a click attempt did, and can refuse the first one."""

    def __init__(self, covered_times=0):
        self.covered_times = covered_times
        self.attempts = 0
        self.centred = 0
        self.js_clicked = 0

    def click(self, selector, timeout=0):
        self.attempts += 1
        if self.attempts <= self.covered_times:
            raise RuntimeError("element is not visible / intercepts pointer events")

    def eval_on_selector(self, selector, script):
        if "scrollIntoView" in script:
            self.centred += 1
        elif ".click()" in script:
            self.js_clicked += 1

    # Playwright's real name for the above
    evaluate = None


def test_a_click_centres_the_element_first():
    """Centring takes it out from under a sticky header, which is the whole
    fix for the commonest real-site failure."""
    page = _ClickPage()
    PagePort(page, call=lambda fn: fn()).click("e7")
    assert page.centred >= 1, "did not scroll the element clear of the header"
    assert page.attempts == 1


def test_a_covered_element_falls_back_to_a_direct_click():
    """When the pointer is still intercepted, dispatch the click on the
    element itself. It is the SAME element already resolved and already passed
    through the consent gate — the fallback changes how the click is
    delivered, never what is clicked."""
    page = _ClickPage(covered_times=1)
    PagePort(page, call=lambda fn: fn()).click("e7")
    assert page.js_clicked == 1, "gave up instead of clicking directly"


def test_the_fallback_is_not_used_when_the_normal_click_works():
    """A direct dispatch skips real hit-testing, so it must stay a last
    resort rather than becoming the default path."""
    page = _ClickPage()
    PagePort(page, call=lambda fn: fn()).click("e7")
    assert page.js_clicked == 0


def test_a_click_blocked_by_a_backdrop_dismisses_it_and_retries():
    """Live on youtube.com: the Home link was covered by
    TP-YT-IRON-OVERLAY-BACKDROP — a modal backdrop left over from a drawer.
    The refusal was correct; stopping there was not. A person presses Escape
    without thinking about it and carries on.

    Escape only, and only once. It is the universal "close this" gesture and
    it cannot submit, buy or delete anything — unlike clicking at whatever
    happens to be on top, which is how an automated retry causes damage."""
    events = []

    class Page:
        def __init__(self):
            self.attempts = 0

        def eval_on_selector(self, selector, script):
            events.append("centre" if "scrollIntoView" in script else "direct")

        def click(self, selector, timeout=0):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("intercepts pointer events")

        class keyboard:
            @staticmethod
            def press(key):
                events.append(f"key:{key}")

    page = Page()
    PagePort(page, call=lambda fn: fn()).click("e9")
    assert "key:Escape" in events, "never tried the human move"
    assert page.attempts >= 2, "did not retry after dismissing"


def test_escape_is_not_pressed_when_the_click_works():
    events = []

    class Page:
        def eval_on_selector(self, selector, script): pass
        def click(self, selector, timeout=0): pass
        class keyboard:
            @staticmethod
            def press(key): events.append(key)

    PagePort(Page(), call=lambda fn: fn()).click("e9")
    assert events == [], "pressed keys on a page that was working fine"
