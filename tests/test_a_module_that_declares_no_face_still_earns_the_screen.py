"""a3d ships a card and declares no face, so nothing could refuse its answers.

`_card_is_drawable` asks whether an answer carries a drawn field beyond the
identity the question already supplied. It answers from the manifest — `[island]
about` and each card's `shows` — and a module that declares no `[island]`
section returns None from `island_face`, which every caller is required to read
as "carry on as before".

a3d is that module. 21 tools, an island template with 30 placeholders, and no
`[island]` section at all. So "carry on as before" meant every a3d answer went
to the screen, whatever was in it.

Measured 2026-09-09 against the real binary — each payload put through the real
CardStore and rendered in the real pill.html, reading innerText back out:

    a3d status   fills  3 of 30   "CC1 Elegoo Centauri Carbon 1 (No AMS) •
                                   OFFLINE ... Nozzle Temp 0°C Bed Temp 0°C"
    a3d browse   fills  0 of 30   "— • — — Layer — • ETA — ACTIVE JOB — —
                                   Nozzle Temp — Bed Temp — Chamber Temp — ..."

vault, doctor, quote and rates are the same shape as browse: five of the nine
read-only tools put a card of pure em dashes on screen. printers, shelf and
search answer with a top-level JSON array, which the unpacking in main.py turns
into {} and the existing guard already refuses.

`browse` is the one that matters, because it is what runs when someone asks the
eagle to find them a model.

The fix asks the same question from the only declaration a faceless module
makes — its template. `MODULE_BUS.island_fields` reads the placeholders out of
the installed island (the vendored copy is the fallback, matching the rule
everywhere else), so the host still learns no module's name. That property is
why `island_face` exists in the bus rather than as a table in main.py, and it
is preserved here.

The payloads below are real shapes captured from the binaries, trimmed to the
keys that decide the question. They are not called live: `browse` and `search`
reach MakerWorld over the network, and `status` reaches a printer that may be
powered off. What IS read live is the template, because that is the artifact
the decision is made against and it is on disk.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

os.environ.setdefault("AETHELARK_SKIP_HEAVY_IMPORTS", "1")

import main  # noqa: E402
from core.card_assembly import CardStore  # noqa: E402


def _drawable(tool: str, payload: dict, args: dict | None = None) -> bool:
    """Through the real CardStore and the real gate, as main.py does it."""
    args = args or {}
    merged = dict(payload)
    for k, v in args.items():
        merged.setdefault(k, v)
    card = CardStore().absorb(tool, merged, args) if merged else {}
    return main.AethelarkLive._card_is_drawable(tool, card, args)


# ── real shapes, trimmed to what decides it ─────────────────────────────────

STATUS = {"printer": "CC1", "name": "Elegoo Centauri Carbon 1 (No AMS)",
          "state": "OFFLINE", "nozzle": 0, "bed": 0}

BROWSE = {"query": "spiderman", "printer": None, "failed": [],
          "fleet": [{"key": "CC1", "name": "Elegoo Centauri Carbon 1 (No AMS)"}],
          "candidates": [{"success": True, "design_id": 2325443,
                          "title": "Urban Spiderman", "creator": "someone",
                          "dimensions": {"x": 100, "y": 80, "z": 180}}]}

VAULT = {"models": [], "count": 0}
DOCTOR = {"checks": [{"name": "network", "ok": True}]}
QUOTE = {"grams": 42.0, "hours": 3.2, "price": 11.40}
RATES = {"electricity": 0.31, "filament": 22.0, "material": "PLA"}

DOWNLOAD = {"title": "Urban Spiderman", "design_id": 2325443,
            "dimensions": {"x": 100, "y": 80, "z": 180},
            "file_path": "/home/x/models/urban.3mf"}


# ── the defect ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tool,payload,args", [
    ("a3d_vault",  VAULT,  {}),
    ("a3d_doctor", DOCTOR, {}),
    ("a3d_quote",  QUOTE,  {}),
    ("a3d_rates",  RATES,  {}),
])
def test_an_answer_that_fills_no_slot_does_not_take_the_screen(tool, payload, args):
    assert not _drawable(tool, payload, args), (
        f"{tool} reached the island. Its payload fills none of the 30 "
        f"placeholders in a3d's template, so the card is em dashes in every "
        f"row.")


# ── a browse IS a deck: its slots are filled inside `candidates` ─────────────
#
# This used to be asserted the other way — that a3d_browse never draws —
# because the gate read only the top-level payload, where a browse carries
# just the query and the fleet. But the drawable fields (title, eta, size)
# sit one level down, inside `candidates`, and pill.html builds the deck from
# there ("A browse exists to show the candidates, so it opens the card").
# Measured 2026-09-21: five benchies came back with titles and times and NO
# carousel opened, because the gate refused the very payload the renderer was
# built to draw. The gate now looks inside the deck.

def test_a_browse_carousel_draws():
    assert _drawable("a3d_browse", BROWSE, {"query": "spiderman"}), (
        "a3d_browse returned candidates with titles — the deck the user flips "
        "through to pick a model. That is the whole point of browse.")


def test_an_empty_browse_still_does_not_draw():
    empty = {"query": "nothing", "printer": None, "failed": [],
             "fleet": [{"key": "CC1"}], "candidates": []}
    assert not _drawable("a3d_browse", empty, {"query": "nothing"}), (
        "a browse that found no models has an empty deck and must not open a "
        "card of em dashes.")


# ── and the answers that ARE cards still are ────────────────────────────────
#
# These carry the weight. A gate that refuses everything would "fix" the em
# dashes by removing the feature, and nobody would notice until they asked
# where their printer card went.

def test_a_printer_status_still_draws():
    assert _drawable("a3d_status", STATUS), (
        "a3d status fills printer, name and state — the card the user asks "
        "for when they say 'how's my printer'")


def test_a_downloaded_model_still_draws():
    """The card with the turntable on it, which is the point of the module."""
    assert _drawable("a3d_download", DOWNLOAD, {"query_or_id": "spiderman"})


def test_a_title_alone_is_enough():
    assert _drawable("a3d_download", {"title": "Urban Spiderman"},
                     {"query_or_id": "spiderman"})


def test_a_payload_that_only_echoes_the_question_is_not_an_answer():
    """`printer` is both a template field and a model-supplied argument.

    main.py merges the call's arguments into the payload, so a tool that
    answered with nothing at all would still carry back whatever the model
    passed in — and `printer` is drawn by the template. Counting it would
    reopen the hole one field wider than before.
    """
    assert not _drawable("a3d_print", {"printer": "CC1"}, {"printer": "CC1"})


def test_the_same_field_counts_when_the_module_supplied_it():
    """The mirror of the above: `printer` from the ANSWER is a real field."""
    assert _drawable("a3d_status", {"printer": "CC1", "state": "PRINTING"}, {})


# ── the mechanism, against the artifact it reads ────────────────────────────

def test_the_bus_reads_the_installed_template():
    """`island_fields` is the whole basis of the decision above."""
    from core.module_bus.bus import ModuleBus
    bus = ModuleBus().discover()
    fields = bus.island_fields("a3d_browse")
    assert len(fields) > 20, (
        f"a3d's template yielded {len(fields)} placeholders; the decision "
        f"above is being made against almost nothing")
    for expected in ("nozzle", "bed", "state", "title", "printer"):
        assert expected in fields, f"{expected} missing from the template scan"


def test_a_module_that_ships_no_template_is_unaffected():
    """An empty set means "no template", not "a template that draws nothing".

    Getting this backwards would silence every module that has no island at
    all, which is the behaviour `island_face` was explicitly written to avoid.
    """
    from core.module_bus.bus import ModuleBus
    bus = ModuleBus().discover()
    assert bus.island_fields("definitely_not_a_tool") == frozenset()
    assert main.AethelarkLive._card_is_drawable(
        "definitely_not_a_tool", {"anything": 1}, {})


def test_a_faced_module_never_reaches_this_path():
    """atrade declares a face, so it keeps being judged by `shows`.

    If the fallback ever started shadowing the faced path, atrade's carefully
    measured drawability rules would be replaced by "did it fill a slot", and
    the em-dash commits this file sits next to would all regress.
    """
    from core.module_bus.bus import ModuleBus
    bus = ModuleBus().discover()
    assert bus.island_face("atrade_quote") is not None
    assert not _drawable("atrade_portfolio",
                         {"cash": 100.0, "equity": 5000.0, "paper": True}, {})
