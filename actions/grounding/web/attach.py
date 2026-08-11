"""Reach into the Chrome the eagle already opened for the user.

The trap this closes, from a real MakerWorld session: `web_agency` hit a
Cloudflare wall in the eagle's own headless browser, correctly fell back to
`browser_control` — which opens a SEPARATE Chrome the user can see — and
thereby lost every structural tool it had. The page rendered perfectly. The
eagle was reduced to `screen_find`, which is blind because Chrome publishes
nothing to the accessibility bus, and to vision, which took 5.8 seconds and
landed 650px off. It could see the search bar and had no way to touch it.

`core/prompt.txt` already warns about this. Nothing made it avoidable.

The fix uses Playwright, which is already installed and already speaks CDP:
launch that window with a debug port and connect to it. Measured on a real
Chrome — 715 controls and the search box, straight out of the DOM, in the
window the user is looking at.

Worth stating plainly because a standalone Go daemon doing this was suggested:
the mechanism is identical, this is CDP either way, and doing it in the
library already present avoids a second runtime to install on three operating
systems — which is the opposite of the "works everywhere without setup" goal.
"""
from __future__ import annotations

from typing import Any

#: Fixed so `browser_control` and this module cannot drift apart. Bound to
#: localhost at launch: a debug port is unrestricted control of the browser,
#: including its cookies and every signed-in session in that profile.
DEBUG_PORT = 9222


def _connect(port: int) -> Any:
    """Attach to a Chrome already listening for CDP. Split out so tests never
    need a browser."""
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    return pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")


def attached_page(port: int | None = None):
    """The page in the user-facing Chrome, or None if there is not one.

    None means "no window to act on" — a different thing from "the control is
    not there", and the caller must not report it as the latter.
    """
    try:
        browser = _connect(port or DEBUG_PORT)
    except Exception:
        return None                     # nothing listening; not an error
    try:
        pages = [p for ctx in getattr(browser, "contexts", []) or []
                 for p in getattr(ctx, "pages", []) or []]
        if not pages:
            return None
        # The last one is the tab just opened for the thing being worked on.
        return pages[-1]
    except Exception:
        return None


def available(port: int | None = None) -> bool:
    return attached_page(port) is not None
