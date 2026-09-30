"""Wait for an element to be genuinely ready, the way a person does.

A human doesn't click where a button is about to be. They wait for the dialog
to settle, notice if it's greyed out, and see when something else has opened on
top of it. This removes the single largest source of flakiness in GUI
automation.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from actions.grounding.actionability import check
from actions.grounding.base import Element


@dataclass(frozen=True)
class WaitResult:
    element: Element | None
    ok: bool
    failed_check: str
    elapsed_ms: float
    attempts: int


def wait_for(description: str,
             action: str = "click",
             *,
             resolver,
             timeout: float = 5.0,
             poll: float = 0.05,
             hit_test: Callable[[int, int], Element | None] | None = None,
             force: bool = False,
             clock: Callable[[], float] = time.monotonic,
             sleep: Callable[[float], None] = time.sleep) -> WaitResult:
    """Poll until `description` is present and actionable for `action`.

    Re-resolves every attempt — Playwright's lesson. A handle held across a
    redraw is a stale handle.
    """
    if hit_test is None and not force:
        # Without a real hit-test the "receives events" check can never pass,
        # so every click wait would time out. Found by running it live.
        from actions.grounding.atspi import hit_test_at
        hit_test = hit_test_at

    start = clock()
    previous: Element | None = None
    attempts = 0
    failed = "not_found"

    while True:
        attempts += 1
        try:
            # Poll the fast path only — vision costs seconds per attempt.
            try:
                element = resolver.find(description, fast_only=True)
            except TypeError:
                element = resolver.find(description)
        except Exception:
            element = None

        if element is not None:
            if force:
                return WaitResult(element, True, "",
                                  (clock() - start) * 1000, attempts)
            ok, failed_check = check(action, element,
                                     previous=previous, hit_test=hit_test)
            if ok:
                return WaitResult(element, True, "",
                                  (clock() - start) * 1000, attempts)
            failed = failed_check
        else:
            failed = "not_found"

        previous = element

        if clock() - start >= timeout:
            return WaitResult(element, False, failed,
                              (clock() - start) * 1000, attempts)
        sleep(poll)
