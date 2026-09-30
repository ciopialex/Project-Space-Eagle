"""Adding the modules a user chose in setup, while they finish setting up.

The choice is made first, before the key screen, because getting a key takes a
minute or more and that is when the downloads can happen unseen.
"""
from __future__ import annotations

from typing import Callable


def install_chosen(names: list[str], *, installed: set[str],
                   install: Callable[[str, Callable[[str], None]], object],
                   report: Callable[[str, str, str], None]) -> None:
    """Add each module in turn. `report(name, state, step)` hears every change:
    state is "installing", "ready" or "failed". One failure is reported and does
    not stop the rest."""
    for name in names:
        if name in installed:
            report(name, "ready", "")
            continue
        report(name, "installing", "Starting…")
        try:
            install(name, lambda step, n=name: report(n, "installing", step))
        except Exception as e:
            first = (str(e).strip().splitlines() or ["It did not install."])[0]
            report(name, "failed", first[:160])
        else:
            report(name, "ready", "")
