from __future__ import annotations

import re
import time
from dataclasses import replace
from typing import Callable

from actions.grounding.base import Element


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


class ElementCache:
    """Remembers where a thing was, scoped to the window it was found in.

    A human doesn't re-hunt for the Save button every time they look at the
    same dialog. Entries are keyed on (window context, description) and expire,
    because interfaces move.
    """

    def __init__(self, ttl: float = 30.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl
        self._clock = clock
        self._store: dict[tuple[str, str], tuple[Element, float]] = {}

    def get(self, context: str, description: str) -> Element | None:
        key = (context, _norm(description))
        hit = self._store.get(key)
        if hit is None:
            return None
        element, stamp = hit
        if self._clock() - stamp >= self._ttl:
            del self._store[key]
            return None
        return replace(element, source="cache")

    def put(self, context: str, description: str, element: Element) -> None:
        self._store[(context, _norm(description))] = (element, self._clock())

    def invalidate(self, context: str | None = None) -> None:
        if context is None:
            self._store.clear()
            return
        for key in [k for k in self._store if k[0] == context]:
            del self._store[key]
