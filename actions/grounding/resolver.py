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


_atspi_cache: bool | None = None


def _atspi_probe() -> bool:
    """A cheap, real check — not a guess. Tries to enumerate the AT-SPI
    accessible registry root; a broken bus raises or returns nothing.

    Uses the same `gi.repository.Atspi` binding as the rest of this
    codebase (see `actions/grounding/atspi.py`), not the separate legacy
    `pyatspi` package — that package isn't installed here and checking it
    made this probe always fail closed, permanently disabling a working
    AT-SPI tier instead of only skipping it when the bus is actually dead.
    """
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        return Atspi.get_desktop(0).get_child_count() >= 0
    except Exception:
        return False


def atspi_available() -> bool:
    """Whether the AT-SPI tier can answer at all, checked once per process.

    Before this existed, every screen_click call spent its full 5s timeout
    (up to 85 internal polling attempts) against a bus that was never going
    to answer, every single time, in a session where it failed 4/4 calls.
    """
    global _atspi_cache
    if _atspi_cache is None:
        _atspi_cache = _atspi_probe()
    return _atspi_cache


class _GatedAtspiTier:
    """Wraps the real AT-SPI grounder so `default_resolver()` skips it fast
    when the bus can't answer, instead of paying for a live walk to find
    that out.

    Deliberately does NOT touch `GroundingResolver.find()`'s generic tier
    loop or `structural_grounder()`'s factory — both are exercised directly,
    with injected walkers, by unit tests that have nothing to do with
    whether the *real* live AT-SPI bus on *this* machine can answer, and
    gating those by name/class would make the probe (a live-environment
    fact) leak into tests that inject their own fake bus on purpose. Only
    the production singleton built by `default_resolver()` needs the fast
    skip, so only that wiring is gated.
    """

    def __init__(self, grounder) -> None:
        self._grounder = grounder
        self.name = grounder.name
        self.cost = getattr(grounder, "cost", "fast")

    def available(self) -> bool:
        return atspi_available() and self._grounder.available()

    def find(self, description: str):
        return self._grounder.find(description)


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
            # AT-SPI specifically can go dead while still importing cleanly
            # (GNOME's own toolkit-accessibility toggle, found live) and its
            # availability check is a real bus walk, not a cheap call — so
            # wrap only that tier with the cached process-wide probe. Other
            # platforms' structural grounders (UIA, macOS AX) are untouched.
            if getattr(structural, "name", None) == "atspi":
                structural = _GatedAtspiTier(structural)
            grounders.append(structural)
        grounders.append(VisionGrounder())
        _DEFAULT = GroundingResolver(grounders=grounders, cache=ElementCache())
    return _DEFAULT


def find_element(description: str) -> Element | None:
    return default_resolver().find(description)
