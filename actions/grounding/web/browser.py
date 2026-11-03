"""One browser the eagle owns, kept warm, on its own thread.

Playwright's sync API must stay on the thread that created it and cannot run on an
asyncio thread, so every call is marshalled onto one long-lived thread. Sync keeps
WebGrounder.find a plain function.
"""
from __future__ import annotations

import asyncio
import os
import queue
import sys
import threading
from pathlib import Path
from typing import Any, Callable

from actions.grounding.web.page import (COLLECT_JS, DOM_SIGNATURE_JS,
                                        HIT_TEST_JS)
from core import user_paths

# A page that has not settled in this long is not going to.
_NAV_TIMEOUT_MS = 30_000
# Strictly greater than _NAV_TIMEOUT_MS, so Playwright's own timeout fires first.
_CALL_TIMEOUT = 45.0

# A ref is good only until the next collect(), which renumbers every data-ae-ref and can
# hand an old ref to a different element (see _act_with_reresolve in web_agency.py). Ref
# actions get a short timeout so a stale ref fails fast enough to re-resolve within the
# same call.
_REF_TIMEOUT_MS = 4_000

# How long close() waits per teardown step before moving on. Teardown jobs are not
# cancellable, so they still run.
_TEARDOWN_TIMEOUT = 5.0


def _default_playwright():
    from playwright.sync_api import sync_playwright
    return sync_playwright().start()


# A profile is always opened with the browser that created it: on Linux the cookie key
# lives in the keyring under the browser's name, and the wrong browser silently reads
# every cookie as garbage.
_CHANNEL_MARKER = ".aethelark-browser-channel"


def profile_channel(profile: Path) -> str | None:
    """Which browser owns this profile, or None for a fresh directory."""
    try:
        marker = (Path(profile) / _CHANNEL_MARKER).read_text().strip()
        return marker or None
    except Exception:
        return None


def _remember_channel(profile: Path, channel: str | None) -> None:
    try:
        Path(profile).mkdir(parents=True, exist_ok=True)
        (Path(profile) / _CHANNEL_MARKER).write_text(channel or "chromium")
    except Exception:
        pass          # best-effort; a missing marker just means "chromium"


# Moving the window off-screen does not work: the compositor puts it back. A private
# display does (core/virtual_display.py).
_WINDOW = ["--window-size=1440,900"]


def _default_launcher(playwright, profile: Path, headless: bool):
    """A persistent context, so logins survive between sessions.

    Sites reject headless browsers (makerworld.com served a Cloudflare wall every
    time), so "headless" means headed on a private display when Xvfb exists, and
    truly headless otherwise; --doctor reports which. AETHELARK_BROWSER_HEADLESS=0
    shows a real window.
    """
    channel = profile_channel(profile)
    launch_kwargs = {"channel": channel} if channel and channel != "chromium" else {}

    # A Chrome-channel profile needs the system keyring: with --password-store=basic,
    # Chrome cannot decrypt v11 cookies and silently discards them.
    if channel == "chrome" and sys.platform.startswith("linux"):
        launch_kwargs["ignore_default_args"] = ["--password-store=basic"]
    # A private display when available, so the browser can be headed without appearing
    # on screen.
    from core import virtual_display
    hidden = virtual_display.display() if headless else None
    if hidden:
        launch_kwargs["env"] = virtual_display.env_for()

    context = playwright.chromium.launch_persistent_context(
        str(profile),
        # Headless only as the fallback.
        headless=bool(headless and not hidden),
        **launch_kwargs,
        viewport={"width": 1440, "height": 900},
        # Without this Playwright cancels every download (PagePort.download).
        accept_downloads=True,
        args=(["--disable-blink-features=AutomationControlled"]
              + (_WINDOW if headless else [])
              + (["--password-store=gnome-libsecret"]
                 if launch_kwargs.get("ignore_default_args") else [])),
    )
    context.set_default_timeout(_NAV_TIMEOUT_MS)
    _remember_channel(profile, channel)
    pages = context.pages
    # Adopt the tab the persistent context opens.
    return pages[0] if pages else context.new_page()


