"""Whether keyboard focus is known — moved out of core/mission_runners.py
so both the mission ladder and computer_control's own direct actions
share the exact same check, rather than one having it and the other not.

A mission typed "motherboard" into the user's terminal while Claude Code
was running in it, and reported success. The same defect is documented at
the top of actions/whatsapp_web.py from months earlier. computer_control's
own type/type_text/smart_type actions never got this guard — a live
babysitting session (2026-08-13/14) hit exactly the same bug through them.

`focused_editable_name()` used to call `atspi_available()` unconditionally,
with no platform check. `atspi_available()` is Linux-specific — on
Windows/macOS `import gi` always fails, so it always returns False, so
`focused_editable_name()` always returned None, so the typing guard above
refused EVERY type on those platforms — a real regression on platforms
that previously typed fine. This dispatches by platform the same way
`actions/grounding/resolver.py::structural_grounder()` does, and reuses
each platform's own `live_walker(scope="active")` (already built for the
Windows UIA and macOS AX grounders, same node shape — `.states`/`.name` —
as AT-SPI's) rather than inventing a new focus-detection mechanism.
"""
from __future__ import annotations

import sys


def _scan_for_focused(nodes) -> str | None:
    """The name of the first FOCUSED+EDITABLE node in `nodes`, or None."""
    for node in nodes:
        states = getattr(node, "states", frozenset())
        if "FOCUSED" in states and "EDITABLE" in states:
            return str(getattr(node, "name", "") or "a text field")
    return None


def _focused_editable_name_linux() -> str | None:
    from actions.grounding.resolver import atspi_available
    if not atspi_available():
        # Fails CLOSED: an unavailable bus means "I don't know", not
        # "nothing has focus" — and definitely not "go ahead and type".
        return None
    from actions.grounding.atspi import live_walker
    return _scan_for_focused(live_walker(scope="active"))


def _focused_editable_name_windows() -> str | None:
    from actions.grounding.windows import live_walker
    return _scan_for_focused(live_walker(scope="active"))


def _focused_editable_name_macos() -> str | None:
    from actions.grounding.macos import live_walker
    return _scan_for_focused(live_walker(scope="active"))


def focused_editable_name(platform: str | None = None) -> str | None:
    """The name of the text field that currently has keyboard focus, or
    None. None means "I do not know where typing would land" — the only
    honest answer when the accessibility layer cannot see a focused
    editable control. It is NOT the same as "there is no field", and must
    never be read as permission.

    Walks the live structural tree directly rather than going through
    `structural_grounder()`'s grounder classes — those only expose
    `available()` and `find(description)`, no way to enumerate nodes. Each
    platform's own `live_walker(scope="active")` is the thing that
    actually yields nodes with `.states`/`.name`.

    `platform` overrides `sys.platform`, matching `structural_grounder()`'s
    own signature — used by tests, not by production callers.
    """
    plat = platform or sys.platform
    try:
        if plat.startswith("win"):
            return _focused_editable_name_windows()
        if plat == "darwin":
            return _focused_editable_name_macos()
        return _focused_editable_name_linux()
    except Exception:
        return None
