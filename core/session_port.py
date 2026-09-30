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

import asyncio
from typing import Any

from actions.grounding.web.page import (COLLECT_JS, DOM_SIGNATURE_JS,
                                        HIT_TEST_JS)

_TIMEOUT = 20

#: Hard ceiling on browser launches. A runaway is not hypothetical: a loop
#: that opens a window each pass leaves a pile of Chromes, and those hold the
#: profile lock, which is what then makes the eagle's OWN browser refuse to
#: start and blame Playwright for it. Every other guard here is about not
#: launching WRONGLY. This one is about not launching MANY, whatever the bug
#: turns out to be — it does not need to know why.
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
    """Cleared when a mission ends and its browsers are released."""
    global _launches
    _launches = 0




class _MarshaledEventCtx:
    """Async counterpart of `browser.py`'s class of the same name — same
    job, same reason, adapted to the async Playwright page this session
    drives instead of a thread-marshaled sync one.

    `outcome.py`'s `click_with_outcome` uses `page.expect_download`/
    `expect_popup` as an ordinary (sync-looking) `with` statement, with the
    real click happening INSIDE the block, between `__enter__` and
    `__exit__`. Each of those, plus reading `.value`, is its own
    `self._sess.run(...)` — not one call wrapping the whole block — so the
    click in between (itself its own `run()`, via `SessionPort.click`) plays
    out in the right order on the session's one event loop, the same way
    `browser.py`'s version interleaves with the browser thread's job queue.
    Without this, `SessionPort` has no `expect_download`/`expect_popup` at
    all, so `click_with_outcome` never actually clicks anything for the
    user's window either — see `browser.py`'s `_MarshaledEventCtx` for the
    full account of the bug this closes.
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
            # Same reasoning as `browser.py`'s sync twin: Playwright's
            # `__aexit__` cancels the expectation when an exception is
            # active in the block (a raising click), and a cancelled
            # expectation surfaces as `asyncio.CancelledError` — a
            # `BaseException` that `except Exception` does not catch. Here
            # it escaped out of `user_click` rather than killing a thread,
            # but it is the same bug wearing different clothes. See
            # `_EXPECTATION_FAILED` in outcome.py.
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

    def dom_signature(self) -> str:
        """The DOM half of `click_with_outcome`'s `"url_or_dom_changed"`
        signal, for the user's own window — see `PagePort.dom_signature`
        (browser.py) and `DOM_SIGNATURE_JS` (page.py). Read-only: it must
        not be a `collect()`, which would re-stamp every `data-ae-ref` and
        invalidate the candidates still queued behind this click.
        """
        async def _do():
            page = await self._sess._get_page()
            return await page.evaluate(DOM_SIGNATURE_JS)
        try:
            return str(self._sess.run(_do(), timeout=_TIMEOUT) or "")
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

    def mouse_click(self, x: int, y: int) -> None:
        """Click a raw page coordinate — the vision fallback's only lever
        when structural matching found nothing to put a ref on. Mirrors
        `PagePort.mouse_click` (browser.py) for the user's own window."""
        async def _do():
            page = await self._sess._get_page()
            await page.mouse.click(int(x), int(y))
        self._sess.run(_do(), timeout=_TIMEOUT)

    def expect_download(self, timeout: int | None = None) -> _MarshaledEventCtx:
        """`click_with_outcome`'s (outcome.py) real-signal detection needs
        this on the user's window too — see `_MarshaledEventCtx` above."""
        tms = 30_000 if timeout is None else timeout
        async def _open():
            page = await self._sess._get_page()
            return page.expect_download(timeout=tms)
        return _MarshaledEventCtx(self._sess, _open, _TIMEOUT)

    def expect_popup(self, timeout: int | None = None) -> _MarshaledEventCtx:
        """The "a new tab opened" half of `click_with_outcome`'s detection —
        see `expect_download` just above and `_MarshaledEventCtx`."""
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
        """Type into whatever the PAGE has focused. "" if nothing editable is.

        Exact, not blind: the browser knows which element has focus, so this
        cannot leak into another window the way the OS keyboard does. Returns
        a description of where the text went, so the caller can say.
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


def user_window(browser: str | None = None, create: bool = False):
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
    # `_registry.get()` CREATES a session — it launches Chrome. Reported live
    # as "a blank page keeps opening out of nowhere": _user_click, _user_type
    # and _user_look all came through here, so a CLICK step with no window
    # open launched an empty browser, did nothing with it, and left it
    # running. Only a step that MEANS to open a page may create one; for the
    # rest, "there is no window" is the honest answer.
    try:
        from actions.browser_control import _registry
        # A NATIVE open leaves no session but there IS a page the user is
        # looking at; reaching it is the whole reason this function exists.
        # So "may create" means: asked to, or there is a page waiting to be
        # followed. Neither is true for a click with nothing open.
        has = _registry.has(browser)
        pending = ""
        if not has:
            # Consumed, not peeked — if there is one we are about to follow it.
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

    # A native open left the address here rather than in any page. Follow it,
    # exactly once — `pop` clears it so a later step does not get yanked back
    # to the start of the mission.
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