# What the eagle may write to disk from a web page. An allow-list, checked on every
# extension component ("stand.stl.exe"). Nothing here executes a downloaded file.
_DOWNLOAD_ALLOWED = frozenset({
    # documents
    "pdf", "txt", "md", "rtf", "odt", "doc", "docx", "ods", "xls", "xlsx",
    "odp", "ppt", "pptx", "csv", "tsv", "json", "xml", "yaml", "yml", "ics",
    # images / media
    "jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "tiff", "heic",
    "mp3", "wav", "flac", "ogg", "m4a", "mp4", "webm", "mov", "mkv",
    # 3D printing and CAD
    "stl", "3mf", "obj", "step", "stp", "gcode", "bgcode", "amf", "ply",
    "f3d", "dxf", "svg",
    # archives (inert until opened, and nothing here opens them)
    "zip", "tar", "gz", "bz2", "xz", "7z", "rar",
    # data / fonts
    "sqlite", "db", "parquet", "ttf", "otf", "woff", "woff2", "epub",
})

# Refused wherever they appear in the name.
_DOWNLOAD_NEVER = frozenset({
    "exe", "msi", "bat", "cmd", "com", "scr", "pif", "cpl", "jar",
    "sh", "bash", "zsh", "run", "bin", "deb", "rpm", "appimage", "dmg", "pkg",
    "app", "so", "dll", "dylib", "ps1", "psm1", "vbs", "vbe", "js", "jse",
    "wsf", "wsh", "hta", "reg", "desktop", "lnk", "apk", "elf",
})


def download_name_ok(name: str) -> tuple[bool, str]:
    """(allowed, why-not). Fails closed on anything unrecognised."""
    parts = [p for p in (name or "").lower().split(".") if p]
    if len(parts) < 2:
        return False, "it has no file extension"
    exts = parts[1:]
    for e in exts:
        if e in _DOWNLOAD_NEVER:
            return False, f"'.{e}' is a program, not a document"
    final = exts[-1]
    if final not in _DOWNLOAD_ALLOWED:
        return False, f"'.{final}' is not a recognised document or media type"
    return True, ""


class _MarshaledEventCtx:
    """An expect_download / expect_popup context manager marshalled onto the browser
    thread one step at a time, because the click inside the `with` body is its own
    marshalled call. __enter__, __exit__ and .value each run on the browser thread
    in order.
    """

    def __init__(self, call: Callable[[Callable[[], Any]], Any],
                 open_ctx: Callable[[], Any]) -> None:
        self._call = call
        self._open_ctx = open_ctx    # zero-arg -> a fresh raw Playwright CM
        self._raw_cm: Any = None
        self._raw_value: Any = None

    def __enter__(self) -> "_MarshaledEventCtx":
        def _do():
            cm = self._open_ctx()
            return cm, cm.__enter__()
        self._raw_cm, self._raw_value = self._call(_do)
        return self

    def __exit__(self, *exc: Any) -> bool:
        try:
            self._call(lambda: self._raw_cm.__exit__(*exc))
        except (Exception, asyncio.CancelledError):
            # Playwright's __exit__ raises TimeoutError when the event never fired (the
            # normal outcome here) and CancelledError (a BaseException) when the block
            # raised; outcome.py handles both (_EXPECTATION_FAILED).
            pass
        return False

    @property
    def value(self) -> Any:
        return self._call(lambda: self._raw_value.value)


