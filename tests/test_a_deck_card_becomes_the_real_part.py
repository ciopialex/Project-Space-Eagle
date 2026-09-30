"""A browse's cover becomes the downloaded part, without moving the user.

A browse answers in about two seconds with covers and downloads nothing,
because MakerWorld puts each download behind a robot check (~20 s, measured
2026-09-25). The module declares in its manifest how one candidate is made
real (`[deck] refine = "download"`), and the host runs that in the background
and hands each answer to the page. This is the page half: the answer lands on
the card it belongs to, the card on screen turns into the part, and the
user's cursor and picks stay where they put them.

The seam is host -> page, driven through the real pill.html in a real
QWebEngineView with the a3d card template.
"""
from __future__ import annotations

import base64
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import PILL_HTML, evaluate, load  # noqa: E402

ISLAND = Path(__file__).resolve().parent / "fixtures" / "module_bus" / "island" / "a3d"


def _cube() -> dict:
    """A preview in the module's wire format: int16 xyz per vertex, base64."""
    q = 32767
    v = [(x, y, z) for x in (-q, q) for y in (-q, q) for z in (-q, q)]
    faces = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1),
             (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]
    packed = [c for f in faces for i in f for c in v[i]]
    return {"triangles": len(faces),
            "vertices": base64.b64encode(struct.pack(f"<{len(packed)}h", *packed)).decode()}


DECK = {
    "query": "phone stand", "printer": None,
    "fleet": [{"key": "CENTAURI_CARBON_2", "name": "Centauri Carbon 2"}],
    "candidates": [
        {"id": 819327, "title": "Phone stand", "weight": "73.0g", "print_time": "1h 45m",
         "printer": "CENTAURI_CARBON_2"},
        {"id": 127277, "title": "Phone Stand (No AMS needed)", "weight": "52.0g",
         "print_time": "1h 33m", "printer": "CENTAURI_CARBON_2"},
        {"id": 470013, "title": "Minimalistic Phone Stand", "weight": "45.0g",
         "print_time": "1h 43m", "printer": "CENTAURI_CARBON_2"},
    ],
}


def _open_deck(view):
    load(view, PILL_HTML)
    evaluate(view, """
        window.pybridge = {island_collapsed: function(){}, island_trace: function(){}};
        window.pill.registerModule("a3d", %s, %s, {});
        window.island.apply("a3d", %s);
        1""" % (json.dumps((ISLAND / "template.html").read_text()),
                json.dumps((ISLAND / "style.css").read_text()),
                json.dumps(DECK)))
    assert evaluate(view, "window.island.deck.length") == 3


def _refine(view, model_id, printer=None):
    answer = {"success": True, "design_id": model_id, "file_path": "/x.3mf",
              "dimensions": {"x": 73.0, "y": 81.2, "z": 70.0},
              "printer": printer, "_preview": _cube()}
    return evaluate(view, "window.island.refineCandidate('id', %s, %s)"
                    % (json.dumps(model_id), json.dumps(answer)))


def test_the_card_on_screen_turns_into_the_downloaded_part(web_view):
    _open_deck(web_view)
    evaluate(web_view, "window.island.send('pill_click', {}), 1")   # open the glance
    assert evaluate(web_view, "window.island.state") == "glance"
    assert evaluate(web_view, "window.a3dViewer.isPlaceholder()") is True
    assert _refine(web_view, 819327) is True
    assert evaluate(web_view, "window.a3dViewer.isPlaceholder()") is False, (
        "the part was downloaded and the card still shows the cover")
    assert evaluate(web_view, "window.island.current().dimensions.x") == 73.0


def test_an_answer_for_another_card_moves_nothing(web_view):
    _open_deck(web_view)
    evaluate(web_view, "window.island.setPrinter('CENTAURI_CARBON_2'), 1")
    picks_before = evaluate(web_view, "window.island.selectionForPrint()")
    assert _refine(web_view, 470013, printer=None) is True
    assert evaluate(web_view, "window.island.cursor") == 0, (
        "a background download moved the user off the card they were reading")
    assert evaluate(web_view, "window.island.selectionForPrint()") == picks_before
    assert evaluate(web_view, "!!window.island.deck[2]._preview") is True
    # An empty field in the answer does not unset what the card already had.
    assert evaluate(web_view, "window.island.deck[2].printer") == "CENTAURI_CARBON_2"


def test_an_answer_for_a_card_that_is_gone_is_dropped(web_view):
    _open_deck(web_view)
    assert _refine(web_view, 999) is False
