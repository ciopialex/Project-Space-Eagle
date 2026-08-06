"""One browser the eagle owns, kept warm, running in its own thread.

Two facts shape this file.

Playwright's sync API must be used from the thread that created it, and cannot
be used at all from a thread running an asyncio loop. Tools are dispatched onto
an executor whose threads are not stable between calls. So the browser owns one
long-lived thread and every call is marshalled onto it.

The `Grounder` protocol is synchronous. Keeping the sync API here is what lets
`WebGrounder.find` stay a plain function instead of infecting the whole
grounding stack with async.
"""
from __future__ import annotations

import os
import queue
import threading
from pathlib import Path
from typing import Any, Callable

from actions.grounding.web.page import COLLECT_JS, HIT_TEST_JS
from core import user_paths

# A page that has not settled in this long is not going to.
_NAV_TIMEOUT_MS = 30_000
# Strictly greater than _NAV_TIMEOUT_MS. If the two race on the same clock, a
# slow Playwright action can time out _submit() at the same moment Playwright
# itself would have timed out the action - and _submit()'s caller is told the
# call failed while the job is still sitting in, or running from, the queue.
# The outer deadline must lose that race, the same margin goto() already uses
# (45s outer vs. 30s inner).
_CALL_TIMEOUT = 45.0

# A ref names an element by a `data-ae-ref` attribute COLLECT_JS stamped
# during the snapshot that found it — and COLLECT_JS strips and renumbers
# every such attribute from scratch at the start of the *next* collect (see
# page.py), navigation or not. A ref is only ever good until the next
# `collect()` call, full stop — an earlier version of this comment claimed
# it "outlives exactly one navigation and no more," which is false: any
# `collect()` invalidates it, including the ones `wait_for` issues on every
# poll while waiting for a control to become actionable. Worse than merely
# going stale: because the numbering is positional ('e' + n, restarting from
# 0 every time), an old ref string can silently be reassigned to a
# DIFFERENT live element in the new snapshot rather than matching nothing at
# all — see `_act_with_reresolve` in actions/web_agency.py for the bug that
# caused and the fix (re-resolve and re-gate immediately before every
# actuation, never trust a ref resolved earlier).
#
# `context.set_default_timeout` above sizes Playwright's default per-call
# wait for navigation, not for discovering a selector matches nothing: a ref
# gone stale between being resolved and being used (an async redirect, an
# SPA route change, or simply another `collect()` running first) would
# otherwise block a click or fill for the full 30s before failing. Ref-based
# actuation gets its own, much shorter timeout instead, so a stale ref is
# reported fast enough for the caller to re-resolve and retry within the same
# tool call — see `_act_with_reresolve` in actions/web_agency.py, the seam
# that owns the retry because it is the one place that still has the
# description a fresh resolve needs.
_REF_TIMEOUT_MS = 4_000

# How long close() waits, per teardown step, before it stops trying to
# confirm the step finished and moves on. Unlike _CALL_TIMEOUT, this number
# has no correctness weight: teardown jobs are submitted with
# cancellable=False, so a caller giving up here never causes the job to be
# dropped (see _submit). It only trades "how promptly close() can return"
# against "how often it can synchronously confirm a normal-speed teardown
# actually finished" - a modest window, well under _CALL_TIMEOUT, since
# there's no safety reason for close() to sit for tens of seconds on a
# wedged browser just to find out what cancellable=False already guarantees:
# the teardown will still run once the thread is free.
_TEARDOWN_TIMEOUT = 5.0


def _default_playwright():
    from playwright.sync_api import sync_playwright
    return sync_playwright().start()


