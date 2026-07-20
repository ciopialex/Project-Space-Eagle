"""Autonomous visual web verification (Playwright coupling).

When a delegated agent modifies web files (HTML/CSS/JS) in its working
directory, a watcher renders the page in a dedicated headless Chromium,
captures a screenshot plus console/page errors, shows the screenshot on
the HUD, and — if the page is broken — pipes the errors straight back
into the agent's session so it can self-correct.

This runs its own headless browser; the user's interactive browsing
session in browser_control.py is never touched.
"""

import threading
import time
from pathlib import Path

WEB_EXTS = {".html", ".htm", ".css", ".js"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__",
             ".space_eagle", "dist", "build"}
SCAN_INTERVAL_S = 2.0
DEBOUNCE_S = 1.5
PAGE_TIMEOUT_MS = 15000
VIEWPORT = {"width": 1280, "height": 800}


def verify_page(target: str) -> dict:
    """Render a page (file path or URL) headlessly; return screenshot + errors.

    Runs the sync Playwright API — call from a worker thread, never from
    an asyncio event loop thread.
    """
    from playwright.sync_api import sync_playwright

    url = target
    if "://" not in target:
        url = Path(target).resolve().as_uri()

    console_errors, page_errors = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport=VIEWPORT)
            page.on("console", lambda m: console_errors.append(m.text)
                    if m.type == "error" else None)
            page.on("pageerror", lambda e: page_errors.append(str(e)))
            page.goto(url, timeout=PAGE_TIMEOUT_MS, wait_until="load")
            page.wait_for_timeout(400)  # let first paint/JS settle
            shot = page.screenshot(type="png")
            title = page.title()
        finally:
            browser.close()

    return {"url": url, "title": title, "screenshot": shot,
            "console_errors": console_errors[:10],
            "page_errors": page_errors[:10],
            "ok": not (console_errors or page_errors)}


def _entry_page(root: Path, changed: Path) -> Path | None:
    """The page to render for a change: the HTML file itself, or the
    nearest index.html above a changed css/js file."""
    if changed.suffix in (".html", ".htm"):
        return changed
    for parent in [changed.parent, *changed.parent.parents]:
        idx = parent / "index.html"
        if idx.exists():
            return idx
        if parent == root:
            break
    return None


class WebWatcher:
    """Watches one directory tree; verifies visually on web-file changes."""

    def __init__(self, root: Path, player=None, session=None):
        self.root = Path(root).resolve()
        self.player = player
        self.session = session  # PtySession to feed errors back into
        self._mtimes = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name=f"webwatch-{self.root.name}", daemon=True)
        self._prime()
        self._thread.start()

    def _iter_web_files(self):
        stack = [self.root]
        seen = 0
        while stack:
            d = stack.pop()
            try:
                for entry in d.iterdir():
                    if entry.is_dir():
                        if entry.name not in SKIP_DIRS:
                            stack.append(entry)
                    elif entry.suffix in WEB_EXTS:
                        yield entry
                        seen += 1
                        if seen > 2000:
                            return
            except OSError:
                continue

    def _prime(self):
        for f in self._iter_web_files():
            try:
                self._mtimes[f] = f.stat().st_mtime
            except OSError:
                pass

    def _changed_files(self):
        changed = []
        for f in self._iter_web_files():
            try:
                mt = f.stat().st_mtime
            except OSError:
                continue
            if self._mtimes.get(f) != mt:
                self._mtimes[f] = mt
                changed.append(f)
        return changed

    def _loop(self):
        while not self._stop.is_set():
            time.sleep(SCAN_INTERVAL_S)
            changed = self._changed_files()
            if not changed:
                continue
            # Debounce: wait for the agent to finish its write burst.
            while True:
                time.sleep(DEBOUNCE_S)
                more = self._changed_files()
                if not more:
                    break
                changed.extend(more)
            page = next((p for f in changed
                         if (p := _entry_page(self.root, f))), None)
            if page:
                try:
                    self._verify_and_report(page)
                except Exception as e:
                    self._hud(f"SYS: Visual verification error: {e}")

    def _hud(self, msg):
        if self.player:
            self.player.write_log(msg)
        print(msg)

    def _verify_and_report(self, page: Path):
        rel = page.relative_to(self.root)
        self._hud(f"SYS: 👁 Visually verifying {rel} in headless Chromium...")
        result = verify_page(str(page))

        if self.player:
            try:
                self.player.show_camera_frame(result["screenshot"])
            except Exception:
                pass

        errors = result["console_errors"] + result["page_errors"]
        if errors:
            summary = " | ".join(e[:160] for e in errors[:4])
            self._hud(f"SYS: 👁 {rel} renders with ERRORS: {summary}")
            if self.session and self.session.is_alive():
                try:
                    self.session.send_line(
                        f"[SWARM UPDATE from eagle] Visual verification of "
                        f"{rel} found browser errors — fix them: {summary}")
                except OSError:
                    pass
        else:
            self._hud(f"SYS: 👁 {rel} renders clean "
                      f"(title: {result['title'] or 'untitled'}).")

    def stop(self):
        self._stop.set()


_WATCHERS = {}
_WATCHERS_LOCK = threading.Lock()


def watch_directory(root, player=None, session=None):
    """Idempotently start visual verification for a directory tree."""
    key = str(Path(root).resolve())
    with _WATCHERS_LOCK:
        w = _WATCHERS.get(key)
        if w and not w._stop.is_set():
            w.player = player or w.player
            w.session = session or w.session
            return w
        w = WebWatcher(root, player=player, session=session)
        _WATCHERS[key] = w
        return w


def stop_all_watchers():
    with _WATCHERS_LOCK:
        for w in _WATCHERS.values():
            w.stop()
        _WATCHERS.clear()