class PagePort:
    """A Playwright Page as the PageLike the grounder wants. `call` marshals onto the
    browser thread; tests pass `lambda fn: fn()`.
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

    def dom_signature(self) -> str:
        """One number for what the page is now, the DOM half of url_or_dom_changed.
        Not a collect(): that would re-stamp every ref.
        """
        return str(self._call(lambda: self._page.evaluate(DOM_SIGNATURE_JS))
                   or "")

    def screenshot(self) -> bytes:
        # From the compositor, not the display: works on a background tab.
        return self._call(lambda: self._page.screenshot(type="png"))

    # Scroll the element to the middle of the viewport, so it is not under a sticky
    # header.
    _CENTRE_JS = "el => el.scrollIntoView({block: 'center', inline: 'center'})"

    # Last resort: dispatch the click on the same, already-gated element without hit-
    # testing. Changes how the click is delivered, never what is clicked.
    _DIRECT_JS = "el => el.click()"

    def centre(self, ref: str) -> None:
        """Scroll `ref` to the middle of the viewport, before the actionability poll.
        Best effort.
        """
        selector = f'[data-ae-ref="{ref}"]'
        try:
            self._call(lambda: self._page.eval_on_selector(selector, self._CENTRE_JS))
        except Exception:
            pass

    # A leftover modal backdrop covering the page.
    _BACKDROP_JS = """(() => {
      const el = [...document.querySelectorAll('*')].find(e => {
        const s = getComputedStyle(e), r = e.getBoundingClientRect();
        return (s.position === 'fixed' || s.position === 'absolute')
          && r.width > innerWidth * 0.5 && r.height > innerHeight * 0.5
          && s.display !== 'none' && +s.opacity > 0.05
          && /backdrop|overlay|modal|scrim/i.test(e.tagName + ' ' + e.className);
      });
      return !!el;
    })()"""

    def dismiss_overlay(self) -> bool:
        """Press Escape if a full-page backdrop covers things, before the
        actionability poll. Escape cannot submit, buy or delete.
        """
        try:
            if not self._call(lambda: self._page.evaluate(self._BACKDROP_JS)):
                return False
            self._call(lambda: self._page.keyboard.press("Escape"))
            self._call(lambda: self._page.wait_for_timeout(150))
            return True
        except Exception:
            return False

    def mouse_click(self, x: int, y: int) -> None:
        """Click a raw page coordinate: the vision fallback, for when collect() found
        nothing.
        """
        self._call(lambda: self._page.mouse.click(int(x), int(y)))

    def expect_download(self, timeout: int | None = None) -> _MarshaledEventCtx:
        """Needed on the live page by click_with_outcome (_MarshaledEventCtx)."""
        tms = 30_000 if timeout is None else timeout
        return _MarshaledEventCtx(
            self._call, lambda: self._page.expect_download(timeout=tms))

    def expect_popup(self, timeout: int | None = None) -> _MarshaledEventCtx:
        """The new-tab half of click_with_outcome's detection."""
        tms = 30_000 if timeout is None else timeout
        return _MarshaledEventCtx(
            self._call, lambda: self._page.expect_popup(timeout=tms))

    def click(self, ref: str) -> None:
        selector = f'[data-ae-ref="{ref}"]'

        def _do():
            try:
                self._page.eval_on_selector(selector, self._CENTRE_JS)
            except Exception:
                pass                      # centring is help, not a requirement
            try:
                self._page.click(selector, timeout=_REF_TIMEOUT_MS)
                return
            except Exception:
                pass

            # Still intercepted after centring, usually a leftover backdrop: press
            # Escape.
            try:
                self._page.keyboard.press("Escape")
                self._page.click(selector, timeout=_REF_TIMEOUT_MS)
                return
            except Exception:
                pass

            # Nothing moved: dispatch the click on the element.
            self._page.eval_on_selector(selector, self._DIRECT_JS)

        self._call(_do)

    def download(self, ref: str, to_dir=None, timeout_ms: int = 60_000):
        """Click `ref` and keep the file. The saved path, or None when no file
        arrived, which the caller must not call a download. The site chooses the
        filename, so it is reduced to a bare name and proved to land inside the
        target directory.
        """
        from pathlib import Path as _Path

        target = _Path(to_dir) if to_dir else (_Path.home() / "Downloads")
        selector = f'[data-ae-ref="{ref}"]'

        def _do():
            try:
                with self._page.expect_download(timeout=timeout_ms) as info:
                    try:
                        self._page.click(selector, timeout=_REF_TIMEOUT_MS)
                    except Exception:
                        self._page.eval_on_selector(selector, self._DIRECT_JS)
                    download = info.value
            except Exception:
                return None               # nothing ever started downloading

            try:
                raw = (getattr(download, "suggested_filename", "") or "").strip()
                name = _Path(raw).name or "download"
                if name in (".", ".."):
                    name = "download"

                # The page chose this name: only documents and media, checked on every
                # extension.
                ok, why = download_name_ok(name)
                if not ok:
                    print(f"[Web] ⛔ refused download {name!r}: {why}")
                    return None

                target.mkdir(parents=True, exist_ok=True)
                dest = target / name
                # Never overwrite.
                stem, suffix, n = dest.stem, dest.suffix, 1
                while dest.exists():
                    dest = target / f"{stem} ({n}){suffix}"
                    n += 1

                # Prove the path is inside the directory.
                if not dest.resolve().is_relative_to(target.resolve()):
                    return None

                download.save_as(str(dest))
                return str(dest)
            except Exception:
                return None               # a path we cannot vouch for is no path

        return self._call(_do)

    def upload(self, ref: str, path: str, timeout_ms: int = 60_000) -> bool:
        """Hand `path` to `ref`. True if the control accepted it. A plain <input
        type=file> gets set_input_files; a styled button's native dialog is
        intercepted with expect_file_chooser.
        """
        from pathlib import Path as _Path
        if not _Path(path).is_file():
            return False
        selector = f'[data-ae-ref="{ref}"]'

        def _do():
            try:
                self._page.set_input_files(selector, path, timeout=_REF_TIMEOUT_MS)
                return True
            except Exception:
                pass
            try:
                with self._page.expect_file_chooser(timeout=timeout_ms) as info:
                    self._page.click(selector, timeout=_REF_TIMEOUT_MS)
                info.value.set_files(path)
                return True
            except Exception:
                return False

        return bool(self._call(_do))

    def type_into_focused(self, text: str) -> str:
        """Type into whatever the page has focused. "" if nothing editable is, so the
        text never falls through to the OS keyboard.
        """
        def _do():
            what = self._page.evaluate(
                "() => { const a = document.activeElement;"
                " if (!a || a === document.body) return '';"
                " const tag = a.tagName.toLowerCase();"
                " if (!(tag === 'input' || tag === 'textarea' ||"
                "       a.isContentEditable)) return '';"
                " return a.getAttribute('aria-label') || a.getAttribute('name')"
                "        || a.getAttribute('placeholder') || tag; }")
            if not what:
                return ""
            self._page.keyboard.type(text)
            return what
        return self._call(_do) or ""

    def fill(self, ref: str, text: str) -> None:
        selector = f'[data-ae-ref="{ref}"]'
        self._call(lambda: self._page.fill(selector, text,
                                           timeout=_REF_TIMEOUT_MS))

    def url(self) -> str:
        # Marshalled and allowed to raise, like every other method here.
        return self._call(lambda: str(self._page.url))