def _default_launcher(playwright, profile: Path, headless: bool):
    """A persistent context, so logins survive between sessions."""
    context = playwright.chromium.launch_persistent_context(
        str(profile),
        headless=headless,
        viewport={"width": 1440, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )
    context.set_default_timeout(_NAV_TIMEOUT_MS)
    pages = context.pages
    # A persistent context already opens a tab. Adopt it rather than adding a
    # second, empty one.
    return pages[0] if pages else context.new_page()


class PagePort:
    """A Playwright `Page` as the `PageLike` the grounder wants.

    `call` marshals onto the browser thread. The tests pass `lambda fn: fn()`
    to run inline.
    """

    def __init__(self, page: Any,
                 call: Callable[[Callable[[], Any]], Any] | None = None) -> None:
        self._page = page
        self._call = call or (lambda fn: fn())

    def collect(self) -> list[dict]:
        return self._call(lambda: self._page.evaluate(COLLECT_JS)) or []

    def hit_test(self, x: int, y: int) -> dict | None:
        return self._call(
            lambda: self._page.evaluate(HIT_TEST_JS, [int(x), int(y)]))

    def screenshot(self) -> bytes:
        # From the compositor, not the display: works on a background tab.
        return self._call(lambda: self._page.screenshot(type="png"))

    def click(self, ref: str) -> None:
        selector = f'[data-ae-ref="{ref}"]'
        self._call(lambda: self._page.click(selector, timeout=_REF_TIMEOUT_MS))

    def fill(self, ref: str, text: str) -> None:
        selector = f'[data-ae-ref="{ref}"]'
        self._call(lambda: self._page.fill(selector, text,
                                           timeout=_REF_TIMEOUT_MS))

    def url(self) -> str:
        # Every other method here marshals onto the browser thread and lets
        # failure raise. This one used to read `self._page.url` inline on the
        # caller's thread and swallow any exception into "" - a stale or
        # closed page would silently report the empty string as "the current
        # URL" instead of surfacing that the read failed.
        return self._call(lambda: str(self._page.url))


class EagleBrowser:
    """The eagle's browser. Started once, kept until shutdown."""

    def __init__(self,
                 headless: bool | None = None,
                 profile_dir: Path | None = None,
                 launcher: Callable[[Any, Path, bool], Any] | None = None,
                 playwright_fn: Callable[[], Any] | None = None) -> None:
        if headless is None:
            # Default headless. `main.py` declares web_agency non-exclusive
            # ("touches neither the user's screen nor their browser" — see
            # its TOOL_SPECS comment) specifically so it can run while the
            # user is doing something else; that guarantee only holds if the
            # browser it drives never puts a window on screen unasked. A
            # human occasionally does need to see this browser — finishing a
            # login is the main case — but there is no surfacing mechanism
            # for that yet (see `_NO_HANDOFF_WINDOW` in web_agency.py, which
            # says so honestly rather than pretending a handoff path exists).
            # `AETHELARK_BROWSER_HEADLESS=0`/`false`/`no` opts back into a
            # visible window for local debugging in the meantime.
            raw = os.environ.get("AETHELARK_BROWSER_HEADLESS", "").strip().lower()
            headless = raw not in ("0", "false", "no")
        self.headless = bool(headless)
        self._profile_dir = profile_dir
        self._launcher = launcher or _default_launcher
        self._playwright_fn = playwright_fn or _default_playwright

        self._jobs: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._page: Any = None
        self._playwright: Any = None
        self.last_error: str = ""
        # Guards start()/close() only (see each method) — not every call,
        # per the fix's own "keep it simple, do not restructure the thread
        # model" instruction. `RLock` because start() can call close() on
        # itself (the page-less-thread retry path below), from the same
        # thread, while already holding the lock.
        self._lifecycle_lock = threading.RLock()

    # ── lifecycle ───────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._page is not None and bool(self._thread
                                               and self._thread.is_alive())

    def start(self) -> None:
        """Launch the browser, or confirm it is already up.

        Locked end to end: `main.py` declares `web_agency` non-exclusive, so
        two tool calls in the same model turn can both reach this method
        concurrently. Without the lock, both would see `self._thread` as
        None or dead at the same instant and each spawn its own browser
        thread — two `launch_persistent_context()` calls racing on the SAME
        profile directory (measured 5/5 in the reproduction: one run left
        two orphaned `EagleBrowser` threads sharing a single `_jobs` queue
        that `close()` only ever posts one sentinel to, so one looped
        forever). The lock makes the second caller's `start()` simply
        observe "already up and serving" once the first one has finished,
        instead of launching a second time.
        """
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                if self._page is not None:
                    return  # already up and serving
                # The thread is alive but page-less: a previous launch failed,
                # and _serve deliberately kept the thread looping so close() had
                # somewhere to send its teardown jobs. That is correct for
                # close(), but left alone it also means a transient failure - a
                # stale profile lock from a crash, chromium mid-install - bricks
                # this object for the rest of the process. Since default_browser()
                # is a process-wide singleton, that bricks web grounding entirely.
                # Tear the dead thread down and retry. Safe to call while
                # holding `_lifecycle_lock`: it is an `RLock`, and close()
                # takes the same lock (see there).
                self.close()
            self.last_error = ""
            self._ready.clear()
            self._thread = threading.Thread(target=self._serve, daemon=True,
                                            name="EagleBrowser")
            self._thread.start()
            self._ready.wait(timeout=60)

    def _serve(self) -> None:
        try:
            profile = self._profile_dir or user_paths.browser_profile_dir()
            self._playwright = self._playwright_fn()
            self._page = self._launcher(self._playwright, Path(profile),
                                        self.headless)
        except Exception as e:
            self.last_error = str(e)
            self._page = None
        finally:
            self._ready.set()

        while True:
            job = self._jobs.get()
            if job is None:
                return
            fn, box, done, cancelled = job
            if cancelled.is_set():
                # The caller's _submit() already gave up and raised
                # TimeoutError - nobody is waiting on `done` any more. This
                # job never started, so skipping it is free: no click has
                # fired, no fill has happened. A job already in flight is a
                # different story (see _submit) - this check only ever stops
                # one that is still waiting in line.
                continue
            try:
                box.append(("ok", fn()))
            except Exception as e:
                box.append(("err", e))
            finally:
                done.set()

    def close(self) -> None:
        """Tear the browser down, or confirm there is nothing to tear down.

        Locked with the same `_lifecycle_lock` `start()` uses (see there for
        why): the two must never run concurrently, or a `start()` racing a
        `close()` could observe a half-torn-down browser as "already up" and
        skip launching, or a `close()` could tear down a browser a
        concurrent `start()` just finished launching.
        """
        with self._lifecycle_lock:
            if self._thread is None:
                return
            page, self._page = self._page, None

            # Each step gets its own try/except. A context.close() that times out
            # must not stop playwright.stop() from being attempted - they are two
            # separate leaks (a chromium process, and the playwright driver
            # process), and a failure in the first used to swallow the second
            # entirely. Both are submitted with cancellable=False: close() giving
            # up on waiting for a result must not mean the teardown never
            # happens, or the browser leaks along with the profile lock it holds
            # - which is exactly the state a later start() would retry into.

            # Playwright pages expose their own context; the fakes do not.
            context = getattr(page, "context", None)
            if context is not None and hasattr(context, "close"):
                try:
                    self._submit(lambda: context.close(), _TEARDOWN_TIMEOUT,
                                 cancellable=False)
                except Exception:
                    pass

            playwright = self._playwright
            if playwright is not None and hasattr(playwright, "stop"):
                try:
                    self._submit(lambda: playwright.stop(), _TEARDOWN_TIMEOUT,
                                 cancellable=False)
                except Exception:
                    pass

            self._jobs.put(None)
            self._thread.join(timeout=5)
            self._thread = None
            self._playwright = None

    # ── work ────────────────────────────────────────────────────────────────

    def _submit(self, fn: Callable[[], Any], timeout: float,
               cancellable: bool = True) -> Any:
        """Run `fn` on the browser thread and wait up to `timeout` for it.

        `cancellable` distinguishes "the caller gave up waiting" from "the
        job should not happen." They are the same thing for an ordinary
        click or fill - the caller no longer wants a result it will never
        see, so the job must not fire. They are not the same thing for
        teardown: close() giving up on waiting for context.close() to
        confirm is not a reason to skip closing the context - that is
        exactly what leaks the browser and its profile lock. close() passes
        cancellable=False for that reason.
        """
        if self._thread is None or not self._thread.is_alive():
            raise RuntimeError("browser thread is not running")
        box: list = []
        done = threading.Event()
        cancelled = threading.Event()
        self._jobs.put((fn, box, done, cancelled))
        if not done.wait(timeout):
            if cancellable:
                # Setting `cancelled` here is the only thing that can still
                # stop this job. If `_serve` has not reached it yet, it will
                # see the flag and drop it - the caller has been told the
                # call failed, so it must not go on to fire anyway. If
                # `_serve` has already started running it, the flag is
                # checked too late to matter and the job runs to completion,
                # because a click already dispatched cannot be un-clicked;
                # only its result is lost, not its effect.
                cancelled.set()
            raise TimeoutError(f"browser call exceeded {timeout}s")
        kind, payload = box[0]
        if kind == "err":
            raise payload
        return payload

    def call(self, fn: Callable[[Any], Any],
             timeout: float = _CALL_TIMEOUT) -> Any:
        """Run `fn(page)` on the browser thread and return its result."""
        return self._submit(lambda: fn(self._page_unsafe()), timeout)

    def _page_unsafe(self) -> Any:
        return self._page

    def page(self) -> PagePort | None:
        """The current page as a `PageLike`, or None if the browser is down."""
        if not self.running:
            return None
        return PagePort(self._page,
                        call=lambda fn: self._submit(fn, _CALL_TIMEOUT))

    def goto(self, url: str) -> str:
        """Navigate. Returns the URL actually landed on."""
        def _go(page):
            page.goto(url, timeout=_NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            return str(page.url)

        return self.call(_go, timeout=45.0)


_DEFAULT: EagleBrowser | None = None
# Guards the check-then-create below. `web_agency` is non-exclusive (see
# main.py's TOOL_SPECS), so two tool calls in the same model turn can both
# reach `default_browser()` for the first time concurrently — without this,
# both could see `_DEFAULT is None` and each construct their own
# `EagleBrowser`, and whichever object callers end up scattered across would
# no longer share one browser, one profile, one job queue.
_DEFAULT_LOCK = threading.Lock()


def default_browser() -> EagleBrowser:
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = EagleBrowser()
        return _DEFAULT
