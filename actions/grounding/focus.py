"""Whether keyboard focus is known — moved out of core/mission_runners.py
so both the mission ladder and computer_control's own direct actions
share the exact same check, rather than one having it and the other not.

A mission typed "motherboard" into the user's terminal while Claude Code
was running in it, and reported success. The same defect is documented at
the top of actions/whatsapp_web.py from months earlier. computer_control's
own type/type_text/smart_type actions never got this guard — a live
babysitting session (2026-08-13/14) hit exactly the same bug through them.
"""
from __future__ import annotations


def focused_editable_name() -> str | None:
    """The name of the text field that currently has keyboard focus, or
    None. None means "I do not know where typing would land" — the only
    honest answer when the accessibility layer cannot see a focused
    editable control. It is NOT the same as "there is no field", and must
    never be read as permission.

    Walks the live AT-SPI tree directly rather than going through
    `structural_grounder()`/`AtspiGrounder` — that class only exposes
    `available()` and `find(description)`, no way to enumerate nodes, so
    there is no `.nodes()` to call there. `actions/grounding/atspi.py`'s
    own `live_walker(scope="active")` is the thing that actually yields
    nodes with `.states`/`.name`, and it is what `AtspiGrounder.find()`
    itself walks internally.
    """
    try:
        from actions.grounding.resolver import atspi_available
        if not atspi_available():
            # Fails CLOSED: an unavailable bus means "I don't know", not
            # "nothing has focus" — and definitely not "go ahead and type".
            return None

        from actions.grounding.atspi import live_walker
        for node in live_walker(scope="active"):
            states = getattr(node, "states", frozenset())
            if "FOCUSED" in states and "EDITABLE" in states:
                return str(getattr(node, "name", "") or "a text field")
    except Exception:
        return None
    return None
