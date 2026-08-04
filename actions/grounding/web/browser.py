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
_CALL_TIMEOUT = 30.0


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
        self._call(lambda: self._page.click(selector))

    def fill(self, ref: str, text: str) -> None:
        selector = f'[data-ae-ref="{ref}"]'
        self._call(lambda: self._page.fill(selector, text))

    def url(self) -> str:
        try:
            return str(self._page.url)
        except Exception:
            return ""


class EagleBrowser:
    """The eagle's browser. Started once, kept until shutdown."""

    def __init__(self,
                 headless: bool | None = None,
                 profile_dir: Path | None = None,
                 launcher: Callable[[Any, Path, bool], Any] | None = None,
                 playwright_fn: Callable[[], Any] | None = None) -> None:
        if headless is None:
            headless = os.environ.get("AETHELARK_BROWSER_HEADLESS", "") \
                         .strip().lower() in ("1", "true", "yes")
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

    # ── lifecycle ───────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._page is not None and bool(self._thread
                                               and self._thread.is_alive())

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
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
            fn, box, done = job
            try:
                box.append(("ok", fn()))
            except Exception as e:
                box.append(("err", e))
            finally:
                done.set()

    def close(self) -> None:
        if self._thread is None:
            return
        page, self._page = self._page, None
        try:
            # Playwright pages expose their own context; the fakes do not.
            context = getattr(page, "context", None)
            if context is not None and hasattr(context, "close"):
                self._submit(lambda: context.close(), timeout=10)
            playwright = self._playwright
            if playwright is not None and hasattr(playwright, "stop"):
                self._submit(lambda: playwright.stop(), timeout=10)
        except Exception:
            pass
        self._jobs.put(None)
        self._thread.join(timeout=5)
        self._thread = None
        self._playwright = None

    # ── work ────────────────────────────────────────────────────────────────

    def _submit(self, fn: Callable[[], Any], timeout: float) -> Any:
        if self._thread is None or not self._thread.is_alive():
            raise RuntimeError("browser thread is not running")
        box: list = []
        done = threading.Event()
        self._jobs.put((fn, box, done))
        if not done.wait(timeout):
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


def default_browser() -> EagleBrowser:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = EagleBrowser()
    return _DEFAULT