class EagleBrowser:
    """The eagle's browser. Started once, kept until shutdown."""

    def __init__(self,
                 headless: bool | None = None,
                 profile_dir: Path | None = None,
                 launcher: Callable[[Any, Path, bool], Any] | None = None,
                 playwright_fn: Callable[[], Any] | None = None) -> None:
        if headless is None:
            # Headless by default (a private display): web_agency is non-exclusive, so
            # this browser never shows a window unasked. surface(True) shows it for a
            # sign-in. AETHELARK_BROWSER_HEADLESS=0 for debugging.
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
        # Guards start() and close(). An RLock: start() can call close().
        self._lifecycle_lock = threading.RLock()


    @property
    def running(self) -> bool:
        return self._page is not None and bool(self._thread
                                               and self._thread.is_alive())

    def start(self) -> None:
        """Launch the browser, or confirm it is up. Locked: two concurrent calls must
        not launch two browsers on one profile.
        """
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                if self._page is not None:
                    return  # already up and serving
                # The thread is alive but has no page: a previous launch failed. Tear it
                # down and retry, or one failure disables web grounding for the whole
                # process.
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
                # The caller already gave up; this job never started, so drop it.
                continue
            try:
                box.append(("ok", fn()))
            except BaseException as e:      # noqa: BLE001 — see below
                # BaseException, only here: an escape ends the thread, and the thread is
                # the whole eagle's browser. A cancelled Playwright expectation raises
                # CancelledError. The exception still goes back to the caller. Worker
                # threads never get KeyboardInterrupt.
                box.append(("err", e))
            finally:
                done.set()

    def close(self) -> None:
        """Tear the browser down, or confirm there is nothing to tear down. Same lock
        as start().
        """
        with self._lifecycle_lock:
            if self._thread is None:
                return
            page, self._page = self._page, None

            # Each step separately, and not cancellable: the context and the Playwright
            # driver are two separate leaks, and a skipped teardown keeps the profile
            # lock.

            self._release(page)

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

    def _release(self, page: Any) -> None:
        """Let go of what `_launcher` returned. Ours, so the whole context goes."""
        context = getattr(page, "context", None)
        if context is not None and hasattr(context, "close"):
            try:
                self._submit(lambda: context.close(), _TEARDOWN_TIMEOUT,
                             cancellable=False)
            except Exception:
                pass


    def _submit(self, fn: Callable[[], Any], timeout: float,
               cancellable: bool = True) -> Any:
        """Run `fn` on the browser thread and wait up to `timeout`. cancellable=False
        means the job runs even if the caller stops waiting (teardown).
        """
        if self._thread is None or not self._thread.is_alive():
            raise RuntimeError("browser thread is not running")
        box: list = []
        done = threading.Event()
        cancelled = threading.Event()
        self._jobs.put((fn, box, done, cancelled))
        if not done.wait(timeout):
            if cancellable:
                # Mark it cancelled: a job not yet started is dropped; one already
                # running completes (a dispatched click cannot be undone).
                cancelled.set()
            raise TimeoutError(f"browser call exceeded {timeout}s")
        if not box:
            # `done` set with no result: the thread exited mid-job.
            raise RuntimeError(
                "the browser thread died while this call was running")
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

    def surface(self, visible: bool) -> bool:
        """Show or hide the eagle's browser, keeping its profile. Restarts the
        browser, since Playwright fixes headless at launch; logins survive on
        disk. Returns True if it is now in the requested state.
        """
        with self._lifecycle_lock:
            # Already in the requested state when headless == (not visible).
            if self.running and self.headless == (not bool(visible)):
                return True
            self.close()
            self.headless = not bool(visible)
            self.start()
            return self.running and self.headless == (not bool(visible))

    def goto(self, url: str) -> str:
        """Navigate, let the page render, and return the URL landed on."""
        def _go(page):
            page.goto(url, timeout=_NAV_TIMEOUT_MS,
                      wait_until="domcontentloaded")
            _settle(page)
            return str(page.url)

        return self.call(_go, timeout=45.0)


