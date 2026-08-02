"""Structural grounding on macOS, via the Accessibility API.

Same idea as the AT-SPI and UI Automation grounders: read what the
application says its interface *is*.

macOS differs from the other two in one important way: reading another
application's accessibility tree requires the user to grant Accessibility
permission in System Settings. That is a consent screen, not an install
step — `permission_status()` reports it so the eagle can explain rather than
silently returning nothing.

⚠️ The `live_walker` and `hit_test_at` bindings below have NOT been executed
on macOS — this repository's development machine is Linux. The matching
logic is fully tested through the injectable `walker` seam; the PyObjC
binding is written from the documented AX API and is unverified. Treat the
first run on a real Mac as the actual test.
"""
from __future__ import annotations

from typing import Callable, Iterable, Iterator

from actions.grounding.base import Element, UINode
from actions.grounding.roles import MACOS, best_match

_MAX_NODES = 4000
_MAX_DEPTH = 40

_PERMISSION_HINT = (
    "macOS needs Accessibility permission before the eagle can read what is "
    "on screen. Open System Settings > Privacy & Security > Accessibility "
    "and enable it for this application, then try again. Until then, "
    "grounding falls back to slower vision lookups."
)


def _backend():
    """(ApplicationServices, AppKit) or (None, None). Never raises."""
    try:
        import AppKit
        import ApplicationServices
        return ApplicationServices, AppKit
    except Exception:
        return None, None


def permission_status() -> dict:
    """Has the user granted Accessibility permission?

    Read-only and non-prompting — asking the OS to show its permission dialog
    is the user's decision to trigger, not ours.
    """
    services, _ = _backend()
    if services is None:
        return {"available": False, "trusted": False,
                "hint": "PyObjC is not installed; run: pip install "
                        "pyobjc-framework-ApplicationServices pyobjc-framework-Cocoa"}
    try:
        trusted = bool(services.AXIsProcessTrusted())
    except Exception:
        trusted = False
    return {"available": True, "trusted": trusted,
            "hint": "" if trusted else _PERMISSION_HINT}


def _attr(services, element, name):
    """One AX attribute, or None. The API returns (error, value) pairs."""
    try:
        err, value = services.AXUIElementCopyAttributeValue(element, name, None)
        return value if err == 0 else None
    except Exception:
        return None


def _states(services, element) -> frozenset:
    found = set()
    enabled = _attr(services, element, "AXEnabled")
    if enabled:
        found.update(("ENABLED", "SENSITIVE"))
    # AX has no direct offscreen flag; a control with real geometry that the
    # system returned is on screen. Bounds sanity is checked in roles.py.
    found.update(("VISIBLE", "SHOWING"))
    if _attr(services, element, "AXFocused"):
        found.add("FOCUSED")
    role = str(_attr(services, element, "AXRole") or "").lower()
    if role in ("axtextfield", "axtextarea", "axsecuretextfield") and enabled:
        found.add("EDITABLE")
    value = _attr(services, element, "AXValue")
    if role in ("axcheckbox", "axradiobutton") and value:
        found.add("CHECKED")
    if _attr(services, element, "AXSelected"):
        found.add("SELECTED")
    return frozenset(found)


def _bounds(services, element) -> tuple[int, int, int, int]:
    try:
        position = _attr(services, element, "AXPosition")
        size = _attr(services, element, "AXSize")
        if position is None or size is None:
            return 0, 0, 0, 0
        import ApplicationServices as _svc
        ok_p, point = _svc.AXValueGetValue(position, _svc.kAXValueCGPointType, None)
        ok_s, extent = _svc.AXValueGetValue(size, _svc.kAXValueCGSizeType, None)
        if not (ok_p and ok_s):
            return 0, 0, 0, 0
        return int(point.x), int(point.y), int(extent.width), int(extent.height)
    except Exception:
        return 0, 0, 0, 0


def _frontmost_pid(appkit) -> int | None:
    try:
        app = appkit.NSWorkspace.sharedWorkspace().frontmostApplication()
        return int(app.processIdentifier()) if app is not None else None
    except Exception:
        return None


def live_walker(scope: str = "active") -> Iterator[UINode]:
    """Walk the frontmost application's accessibility tree."""
    services, appkit = _backend()
    if services is None:
        return
    if not permission_status().get("trusted"):
        return

    try:
        if scope == "active":
            pid = _frontmost_pid(appkit)
            if pid is None:
                return
            root = services.AXUIElementCreateApplication(pid)
        else:
            root = services.AXUIElementCreateSystemWide()
    except Exception:
        return
    if root is None:
        return

    count = 0
    stack = [(root, 0)]
    while stack and count < _MAX_NODES:
        element, depth = stack.pop()
        if depth > _MAX_DEPTH:
            continue
        children = _attr(services, element, "AXChildren") or []
        for child in children:
            try:
                stack.append((child, depth + 1))
                left, top, width, height = _bounds(services, child)
                name = (_attr(services, child, "AXTitle")
                        or _attr(services, child, "AXDescription") or "")
                raw_value = _attr(services, child, "AXValue")
                yield UINode(
                    name=str(name),
                    role=str(_attr(services, child, "AXRole") or ""),
                    left=left, top=top, width=width, height=height,
                    states=_states(services, child),
                    value=str(raw_value) if isinstance(raw_value, str) else "",
                )
                count += 1
            except Exception:
                continue


def hit_test_at(x: int, y: int) -> Element | None:
    """What control is actually at this screen point?"""
    services, _ = _backend()
    if services is None or not permission_status().get("trusted"):
        return None
    try:
        system = services.AXUIElementCreateSystemWide()
        err, element = services.AXUIElementCopyElementAtPosition(
            system, float(x), float(y), None)
        if err != 0 or element is None:
            return None
        left, top, width, height = _bounds(services, element)
        name = (_attr(services, element, "AXTitle")
                or _attr(services, element, "AXDescription") or "")
        return Element.from_bounds(
            str(name), str(_attr(services, element, "AXRole") or ""),
            left, top, width, height, "ax")
    except Exception:
        return None


class MacGrounder:
    """Locate an element by matching the macOS accessibility tree."""

    name = "ax"
    cost = "fast"      # local API calls, no network

    def __init__(self,
                 walker: Callable[[], Iterable[UINode]] | None = None,
                 threshold: float = 0.5) -> None:
        self._walker = walker or live_walker
        self._threshold = threshold

    def available(self) -> bool:
        try:
            for _ in self._walker():
                return True
        except Exception:
            return False
        return False

    def find(self, description: str) -> Element | None:
        try:
            node = best_match(self._walker(), description,
                              self._threshold, MACOS)
        except Exception:
            return None
        if node is None:
            return None
        return Element.from_bounds(node.name, node.role, node.left, node.top,
                                   node.width, node.height, "ax",
                                   states=node.states, value=node.value)
