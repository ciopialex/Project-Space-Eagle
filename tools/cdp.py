"""Drive the eagle's own pages over the Chrome DevTools protocol.

Playwright cannot attach to Qt WebEngine: on connect it asks for browser contexts
("Browser context management is not supported") and gives up. Each page still
speaks DevTools, so this talks to pages directly. Clicks and keys are real input
events (Input.dispatch*), not element.click(): they go through hit-testing, focus
and the page's own handlers, the way a person's do.

    eagle = Eagle(9229)
    ob = eagle.page("onboarding.html")
    ob.click("text=Get started")
    ob.wait("document.querySelector('.screen.active').dataset.step == 'modules'")
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

from websockets.sync.client import connect


class PageError(AssertionError):
    pass


class Page:
    def __init__(self, name: str, ws_url: str, shots: Path | None = None):
        self.name, self._shots = name, shots
        self._ws = connect(ws_url, max_size=None, open_timeout=10)
        self._id = 0

    def close(self) -> None:
        self._ws.close()

    def call(self, method: str, **params):
        self._id += 1
        self._ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        while True:
            msg = json.loads(self._ws.recv(timeout=30))
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise PageError(f"{self.name}: {method}: {msg['error'].get('message')}")
                return msg.get("result", {})

    def eval(self, expr: str):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            raise PageError(f"{self.name}: {expr[:80]!r} threw "
                            f"{r['exceptionDetails'].get('exception', {}).get('description', r['exceptionDetails'])}")
        return r.get("result", {}).get("value")

    def wait(self, expr: str, timeout: float = 10, what: str = ""):
        """Poll a JavaScript expression until it is truthy; return its value."""
        end = time.monotonic() + timeout
        while True:
            try:
                v = self.eval(expr)
            except PageError:
                v = None
            if v:
                return v
            if time.monotonic() > end:
                shot = self.screenshot(f"timeout-{int(time.time())}")
                raise PageError(f"{self.name}: waited {timeout:.0f}s for {what or expr}"
                                + (f" (screenshot {shot})" if shot else ""))
            time.sleep(0.15)

    # -------------------------------------------------- finding things
    @staticmethod
    def _find_js(selector: str) -> str:
        """`text=Foo` finds the visible element whose own text is exactly Foo;
        anything else is a CSS selector. Hidden elements never match."""
        if selector.startswith("text="):
            want = json.dumps(selector[5:])
            return ("(() => { const want = %s;"
                    " const vis = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);"
                    "   return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };"
                    " return [...document.querySelectorAll('button,a,[onclick],.chip,.btn,.link,span,div,h1,p,label')]"
                    "  .filter(e => (e.innerText || '').trim() === want && vis(e))"
                    "  .sort((a, b) => a.innerText.length - b.innerText.length)[0] || null; })()" % want)
        return ("(() => { const vis = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);"
                " return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };"
                " return [...document.querySelectorAll(%s)].find(vis) || null; })()" % json.dumps(selector))

    def exists(self, selector: str) -> bool:
        return bool(self.eval(f"!!{self._find_js(selector)}"))

    def text(self, selector: str) -> str:
        return self.eval(f"(({self._find_js(selector)}) || {{}}).innerText || ''")

    def center(self, selector: str, timeout: float = 10) -> tuple[float, float]:
        js = (f"(() => {{ const e = {self._find_js(selector)}; if (!e) return null;"
              " e.scrollIntoView({block: 'center'}); const r = e.getBoundingClientRect();"
              " return [r.left + r.width / 2, r.top + r.height / 2, !e.disabled]; })()")
        x, y, enabled = self.wait(js, timeout, what=f"{selector!r} to be visible")
        # Windows open and cards morph with a spring: a click aimed while it moves
        # lands where the element was. Wait until it holds still.
        end, last = time.monotonic() + timeout, None
        while (x, y) != last and time.monotonic() < end:
            last = (x, y)
            time.sleep(0.12)
            x, y, enabled = self.wait(js, timeout, what=f"{selector!r} to be visible")
        if not enabled:
            self.wait(f"(() => {{ const e = {self._find_js(selector)}; return e && !e.disabled; }})()",
                      timeout, what=f"{selector!r} to be enabled")
        return x, y

    # -------------------------------------------------- doing things
    def click(self, selector: str, count: int = 1, timeout: float = 10) -> None:
        x, y = self.center(selector, timeout)
        self.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        for n in range(1, count + 1):
            for kind in ("mousePressed", "mouseReleased"):
                self.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", clickCount=n)

    def type(self, text: str) -> None:
        """Into whatever has focus, a character at a time, like a keyboard."""
        for ch in text:
            self.call("Input.dispatchKeyEvent", type="keyDown", text=ch, key=ch, unmodifiedText=ch)
            self.call("Input.dispatchKeyEvent", type="keyUp", key=ch)

    def paste(self, text: str) -> None:
        """Into whatever has focus, in one go, like Ctrl+V."""
        self.call("Input.insertText", text=text)

    def press(self, key: str) -> None:
        codes = {"Enter": (13, "\r"), "Escape": (27, ""), "Tab": (9, "\t"), "Backspace": (8, "")}
        code, text = codes[key]
        self.call("Input.dispatchKeyEvent", type="rawKeyDown", key=key, code=key,
                  windowsVirtualKeyCode=code, nativeVirtualKeyCode=code)
        if text:
            self.call("Input.dispatchKeyEvent", type="char", key=key, text=text)
        self.call("Input.dispatchKeyEvent", type="keyUp", key=key, code=key,
                  windowsVirtualKeyCode=code, nativeVirtualKeyCode=code)

    def screenshot(self, name: str) -> Path | None:
        if not self._shots:
            return None
        import base64
        try:
            data = self.call("Page.captureScreenshot", format="png")["data"]
        except Exception:
            return None
        self._shots.mkdir(parents=True, exist_ok=True)
        path = self._shots / f"{self.name.replace('.html', '')}-{name}.png"
        path.write_bytes(base64.b64decode(data))
        return path


class Eagle:
    """The eagle's pages, found by file name (pill.html, dashboard.html, ...)."""

    def __init__(self, port: int, shots: Path | None = None):
        self.port, self.shots = port, shots
        self._pages: dict[str, Page] = {}

    def targets(self) -> dict[str, str]:
        try:
            raw = json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=2))
        except OSError:
            return {}
        return {t["url"].rsplit("/", 1)[-1]: t["webSocketDebuggerUrl"]
                for t in raw if t.get("type") == "page" and t.get("webSocketDebuggerUrl")}

    def page(self, name: str, timeout: float = 60) -> Page:
        if name in self._pages:
            return self._pages[name]
        end = time.monotonic() + timeout
        while True:
            url = self.targets().get(name)
            if url:
                page = Page(name, url, self.shots)
                page.wait("document.readyState === 'complete'", 20, what=f"{name} to load")
                self._pages[name] = page
                return page
            if time.monotonic() > end:
                raise PageError(f"{name} never appeared; pages open: {sorted(self.targets())}")
            time.sleep(0.25)

    def forget(self, name: str) -> None:
        page = self._pages.pop(name, None)
        if page:
            page.close()

    def close(self) -> None:
        for page in self._pages.values():
            page.close()
        self._pages.clear()