# Rendering time after domcontentloaded. networkidle is not an option: YouTube never
# goes idle.
_SETTLE_MS = 1200

# Upper bound on waiting for a DOM that is still growing.
_SETTLE_MAX_MS = 1500

# Gap between size readings (one evaluate, about 1-3 ms).
_SETTLE_POLL_MS = 100

# Identical readings required before a page counts as settled.
_SETTLE_STABLE_POLLS = 2

_SETTLE_SIZE_JS = "document.getElementsByTagName('*').length"


def _settle(page) -> None:
    """Wait until the page stops changing, by watching the DOM's element count. A
    finished page is ready at once; a slow single-page app gets the full budget.
    If the page cannot be measured, wait the full budget.
    """
    try:
        page.wait_for_selector("body", timeout=_SETTLE_MAX_MS)
    except Exception:
        pass

    evaluate = getattr(page, "evaluate", None)
    if evaluate is None:
        try:
            page.wait_for_timeout(_SETTLE_MAX_MS)
        except Exception:
            pass
        return

    waited = 0
    last_size = -1
    stable = 0
    while waited < _SETTLE_MAX_MS:
        try:
            size = evaluate(_SETTLE_SIZE_JS)
        except Exception:
            # Cannot measure: wait out the budget.
            try:
                page.wait_for_timeout(_SETTLE_MAX_MS - waited)
            except Exception:
                pass
            return

        if size == last_size:
            stable += 1
            if stable >= _SETTLE_STABLE_POLLS:
                return
        else:
            stable = 0
            last_size = size

        try:
            page.wait_for_timeout(_SETTLE_POLL_MS)
        except Exception:
            return
        waited += _SETTLE_POLL_MS


_DEFAULT: EagleBrowser | None = None
# Guards the check-then-create: web_agency is non-exclusive, so two calls can reach
# default_browser() at once.
_DEFAULT_LOCK = threading.Lock()


def default_browser() -> EagleBrowser:
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = EagleBrowser()
        return _DEFAULT
