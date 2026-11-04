"""Did the click do something anywhere on the page? (act_and_verify checks only the
clicked element itself.)

Three signals: a download starting, a new tab opening, or the URL or DOM changing.
The first two use Playwright's event expectations wrapped around the click; the
third is a before/after reading.
"""
from __future__ import annotations

import asyncio
from typing import Callable

# A cancelled expectation raises CancelledError (a BaseException) when the click inside
# the block raised. Uncaught, it killed the browser thread for the whole process.
# Exception is listed explicitly so a KeyboardInterrupt is not read as a failed click.
_EXPECTATION_FAILED = (Exception, asyncio.CancelledError)


def _current_url(page) -> str:
    """page.url, whichever shape it is: a method on PagePort (two bound methods
    always compare equal) or a plain attribute on test fakes.
    """
    url = getattr(page, "url", "")
    return url() if callable(url) else (url or "")


def _dom_signature(page) -> str:
    """A hash of the page now, or "" for cannot tell, which never counts as
    unchanged.
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
    """Click `ref` via click_fn and return which outcome fired: "download",
    "new_page", "url_or_dom_changed", or "". Never raises.

    `timeout_ms` is for the whole call: Playwright spends a timed-out
    expectation's wait inside __exit__, so the two nested expectations each get
    half.
    """
    # Read before the click, URL first, so it is as close to the click as possible.
    before_url = _current_url(page)
    before_dom = _dom_signature(page)

    per_signal = max(1, int(timeout_ms) // 2)

    popup_info = None
    dl_info = None

    # Click exactly once; outcomes are checked independently after.
    try:
        with page.expect_download(timeout=per_signal) as dl_info:
            try:
                with page.expect_popup(timeout=per_signal) as popup_info:
                    click_fn(ref)
            except _EXPECTATION_FAILED:
                # A timed-out popup wait is normal. This also swallows what click_fn
                # raised; WebGrounder.resolve_and_click keeps its own record of that.
                pass
    except _EXPECTATION_FAILED:
        pass

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

    # The DOM half, so a non-navigational click (checkbox, menu, filter) is seen to
    # work. Both readings must be real; "" never counts as a change.
    after_dom = _dom_signature(page)
    if before_dom and after_dom and before_dom != after_dom:
        return "url_or_dom_changed"
    return ""
