from __future__ import annotations

from typing import Callable

from actions.grounding.base import Element, Grounder
from actions.grounding.cache import ElementCache


def _default_context() -> str:
    """Identify the active window by id so cache entries do not leak across apps.
    Titles are unstable (spinners, modified dots).
    """
    try:
        import subprocess
        out = subprocess.run(["xdotool", "getactivewindow"],
                             capture_output=True, text=True, timeout=2)
        return (out.stdout or "").strip() or "unknown"
    except Exception:
        return "unknown"


class GroundingResolver:
    """Try each way of locating an element, most exact first: remembered, perceived,
    then guessed from a picture.
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
        """Locate `description`. fast_only skips grounders that cost a network round
        trip; polling loops must use it.
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
    """The structural grounder for this OS (AT-SPI, UI Automation, or the macOS
    Accessibility API), or None.
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
    """A real check that the AT-SPI bus can answer: bootstrap the gi bindings first
    (as every other AT-SPI entry point does), check GNOME's toolkit-accessibility
    toggle, then enumerate the registry root. Uses gi.repository.Atspi, not
    pyatspi.
    """
    try:
        from actions.grounding.atspi import _ensure_bindings_once, atspi_enabled
        _ensure_bindings_once()
        if not atspi_enabled():
            return False
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        return Atspi.get_desktop(0).get_child_count() >= 0
    except Exception:
        return False


def atspi_available() -> bool:
    """Whether the AT-SPI tier can answer at all, checked once per process, so a dead
    bus does not cost every screen_click its full timeout.
    """
    global _atspi_cache
    if _atspi_cache is None:
        _atspi_cache = _atspi_probe()
    return _atspi_cache


class _GatedAtspiTier:
    """Wraps the real AT-SPI grounder so default_resolver() skips it fast when the
    bus cannot answer. Only the production singleton is gated, so tests that
    inject a fake bus are unaffected.
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
    """The hit-test function for this OS, or None. Without one, receives_events never
    passes and every click wait times out.
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
            # AT-SPI can go dead while importing cleanly, so only that tier is wrapped
            # in the cached probe.
            if getattr(structural, "name", None) == "atspi":
                structural = _GatedAtspiTier(structural)
            grounders.append(structural)
        grounders.append(VisionGrounder())
        _DEFAULT = GroundingResolver(grounders=grounders, cache=ElementCache())
    return _DEFAULT


def find_element(description: str) -> Element | None:
    return default_resolver().find(description)
