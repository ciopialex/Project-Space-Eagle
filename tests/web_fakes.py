"""A page, without a browser.

Everything above the seam is tested against this. If a test in this area needs
a real browser to run, either the test is wrong or the seam has leaked.
"""
from __future__ import annotations

import asyncio
import time


class EventCtx:
    """Playwright's `expect_download`/`expect_popup` context manager, with
    Playwright's ACTUAL control flow.

    THE ONE double for this in the whole suite. There used to be four
    (`_FakeDownloadCtx`, `_NoOpCtx`, `_NoFire`, `_NeverFires`, spread across
    test_click_outcome.py, test_grounder_tie_retry.py,
    test_click_escalation_integration.py and here), and all four diverged
    from the real thing in the same two ways — both of which hid a real bug
    behind a green suite:

    1. They returned instantly from `__exit__` and put the timeout in
       `.value`. Verified against playwright 1.61
       (`_impl/_sync_base.py`): `EventContextManager.__exit__` with no
       active exception reads `self._event.value`, and `EventInfo.value`
       BLOCKS until the future resolves, then raises the future's
       `TimeoutError` straight out of `__exit__`. The wait is spent in
       `__exit__`, which is why nesting two of these around one click costs
       two full timeouts per candidate — invisible to a suite whose doubles
       cost nothing (finding I3).
    2. None of them cancelled on `__exit__(exc, ...)`. Real Playwright calls
       `self._event._cancel()` when an exception is active in the block, and
       a later `.value` on a cancelled future raises
       `asyncio.CancelledError` — a `BaseException`. That is the entire
       mechanism of finding C2 (a raising click killing the browser thread
       for the rest of the process), and no test could reproduce it because
       no double did this.

    `fires=True` makes the event arrive; `wait_s` is how long a non-firing
    expectation blocks in `__exit__`, for tests that need the wall clock to
    be real.
    """

    def __init__(self, fires: bool = False, value=None, wait_s: float = 0.0):
        self._fires = fires
        self._value = value if value is not None else object()
        self._wait_s = wait_s
        self.cancelled = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type=None, exc=None, tb=None):
        if exc is not None or exc_type is not None:
            # `EventContextManager.__exit__`: `if exc_val: self._event._cancel()`
            self.cancelled = True
            return False
        if self._fires:
            return False
        if self._wait_s:
            time.sleep(self._wait_s)
        # `EventInfo.value` re-raises the future's own exception, and it does
        # so from inside `__exit__`.
        raise TimeoutError("expectation timed out")

    @property
    def value(self):
        if self.cancelled:
            # `asyncio.Future.exception()` on a cancelled future raises
            # this. It is a BaseException, not an Exception.
            raise asyncio.CancelledError()
        if not self._fires:
            raise TimeoutError("expectation timed out")
        return self._value


class FakePage:
    """Implements the whole of `PageLike` (page.py), including the parts
    `click_with_outcome`/`resolve_and_click` (outcome.py, grounder.py)
    require: `dismiss_overlay`/`centre` (best-effort, no-ops here — there is
    no real DOM to scroll or clear a backdrop on), `expect_download`/
    `expect_popup` (never fire — this fake's `click` has no real browser
    behind it to start either), `mouse_click` (the vision fallback's only
    lever) and `dom_signature`.

    `dom_signature` is a plain counter a subclass bumps to say "that click
    changed the page" — the DOM half of `"url_or_dom_changed"`, without a
    DOM. Every non-navigational click depends on it, so a fake without one
    can only ever exercise the navigational half.
    """

    def __init__(self, records=(), shot=b"PNG", url="https://example.test/"):
        self._records = list(records)
        self._shot = shot
        self._url = url
        self.shots_taken = 0
        self.collects = 0
        self.clicked: list[str] = []
        self.filled: list[tuple[str, str]] = []
        self.dismissed = 0
        self.centred: list[str] = []
        self.mouse_clicked: list[tuple[int, int]] = []
        #: Bump this from a `click()` override to model a click that changed
        #: something without navigating.
        self.dom_state = 0

    def collect(self):
        self.collects += 1
        return list(self._records)

    def hit_test(self, x, y):
        return None

    def screenshot(self):
        self.shots_taken += 1
        return self._shot

    def click(self, ref):
        self.clicked.append(ref)

    def mouse_click(self, x, y):
        """The vision fallback clicks a coordinate, not a ref. Without this
        no `web_agency` test could reach that path through this fake at all
        — `resolve_and_click` raised `AttributeError` inside
        `click_with_outcome`, which swallows it, so the branch read as "the
        click did nothing" instead of running."""
        self.mouse_clicked.append((int(x), int(y)))

    def fill(self, ref, text):
        self.filled.append((ref, text))

    def url(self):
        return self._url

    def dom_signature(self):
        return f"dom-{self.dom_state}"

    def dismiss_overlay(self):
        self.dismissed += 1
        return False

    def centre(self, ref):
        self.centred.append(ref)

    def expect_download(self, timeout=None):
        return EventCtx()

    def expect_popup(self, timeout=None):
        return EventCtx()


LIVE = ["ENABLED", "SENSITIVE", "VISIBLE", "SHOWING"]
TYPABLE = LIVE + ["EDITABLE"]


def record(ref="e0", name="Sign in", role="button", top=0, states=None,
           **over):
    """One collector record, with sane defaults."""
    rec = {"ref": ref, "name": name, "role": role,
           "left": 0, "top": top, "width": 90, "height": 24,
           "states": list(states if states is not None else LIVE),
           "value": ""}
    rec.update(over)
    return rec


def records(n):
    """`n` distinct, ordinary controls."""
    return [record(ref=f"e{i}", name=f"Control {i}", top=i * 20)
            for i in range(n)]
