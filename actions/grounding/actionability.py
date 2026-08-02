"""Is this element actually ready to be acted on?

Transplanted from Playwright's actionability model, which encodes a decade of
GUI-automation flakiness lessons. Every check has an exact AT-SPI equivalent:

    Visible          STATE_VISIBLE + STATE_SHOWING + non-zero bounds
    Stable           identical bounds across two consecutive reads
    Receives Events  get_accessible_at_point(x, y) returns this node
    Enabled          STATE_ENABLED + STATE_SENSITIVE
    Editable         STATE_EDITABLE

This is the difference between an agent that fires a click into the void and
one that waits like a person would.
"""
from __future__ import annotations

from typing import Callable

from actions.grounding.base import Element

# Playwright's per-action requirement matrix, transplanted.
# https://playwright.dev/docs/actionability
ACTION_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "click":            ("visible", "stable", "receives_events", "enabled"),
    "dblclick":         ("visible", "stable", "receives_events", "enabled"),
    "right_click":      ("visible", "stable", "receives_events", "enabled"),
    "check":            ("visible", "stable", "receives_events", "enabled"),
    "hover":            ("visible", "stable", "receives_events"),
    "drag":             ("visible", "stable", "receives_events"),
    "screenshot":       ("visible", "stable"),
    "fill":             ("visible", "enabled", "editable"),
    "clear":            ("visible", "enabled", "editable"),
    "select":           ("visible", "enabled"),
    "scroll_into_view": ("stable",),
    "focus":            (),
    "press":            (),
}


def is_visible(el: Element) -> bool:
    return (el.width > 0 and el.height > 0
            and el.has("VISIBLE") and el.has("SHOWING"))


def is_enabled(el: Element) -> bool:
    return el.has("ENABLED") and el.has("SENSITIVE")


def is_editable(el: Element) -> bool:
    return is_enabled(el) and el.has("EDITABLE")


def is_stable(before: Element | None, after: Element | None) -> bool:
    """Two consecutive reads with identical bounds — the animation has settled."""
    if before is None or after is None:
        return False
    return before.bounds == after.bounds


def _identity(el: Element) -> tuple:
    return (el.name, el.role, el.bounds)


def receives_events(el: Element,
                    hit_test: Callable[[int, int], Element | None]) -> bool:
    """Is this element what you'd actually hit at its own centre?

    Catches the modal that opened over the button you were about to click.
    """
    try:
        hit = hit_test(el.x, el.y)
    except Exception:
        return False
    return hit is not None and _identity(hit) == _identity(el)


def check(action: str, el: Element, *,
          previous: Element | None = None,
          hit_test: Callable[[int, int], Element | None] | None = None,
          ) -> tuple[bool, str]:
    """Run the checks this action requires. Returns (ok, first_failed_name)."""
    for name in ACTION_REQUIREMENTS.get(action, ()):
        if name == "visible" and not is_visible(el):
            return False, "visible"
        if name == "enabled" and not is_enabled(el):
            return False, "enabled"
        if name == "editable" and not is_editable(el):
            return False, "editable"
        if name == "stable" and not is_stable(previous, el):
            return False, "stable"
        if name == "receives_events":
            if hit_test is None or not receives_events(el, hit_test):
                return False, "receives_events"
    return True, ""
