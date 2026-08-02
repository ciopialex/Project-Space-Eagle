"""Structural grounding on Windows, via UI Automation.

The same idea as the AT-SPI grounder: read what the application says its
interface *is*, rather than screenshotting it and asking a model to guess.

Scoped to the foreground window for the same two reasons it is on Linux —
it is far cheaper than walking every window, and background windows report
stale or off-screen geometry that looks real but isn't.

⚠️ The `live_walker` and `hit_test_at` bindings below have NOT been executed
on Windows — this repository's development machine is Linux. The matching
logic is fully tested through the injectable `walker` seam; the COM binding
is written from the documented UIAutomation API and is unverified. Treat the
first run on a real Windows machine as the actual test.
"""
from __future__ import annotations

from typing import Callable, Iterable, Iterator

from actions.grounding.base import Element, UINode
from actions.grounding.roles import WINDOWS, best_match

_MAX_NODES = 4000
_MAX_DEPTH = 40


def _backend():
    """The uiautomation module, or None. Never raises."""
    try:
        import uiautomation
        return uiautomation
    except Exception:
        return None


def _states(control) -> frozenset:
    """UIA properties mapped onto the shared state vocabulary.

    UIA has no separate ENABLED/SENSITIVE split, so both are set together —
    `is_enabled` in actionability.py requires both.
    """
    found = set()
    try:
        if control.IsEnabled:
            found.update(("ENABLED", "SENSITIVE"))
    except Exception:
        pass
    try:
        if not control.IsOffscreen:
            found.update(("VISIBLE", "SHOWING"))
    except Exception:
        pass
    try:
        if control.HasKeyboardFocus:
            found.add("FOCUSED")
    except Exception:
        pass
    try:
        pattern = control.GetValuePattern()
        if pattern is not None and not pattern.IsReadOnly:
            found.add("EDITABLE")
    except Exception:
        pass
    try:
        toggle = control.GetTogglePattern()
        if toggle is not None and toggle.ToggleState == 1:
            found.add("CHECKED")
    except Exception:
        pass
    try:
        selection = control.GetSelectionItemPattern()
        if selection is not None and selection.IsSelected:
            found.add("SELECTED")
    except Exception:
        pass
    return frozenset(found)


def _value(control) -> str:
    try:
        pattern = control.GetValuePattern()
        if pattern is not None:
            return pattern.Value or ""
    except Exception:
        pass
    return ""


def _role(control) -> str:
    """UIA reports 'ButtonControl'; the roles table keys on 'Button'."""
    try:
        raw = control.ControlTypeName or ""
    except Exception:
        return ""
    return raw[:-7] if raw.endswith("Control") else raw


def live_walker(scope: str = "active") -> Iterator[UINode]:
    """Walk the real UI Automation tree. Yields nothing on failure."""
    auto = _backend()
    if auto is None:
        return

    try:
        if scope == "active":
            root = auto.GetForegroundControl()
        else:
            root = auto.GetRootControl()
    except Exception:
        return
    if root is None:
        return

    count = 0
    stack = [(root, 0)]
    while stack and count < _MAX_NODES:
        node, depth = stack.pop()
        if depth > _MAX_DEPTH:
            continue
        try:
            children = node.GetChildren()
        except Exception:
            continue
        for child in children:
            try:
                stack.append((child, depth + 1))
                rect = child.BoundingRectangle
                yield UINode(
                    name=child.Name or "",
                    role=_role(child),
                    left=int(rect.left), top=int(rect.top),
                    width=int(rect.right - rect.left),
                    height=int(rect.bottom - rect.top),
                    states=_states(child), value=_value(child),
                )
                count += 1
            except Exception:
                continue


def hit_test_at(x: int, y: int) -> Element | None:
    """What control is actually at this screen point?

    Playwright's "receives events" check needs this; without it every click
    wait times out.
    """
    auto = _backend()
    if auto is None:
        return None
    try:
        control = auto.ControlFromPoint(int(x), int(y))
        if control is None:
            return None
        rect = control.BoundingRectangle
        return Element.from_bounds(
            control.Name or "", _role(control),
            int(rect.left), int(rect.top),
            int(rect.right - rect.left), int(rect.bottom - rect.top),
            "uia")
    except Exception:
        return None


def scroll_to_element(description: str, threshold: float = 0.5) -> bool:
    """Ask the application to bring a control on screen."""
    auto = _backend()
    if auto is None:
        return False
    try:
        controls = []
        for node in live_walker():
            controls.append(node)
        target = best_match(controls, description, threshold, WINDOWS)
        if target is None:
            return False
        # Re-resolve the live control at the matched centre, then scroll it.
        control = auto.ControlFromPoint(
            target.left + target.width // 2, target.top + target.height // 2)
        if control is None:
            return False
        pattern = control.GetScrollItemPattern()
        if pattern is None:
            return False
        pattern.ScrollIntoView()
        return True
    except Exception:
        return False


class WindowsGrounder:
    """Locate an element by matching the UI Automation tree."""

    name = "uia"
    cost = "fast"      # local COM calls, no network

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
                              self._threshold, WINDOWS)
        except Exception:
            return None
        if node is None:
            return None
        return Element.from_bounds(node.name, node.role, node.left, node.top,
                                   node.width, node.height, "uia",
                                   states=node.states, value=node.value)
