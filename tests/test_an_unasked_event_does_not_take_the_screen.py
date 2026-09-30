"""An event nobody asked for does not clear a card somebody did.

`_ambient_may_speak` decides whether a module has earned the right to say
anything at all. It was the only gate, and it answers a different question from
the one that matters here: it never looks at what is already on screen.

So `_drain_ambient_events` — which runs on a 2s QTimer — called
`set_pill_context` unconditionally. A `printer_standby` at priority 5 could
clear a card the user had asked for two seconds earlier, and the only trace was
the card being gone.

The module ranks its own events against each other. The host ranks them against
a person, because only the host knows there is one reading. That is the split
`AMBIENT_INTERRUPTS_A_CARD` encodes.

Nothing is dropped by any of this. An event that does not clear the bar is
still on the bus and still answerable when asked; it just does not take the
screen out from under a question.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

MANIFESTS = REPO / "tests" / "fixtures" / "module_bus" / "manifests"


class _Event:
    """What `AmbientWatcher.poll` hands the drain."""

    def __init__(self, module: str, event: str, priority: int) -> None:
        self.module = module
        self.event = event
        self.priority = priority
        self.payload: dict = {}


@pytest.fixture()
def ui():
    """A bare WebShellUI. `__init__` builds Qt windows; none of that is on the
    path being measured, which reads three attributes and a constant."""
    pytest.importorskip("PyQt6.QtWidgets")
    import aethelark_web

    shell = aethelark_web.WebShellUI.__new__(aethelark_web.WebShellUI)
    shell._active_pill_context = None
    shell._active_pill_is_ambient = False
    return shell


# ── the defect ───────────────────────────────────────────────────────────────

def test_a_standby_tick_does_not_clear_a_card_the_user_asked_for(ui):
    """The regression itself, with a3d's own lowest-priority event."""
    ui._active_pill_context = ("atrade", {"ticker": "NVDA"})
    ui._active_pill_is_ambient = False

    assert ui._may_take_the_screen(_Event("a3d", "printer_standby", 5)) is False


def test_a_progress_tick_does_not_either(ui):
    """The common case: a print running while the user asks about something
    else. It ticks every few seconds and would otherwise repaint every time."""
    ui._active_pill_context = ("atrade", {"ticker": "NVDA"})
    ui._active_pill_is_ambient = False

    assert ui._may_take_the_screen(_Event("a3d", "print_progress", 25)) is False


def test_a_printer_error_does_interrupt(ui):
    """The other half. A gate that never opens is not arbitration, it is a
    mute button — and a machine that has stopped is worth a sentence."""
    ui._active_pill_context = ("atrade", {"ticker": "NVDA"})
    ui._active_pill_is_ambient = False

    assert ui._may_take_the_screen(_Event("a3d", "printer_error", 100)) is True


# ── the cases that must not change ───────────────────────────────────────────

def test_an_empty_island_is_taken_by_anything(ui):
    """Nothing to interrupt. This is the whole of ambient's normal life."""
    ui._active_pill_context = None
    assert ui._may_take_the_screen(_Event("a3d", "printer_standby", 5)) is True


def test_an_ambient_card_is_replaced_by_the_next_ambient_event(ui):
    """poll() has already ordered these by the module's own priority, so a
    newer head is by definition the more important news. Guarding ambient from
    ambient would freeze the first event of a session onto the island."""
    ui._active_pill_context = ("a3d", {"printer": "CC1"})
    ui._active_pill_is_ambient = True

    assert ui._may_take_the_screen(_Event("a3d", "print_progress", 25)) is True


def test_a_collapsed_island_stops_being_protected(ui):
    """`note_island_collapsed` is what says the card is gone. If it cleared the
    context but left the flag, the next ambient event would be judged against a
    card that is no longer there — in the direction that blocks it."""
    import aethelark_web

    ui._pill_context_timer = type("T", (), {"stop": lambda self: None})()
    ui._active_pill_context = ("atrade", {"ticker": "NVDA"})
    ui._active_pill_is_ambient = False

    aethelark_web.WebShellUI.note_island_collapsed(ui)

    assert ui._active_pill_context is None
    assert ui._active_pill_is_ambient is False
    assert ui._may_take_the_screen(_Event("a3d", "printer_standby", 5)) is True


# ── the threshold has to mean something against the real manifests ───────────

def _declared_priorities() -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for path in sorted(MANIFESTS.glob("*.toml")):
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        prio = ((raw.get("events") or {}).get("priority") or {})
        if prio:
            out[path.stem] = {str(k): int(v) for k, v in prio.items()}
    return out


def test_the_threshold_splits_the_real_priorities_into_both_groups():
    """A property over the manifests, not a mirror of them.

    `AMBIENT_INTERRUPTS_A_CARD` is only meaningful if some declared events sit
    above it and some below. A manifest edit that pushed every priority to one
    side would turn the gate into either a mute button or a no-op, and nothing
    else would notice: both failures look like ordinary behaviour.
    """
    pytest.importorskip("PyQt6.QtWidgets")
    import aethelark_web

    bar = aethelark_web.WebShellUI.AMBIENT_INTERRUPTS_A_CARD
    declared = _declared_priorities()
    assert declared, "no module declares event priorities; the gate is untested"

    every = {f"{mod}.{name}": p
             for mod, prios in declared.items() for name, p in prios.items()}
    interrupts = {k: v for k, v in every.items() if v >= bar}
    waits = {k: v for k, v in every.items() if v < bar}

    assert interrupts, (
        f"no declared event reaches {bar}, so nothing can ever interrupt a "
        f"card — the gate is a mute button. Declared: {every}")
    assert waits, (
        f"every declared event reaches {bar}, so the gate never holds anything "
        f"back and an unasked-for tick still clears a card. Declared: {every}")


def test_a_print_finishing_waits_but_a_printer_stopping_does_not():
    """The line, stated in the vocabulary the operator actually uses.

    Read from the shipped manifest rather than restated here, so it fails if
    someone renumbers these rather than passing because this file agrees with
    itself.
    """
    pytest.importorskip("PyQt6.QtWidgets")
    import aethelark_web

    bar = aethelark_web.WebShellUI.AMBIENT_INTERRUPTS_A_CARD
    a3d = _declared_priorities().get("a3d")
    if not a3d:
        pytest.skip("a3d declares no event priorities")

    for name in ("printer_error", "printer_paused"):
        if name in a3d:
            assert a3d[name] >= bar, (
                f"{name} at {a3d[name]} no longer interrupts a card; a machine "
                f"that has stopped is worth a sentence")
    for name in ("print_progress", "printer_standby", "print_complete"):
        if name in a3d:
            assert a3d[name] < bar, (
                f"{name} at {a3d[name]} now clears a card the user asked for")
