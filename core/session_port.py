"""Act on the browser the eagle opened for the user, through the DOM. browser_control
already holds a Playwright Page for that window; each method here runs one
coroutine on its event loop via sess.run(), so WebGrounder drives the user's
window unchanged.
"""
from __future__ import annotations

import asyncio
from typing import Any

from actions.grounding.web.page import (COLLECT_JS, DOM_SIGNATURE_JS,
                                        HIT_TEST_JS)

_TIMEOUT = 20

# Hard ceiling on browser launches: a runaway loop of Chromes holds the profile lock and
# breaks the eagle's own browser.
MAX_LAUNCHES = 4
_launches = 0


def _spend_launch() -> bool:
    """Take one from the budget. False when it is gone."""
    global _launches
    if _launches >= MAX_LAUNCHES:
        print(f"[SessionPort] refusing to open another browser — {_launches} "
              f"already opened (cap {MAX_LAUNCHES}). Something is looping.")
        return False
    _launches += 1
    return True


def launches_used() -> int:
    return _launches


def reset_launch_budget() -> None:
    """Reset when a mission ends and its browsers are released."""
    global _launches
    _launches = 0


class _MarshaledEventCtx:
    """Async twin of browser.py's _MarshaledEventCtx: __enter__, __exit__ and .value
    each run on the session's loop, so the click inside the block happens in
    order.
    """

    def __init__(self, sess: Any, open_ctx, timeout: float) -> None:
        self._sess = sess
        self._open_ctx = open_ctx      # zero-arg async -> a fresh raw CM
        self._timeout = timeout
        self._raw_cm: Any = None
        self._raw_value: Any = None

    def __enter__(self) -> "_MarshaledEventCtx":
        async def _do():
            cm = await self._open_ctx()
            return cm, await cm.__aenter__()
        self._raw_cm, self._raw_value = self._sess.run(
            _do(), timeout=self._timeout)
        return self

    def __exit__(self, *exc: Any) -> bool:
        try:
            async def _do():
                return await self._raw_cm.__aexit__(*exc)
            self._sess.run(_do(), timeout=self._timeout)
        except (Exception, asyncio.CancelledError):
            # A cancelled expectation raises CancelledError (a BaseException); see
            # _EXPECTATION_FAILED in outcome.py.
            pass
        return False

    @property
    def value(self) -> Any:
        async def _do():
            return await self._raw_value.value
        return self._sess.run(_do(), timeout=self._timeout)


