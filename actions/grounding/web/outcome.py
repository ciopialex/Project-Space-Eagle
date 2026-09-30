"""Did the click actually DO something?

`act_and_verify` (actions/grounding/verify.py) already checks whether the
CLICKED ELEMENT ITSELF changed - its own bounds, states, value. That is a
real, different question from this one. Tonight's live bug: a click landed
on a card that opened an inline preview overlay. The element clicked may
report no change in its own state at all, while the click still did
something real elsewhere on the page - or, as happened tonight, nothing
real happened anywhere, and the tool reported success regardless.

Three concrete signals: a download starting, a new tab/page opening, or the
URL or DOM changing. The first two are checked with Playwright's own
event-expectation APIs (wrapped around the click, not polled after the
fact - faster, and it catches the specific event type instead of guessing
how long to wait). The third is a before/after reading of the page itself.
"""
from __future__ import annotations

import asyncio
from typing import Callable

#: A cancelled event expectation raises `asyncio.CancelledError`, which is a
#: `BaseException` and NOT caught by `except Exception`. It reaches here by a
#: completely ordinary route: `click_fn` raises inside the `with` block (a
#: stale ref — the exact case the caller's own error side-channel exists
#: for), Playwright's `EventContextManager.__exit__` sees an active exception
#: and cancels the pending future, and reading `.value` off a cancelled
#: future re-raises the cancellation.
#:
#: Left uncaught, that `BaseException` does not just fail this call. On the
#: eagle's own browser every one of these reads runs as a job on the single
#: browser thread (`_MarshaledEventCtx`, browser.py), whose loop caught only
#: `Exception` — so it killed the thread, and every later browser call in the
#: whole process failed with "browser thread is not running". On the user's
#: window (core/session_port.py) it escaped out of `user_click` instead.
#:
#: `Exception` is still listed explicitly rather than reaching for a bare
#: `BaseException`: a `KeyboardInterrupt` here is a person asking the eagle
#: to stop, and must not be read as "that click did not work".
_EXPECTATION_FAILED = (Exception, asyncio.CancelledError)


def _current_url(page) -> str:
    """`page.url`, whichever shape it is.

    On the real `PagePort` (actions/grounding/web/browser.py), `url` is a
    METHOD, not an attribute - `getattr(page, "url", "")` against a real
    instance returns the bound method object itself, never the string. Two
    separately-obtained bound methods of the same instance always compare
    equal in Python, so a before/after comparison built on that never
    detects a change - it silently reports "nothing happened" every time,
    no matter what really occurred. The fakes in test_click_outcome.py use
    a plain string attribute instead (matching Task 1's brief, not the real
    interface), which is how this shipped without a failing test. Both
    conventions are supported here because Task 2's fakes also use the
    plain-attribute shape.
    """
    url = getattr(page, "url", "")
    return url() if callable(url) else (url or "")


def _dom_signature(page) -> str:
    """A hash of what the page currently is, or "" for "cannot tell".

    "" is load-bearing: a page-like object with no `dom_signature` at all,
    or one whose read failed, must fall back to the URL comparison alone and
    never be read as "the DOM did not change" — that claim would be
    invented, not observed. See `DOM_SIGNATURE_JS` (page.py) for what the
    real reading covers and why it is not a `collect()`.
    """
    read = getattr(page, "dom_signature", None)
    if not callable(read):
        return ""
    try:
        return str(read() or "")
    except _EXPECTATION_FAILED:
        return ""


def click_with_outcome(page, ref: str, click_fn: Callable[[str], None], *,
                       timeout_ms: int = 2000) -> str:
    """Click `ref` via `click_fn`, return which real outcome fired.

    One of "download", "new_page", "url_or_dom_changed", or "" if none
    fired. Never raises - a timeout on any individual signal is the normal
    "that wasn't it" case, not an error.

    `timeout_ms` is the budget for this WHOLE call, not per signal. That
    distinction is the difference between the ~5s retry contract this was
    built to meet and roughly twice that: two event expectations are nested
    around the one click, and Playwright spends a timed-out expectation's
    wait inside `__exit__` (verified against playwright 1.61,
    `_impl/_sync_base.py`: `__exit__` with no active exception reads
    `.value`, which blocks until the future resolves). Two nested waits, one
    click, so an unnested budget is spent twice per candidate — and with
    three tied candidates, six times per call. Each expectation therefore
    gets half.

    The cost of halving is a shorter window for a download or popup to
    arrive after the click; both are dispatched by the browser as soon as
    the click is handled, and downloads have their own dedicated action
    (`_download`, web_agency.py) that does not depend on this at all.
    """
    # Read BEFORE the click, and read the URL first: `dom_signature` is a
    # page evaluate, so doing it second keeps the URL reading as close to
    # the click as it can be.
    before_url = _current_url(page)
    before_dom = _dom_signature(page)

    per_signal = max(1, int(timeout_ms) // 2)

    popup_info = None
    dl_info = None

    # Perform the click exactly once, with both expectations set up around it.
    # Do not re-click on timeout - that's handled by independent outcome checks below.
    try:
        with page.expect_download(timeout=per_signal) as dl_info:
            try:
                with page.expect_popup(timeout=per_signal) as popup_info:
                    click_fn(ref)
            except _EXPECTATION_FAILED:
                # Popup wait failed or timed out, continue to check outcomes.
                # This also swallows whatever `click_fn` itself raised — the
                # caller that needs to know about THAT (a stale ref is a
                # click that never landed, not a click with no signal) keeps
                # its own side channel; see `WebGrounder.resolve_and_click`.
                pass
    except _EXPECTATION_FAILED:
        # Download wait failed or timed out, continue to check outcomes
        pass

    # Check each outcome independently (do not re-click).
    if popup_info is not None:
        try:
            popup_info.value
            return "new_page"
        except _EXPECTATION_FAILED:
            pass

    if dl_info is not None:
        try:
            dl_info.value
            return "download"
        except _EXPECTATION_FAILED:
            pass

    after_url = _current_url(page)
    if after_url != before_url:
        return "url_or_dom_changed"

    # The DOM half of the same signal, and the reason this function's name
    # stopped being a half-truth. Without it EVERY non-navigational click —
    # a checkbox, a disclosure triangle, an in-page filter, a menu — reported
    # "nothing happened": the retry loop then clicked the next tied candidate
    # and toggled the first one's effect straight back off, and the caller
    # could not tell a verified success from a verified nothing.
    #
    # Both readings must be real. A missing or failed reading is "" on either
    # side, and "" never counts as a change — the only honest answer when the
    # page could not be read is the one this already gives for a signal that
    # did not fire.
    after_dom = _dom_signature(page)
    if before_dom and after_dom and before_dom != after_dom:
        return "url_or_dom_changed"
    return ""
