from __future__ import annotations

from typing import Callable

from actions.grounding.base import Element, Grounder
from actions.grounding.cache import ElementCache


def _default_context() -> str:
    """Identify the active window so cache entries don't leak across apps.

    Uses the window *id*, not its title. Titles are not stable — terminals put
    spinners in them, editors add a modified dot, browsers show the tab count —
    so a title-keyed cache misses on every single lookup. Measured: keying on
    the title gave a 0% hit rate against a live terminal.
    """
    try:
        import subprocess
        out = subprocess.run(["xdotool", "getactivewindow"],
                             capture_output=True, text=True, timeout=2)
        return (out.stdout or "").strip() or "unknown"
    except Exception:
        return "unknown"


class GroundingResolver:
    """Try each way of locating an element, cheapest and most exact first.

    Order matters and is the whole point: remembered, then perceived, then
    guessed at from a picture.
    """

    def __init__(self,
                 grounders: list[Grounder],
                 cache: ElementCache | None = None,
                 context_fn: Callable[[], str] | None = None) -> None:
        self._grounders = grounders
        self._cache = cache
        self._context_fn = context_fn or _default_context
        self.last_source: str | None = None

    def _context(self) -> str:
        try:
            return self._context_fn()
        except Exception:
            return "unknown"

    def find(self, description: str, fast_only: bool = False) -> Element | None:
        """Locate `description`.

        fast_only skips grounders that cost a network round-trip. Polling loops
        must use it: a person waiting for a dialog does not re-photograph the
        screen fifty times. Without this, a single `wait_for` on a missing
        element took 3.9 seconds per attempt and blew straight past its timeout.
        """
        context = self._context()

        if self._cache is not None:
            hit = self._cache.get(context, description)
            if hit is not None:
                self.last_source = "cache"
                return hit

        for grounder in self._grounders:
            try:
                if fast_only and getattr(grounder, "cost", "slow") != "fast":
                    continue
                if not grounder.available():
                    continue
                found = grounder.find(description)
            except Exception:
                continue
            if found is not None:
                if self._cache is not None:
                    self._cache.put(context, description, found)
                self.last_source = found.source
                return found

        self.last_source = None
        return None


def structural_grounder(platform: str | None = None):
    """The structural grounder for this operating system, or None.

    Each platform publishes its interface through a different API — AT-SPI on
    Linux, UI Automation on Windows, the Accessibility API on macOS. All three
    answer the same question, so the resolver above never needs to know which
    one it is talking to.
    """
    import sys
    plat = platform or sys.platform
    try:
        if plat.startswith("win"):
            from actions.grounding.windows import WindowsGrounder
            return WindowsGrounder()
        if plat == "darwin":
            from actions.grounding.macos import MacGrounder
            return MacGrounder()
        from actions.grounding.atspi import AtspiGrounder
        return AtspiGrounder()
    except Exception:
        return None


def platform_hit_test(platform: str | None = None):
    """The hit-test function for this OS, or None.

    Without one the "receives events" check can never pass and every click
    wait times out — a bug this codebase has already shipped once.
    """
    import sys
    plat = platform or sys.platform
    try:
        if plat.startswith("win"):
            from actions.grounding.windows import hit_test_at
            return hit_test_at
        if plat == "darwin":
            from actions.grounding.macos import hit_test_at
            return hit_test_at
        from actions.grounding.atspi import hit_test_at
        return hit_test_at
    except Exception:
        return None


_DEFAULT: GroundingResolver | None = None


def default_resolver() -> GroundingResolver:
    global _DEFAULT
    if _DEFAULT is None:
        from actions.grounding.vision import VisionGrounder
        grounders = []
        structural = structural_grounder()
        if structural is not None:
            grounders.append(structural)
        grounders.append(VisionGrounder())
        _DEFAULT = GroundingResolver(grounders=grounders, cache=ElementCache())
    return _DEFAULT


def find_element(description: str) -> Element | None:
    return default_resolver().find(description)