class SessionPort:
    """`PageLike` over `browser_control`'s live async session."""

    def __init__(self, sess: Any) -> None:
        self._sess = sess


    def collect(self) -> list[dict]:
        async def _do():
            page = await self._sess._get_page()
            return await page.evaluate(COLLECT_JS)
        return self._sess.run(_do(), timeout=_TIMEOUT) or []

    def hit_test(self, x: int, y: int) -> dict | None:
        async def _do():
            page = await self._sess._get_page()
            return await page.evaluate(HIT_TEST_JS, {"x": int(x), "y": int(y)})
        try:
            return self._sess.run(_do(), timeout=_TIMEOUT)
        except Exception:
            return None

    def screenshot(self) -> bytes:
        async def _do():
            page = await self._sess._get_page()
            return await page.screenshot()
        return self._sess.run(_do(), timeout=_TIMEOUT)

    def url(self) -> str:
        async def _do():
            page = await self._sess._get_page()
            return page.url
        try:
            return self._sess.run(_do(), timeout=_TIMEOUT) or ""
        except Exception:
            return ""

    def dom_signature(self) -> str:
        """The DOM half of url_or_dom_changed for the user's window. Read-only, never
        a collect().
        """
        async def _do():
            page = await self._sess._get_page()
            return await page.evaluate(DOM_SIGNATURE_JS)
        try:
            return str(self._sess.run(_do(), timeout=_TIMEOUT) or "")
        except Exception:
            return ""


    def click(self, ref: str) -> None:
        selector = f'[data-ae-ref="{ref}"]'

        async def _do():
            page = await self._sess._get_page()
            try:
                await page.click(selector, timeout=4_000)
            except Exception:
                # Last resort: dispatch the click on the located element.
                await page.eval_on_selector(selector, "el => el.click()")
        self._sess.run(_do(), timeout=_TIMEOUT)

    def mouse_click(self, x: int, y: int) -> None:
        """Click a raw page coordinate: the vision fallback."""
        async def _do():
            page = await self._sess._get_page()
            await page.mouse.click(int(x), int(y))
        self._sess.run(_do(), timeout=_TIMEOUT)

    def expect_download(self, timeout: int | None = None) -> _MarshaledEventCtx:
        """Needed by click_with_outcome on the user's window too."""
        tms = 30_000 if timeout is None else timeout
        async def _open():
            page = await self._sess._get_page()
            return page.expect_download(timeout=tms)
        return _MarshaledEventCtx(self._sess, _open, _TIMEOUT)

    def expect_popup(self, timeout: int | None = None) -> _MarshaledEventCtx:
        """The new-tab half of click_with_outcome's detection."""
        tms = 30_000 if timeout is None else timeout
        async def _open():
            page = await self._sess._get_page()
            return page.expect_popup(timeout=tms)
        return _MarshaledEventCtx(self._sess, _open, _TIMEOUT)

    def fill(self, ref: str, text: str) -> None:
        selector = f'[data-ae-ref="{ref}"]'

        async def _do():
            page = await self._sess._get_page()
            await page.fill(selector, text, timeout=4_000)
        self._sess.run(_do(), timeout=_TIMEOUT)

    def type_into_focused(self, text: str) -> str:
        """Type into whatever the page has focused, or "" if nothing editable is.
        Cannot leak into another window. Returns where the text went.
        """
        async def _do():
            page = await self._sess._get_page()
            what = await page.evaluate(
                "() => { const a = document.activeElement;"
                " if (!a || a === document.body) return '';"
                " const tag = a.tagName.toLowerCase();"
                " if (!(tag === 'input' || tag === 'textarea' ||"
                "       a.isContentEditable)) return '';"
                " return a.getAttribute('aria-label') || a.getAttribute('name')"
                "        || a.getAttribute('placeholder') || tag; }")
            if not what:
                return ""
            await page.keyboard.type(text)
            return what
        return self._sess.run(_do(), timeout=_TIMEOUT) or ""

    def goto(self, url: str) -> None:
        async def _do():
            page = await self._sess._get_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        self._sess.run(_do(), timeout=45)

    def press(self, key: str) -> None:
        async def _do():
            page = await self._sess._get_page()
            await page.keyboard.press(key)
        self._sess.run(_do(), timeout=_TIMEOUT)


def peek_window(browser: str | None = None):
    """The user's window without touching it. Observers must use this: following a
    pending URL is a navigation.
    """
    try:
        from actions.browser_control import _registry
        if not _registry.has(browser):
            return None, None
        sess = _registry.get(browser)
    except Exception:
        return None, None
    if sess is None:
        return None, None
    try:
        from actions.grounding.web.grounder import WebGrounder
        port = SessionPort(sess)
        return port, WebGrounder(lambda: port)
    except Exception:
        return None, None


def user_window(browser: str | None = None, create: bool = False):
    """(SessionPort, WebGrounder) for the user-facing browser, or (None, None) when
    there is no window, which callers must not report as a missing control.

    browser_control's go_to opens natively and only notes the URL; this creates
    the controllable session when needed and navigates it to that URL.
    """
    # _registry.get() launches Chrome. Only a step meant to open a page, or one
    # following a native open, may create a session.
    try:
        from actions.browser_control import _registry
        # A native open left a page to follow.
        has = _registry.has(browser)
        pending = ""
        if not has:
            # Consumed, not peeked.
            pending = _registry.pop_native_url() or ""
        if not (has or create or pending):
            return None, None
        if not has and not _spend_launch():
            return None, None
        sess = _registry.get(browser)
    except Exception:
        return None, None
    if sess is None:
        return None, None

    # Follow the noted address exactly once.
    try:
        if pending:
            sess.run(sess.go_to(pending), timeout=45)
    except Exception as e:
        print(f"[SessionPort] could not follow the last page: {e}")
    try:
        from actions.grounding.web.grounder import WebGrounder
        port = SessionPort(sess)
        return port, WebGrounder(lambda: port)
    except Exception:
        return None, None
