"""Act on the browser the eagle opened FOR THE USER, through the DOM.

The chain this closes, traced on a real mission:

  1. `web_open` correctly fails — makerworld serves a bot wall to the eagle's
     own headless browser.
  2. `browser_open` succeeds — the page opens in the user's real Chrome.
  3. Every step after that had nowhere to go. `web_click` looks in the eagle's
     browser (blank), `screen_click` is blind because Chrome publishes nothing
     to the accessibility bus, and vision guesses — measured 5.8s and ~650px
     off. The eagle could SEE the page and had no way to touch it.

The first attempt at this launched that Chrome with `--remote-debugging-port`
and connected over CDP from outside. It does not work: Playwright's
`launch_persistent_context` manages its own CDP channel, and no listener
appears on the port. It was also unnecessary — `browser_control` is ALREADY
holding a Playwright `Page` for that window. The fix is to reach into the
session that exists rather than open a second door to it.

The only wrinkle is that the session is async and lives on its own event loop,
while `PageLike` (and everything above it — the grounder, the actionability
check, the waiting loop) is synchronous. `sess.run()` already marshals a
coroutine onto that loop and blocks for the result, so each method here is one
`run()` away from the same protocol the eagle uses on its own browser. That is
what lets `WebGrounder` drive the user's window with no changes at all.
"""
from __future__ import annotations

from typing import Any

from actions.grounding.web.page import COLLECT_JS, HIT_TEST_JS

_TIMEOUT = 20


class SessionPort:
    """`PageLike` over `browser_control`'s live async session."""

    def __init__(self, sess: Any) -> None:
        self._sess = sess

    # ── perception ──────────────────────────────────────────────────────────

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

    # ── actuation ───────────────────────────────────────────────────────────

    def click(self, ref: str) -> None:
        selector = f'[data-ae-ref="{ref}"]'

        async def _do():
            page = await self._sess._get_page()
            try:
                await page.click(selector, timeout=4_000)
            except Exception:
                # Same last resort the eagle's own port uses: deliver the click
                # to the element rather than fail on one we located.
                await page.eval_on_selector(selector, "el => el.click()")
        self._sess.run(_do(), timeout=_TIMEOUT)

    def fill(self, ref: str, text: str) -> None:
        selector = f'[data-ae-ref="{ref}"]'

        async def _do():
            page = await self._sess._get_page()
            await page.fill(selector, text, timeout=4_000)
        self._sess.run(_do(), timeout=_TIMEOUT)

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
    """The user's window WITHOUT touching it.

    `user_window` follows the url a native open left behind — which is a
    navigation, and therefore a change to the world. Anything that merely
    OBSERVES must use this instead: an observer with a side effect cannot be
    used to detect side effects, and the first version of the movement check
    reported "nothing changed" on every step because looking had already
    moved things.
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


def user_window(browser: str | None = None):
    """`(SessionPort, WebGrounder)` for the user-facing browser, or `(None, None)`.

    `browser_control` has TWO ways of opening a page and only one of them can
    be driven. `go_to` opens NATIVELY — fast, visible, and completely
    uncontrollable — and merely *notes* the url. A controllable window is
    created lazily, the first time an interactive action needs one, and then
    navigated to that noted url. That is why the first version of this
    function found nothing: it asked `_registry.has()`, which is False after a
    native open, and a mission that had just successfully opened MakerWorld
    was told "no browser window is open for the user".

    So this does what `browser_control` does for its own interactive actions:
    create the session if needed, then sync it to wherever the user was last
    sent. Getting that wrong the other way is worse — an earlier version
    created a session and left it on about:blank, so the grounder read ZERO
    controls off a window that visibly had a page in it.

    `(None, None)` means there is no window at all — a different thing from
    "the control is not there", and callers must not report it as the latter.
    """
    try:
        from actions.browser_control import _registry
        sess = _registry.get(browser)
    except Exception:
        return None, None
    if sess is None:
        return None, None

    # A native open left the address here rather than in any page. Follow it,
    # exactly once — `pop` clears it so a later step does not get yanked back
    # to the start of the mission.
    try:
        last = _registry.pop_native_url()
        if last:
            sess.run(sess.go_to(last), timeout=45)
    except Exception as e:
        print(f"[SessionPort] could not follow the last page: {e}")
    try:
        from actions.grounding.web.grounder import WebGrounder
        port = SessionPort(sess)
        return port, WebGrounder(lambda: port)
    except Exception:
        return None, None
