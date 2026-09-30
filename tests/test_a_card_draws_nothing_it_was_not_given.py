"""Every field a card template asks for is one some payload can actually fill.

`web/pill.html` fills a module's template by substituting `{name}` for each key
in the payload, and then deletes whatever is left over. That deletion is what
made a whole family of defects invisible. The card asks for `{eta}`, `a3d`
calls the same number `print_time`, the gap renders as nothing, and the only
way anyone finds out is by looking at a card and wondering why it is blank.

Four of ten consecutive commits were that one shape:

    ff92f06  fix(cards): the island was drawing claims no payload ever made
    f5ae599  fix(island): an answer no card can draw no longer takes the screen
    0ad78f0  fix(island): ETA and Filament were in the payload the whole time
    f9c7054  fix(capsule): say what the quote answered instead of two em dashes

Every one was found by a person looking at the screen. The suite was green for
all of them, because nothing compared the template against the payload.

This is a seam test in the sense CLAUDE.md means -- the module's template and
the host's renderer meet here, and neither can see the other -- and it is a
property over an artifact rather than a mirror: the template is the product,
the assertion is about a contract between two repositories, and it fails on a
real disagreement rather than on a rename.

The renderer now records what it deleted on `window.pill._unfilled`, so the
page reports its own contract violations instead of swallowing them.

Adding a module means adding a payload below. That is deliberate: a card
nobody has ever rendered is a card nobody knows is blank.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import PILL_HTML, evaluate, load, pump  # noqa: E402

ISLAND = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "module_bus" / "island"

#: One realistic payload per module, in the shape its own binary emits.
#:
#: Taken from real `--json` output rather than invented: `a3d` really does send
#: `print_time` and `weight` as strings with units, and `atrade` really does
#: send `_series` keyed by range. A fixture that guessed those shapes would
#: pass while the product was broken, which is the failure mode this whole file
#: exists to catch.
PAYLOADS: dict[str, list[dict]] = {
    "atrade": [
        {
            "_what": "a quote — no verdict, no layers",
            "ticker": "NVDA", "company_name": "NVIDIA Corp.",
            "price": 230.36, "previous_close": 229.62, "change": 0.74,
            "change_pct": 0.32, "day_high": 234.76, "day_low": 229.63,
            "volume": 134946800, "market_cap": 5562502934738,
            "_series": {"1D": [229.0, 230.0, 231.0, 230.5, 231.2]},
            "title": "NVDA", "detail": "$230.36  +0.32%",
        },
        {
            "_what": "an analyze — the seven-layer card",
            "ticker": "NVDA", "company_name": "NVIDIA Corp.",
            "title": "ACCUMULATE", "detail": "the business prints cash",
            "verdict": "ACCUMULATE", "reason": "the business prints cash",
            "why": "the business prints cash",
            "price": 230.36, "change_pct": 0.32, "previous_close": 229.62,
            "asymmetry_ratio": "0.2 : 1", "invalidation_level": "$218.08",
            "coverage": 1.0,
            "layers": {
                "layer_1_fundamentals": {"score": 78, "summary": "x",
                                         "available": True},
                "layer_2_macro_gravity": {"score": 55, "summary": "y",
                                          "available": True},
            },
            "_series": {"1D": [229.0, 230.0, 231.0]},
        },
    ],
    "a3d": [
        {
            "_what": "one downloaded model, no deck",
            "printer": "CC1", "title": "Urban Spiderman", "detail": "169g",
            "dimensions": {"x": 12.344, "y": 12.782, "z": 28.132},
            "weight": "169.0g", "print_time": "9h 49m",
        },
        {
            "_what": "a browse — several candidates, deck open",
            "printer": "CC1", "title": "2 models", "detail": "Spider-Man",
            "candidates": [
                {"id": 1, "title": "Urban Spiderman", "weight": "169.0g",
                 "print_time": "9h 49m",
                 "dimensions": {"x": 12.3, "y": 12.8, "z": 28.1}},
                {"id": 2, "title": "Spider-Man Helmet", "weight": "1736.0g",
                 "print_time": "89h 0m",
                 "dimensions": {"x": 175.2, "y": 137.0, "z": 261.3}},
            ],
        },
        {
            "_what": "printer telemetry, no model at all",
            "printer": "CC2", "title": "CC2", "detail": "printing",
            "telemetry": {"state": "PRINTING", "progress": 42},
        },
    ],
    "alaw": [
        {
            "_what": "a tax answer",
            "entity_type": "SRL", "company": "Acme SRL", "cui": "RO123456",
            "applied_tax_rate": "1%", "corporate_tax_ron": 1000.0,
            "dividend_tax_ron": 200.0, "cass_due_ron": 50.0,
            "net_personal_cash_ron": 900.0, "effective_tax_rate_percent": 12.0,
            "title": "SRL", "detail": "micro régime · 1%",
        },
    ],
}

STAGES = ("capsule", "glance", "expanded")


def _modules() -> list[str]:
    return sorted(d.name for d in ISLAND.iterdir()
                  if d.is_dir() and (d / "template.html").is_file())


@pytest.fixture(scope="module")
def island(web_view):
    load(web_view, PILL_HTML)
    for key in _modules():
        tpl = (ISLAND / key / "template.html").read_text(encoding="utf-8")
        css_path = ISLAND / key / "style.css"
        css = css_path.read_text(encoding="utf-8") if css_path.is_file() else ""
        evaluate(web_view, "window.pill.registerModule(%s, %s, %s), 1"
                 % (json.dumps(key), json.dumps(tpl), json.dumps(css)))
    return web_view


def test_every_installed_module_has_a_payload_to_render():
    """The gate that keeps the rest of this file honest.

    Without it, a module added tomorrow is simply not covered and the file
    still passes — which is exactly how a card gets to production having never
    been rendered by anything but a person, if at all.
    """
    missing = [m for m in _modules() if m not in PAYLOADS]
    assert missing == [], (
        f"no card payload in this file for: {missing}. Add one shaped like "
        f"that module's real `--json` output, or its template is unrendered "
        f"by anything here.")


@pytest.mark.parametrize("module", sorted(PAYLOADS))
def test_a_module_card_leaves_no_field_unfilled(island, module):
    """The defect itself: a template asking for something nothing provides."""
    if module not in _modules():
        pytest.skip(f"{module} is not installed in core/module_bus/island")

    complaints: list[str] = []
    for payload in PAYLOADS[module]:
        what = payload.get("_what", "payload")
        deck = payload.get("candidates") or []
        for stage in STAGES:
            js = """
              (function(){
                var I = window.island;
                I.module = %s;
                I._data = %s;
                I.deck = %s;
                I.cursor = 0;
                window.pill._unfilled = null;
                I._go(%s, I._data, %s);
                return JSON.stringify(window.pill._unfilled || []);
              })()
            """ % (json.dumps(module), json.dumps(payload), json.dumps(deck),
                   json.dumps(stage), json.dumps(module))
            unfilled = json.loads(evaluate(island, js))
            pump(0.05)
            if unfilled:
                complaints.append(f"{module}/{stage} on {what}: "
                                  f"{' '.join(unfilled)}")

    assert complaints == [], (
        "a card asked for fields the payload could not fill, and the renderer "
        "deleted them silently:\n  " + "\n  ".join(complaints) +
        "\n\nEither the module sends that field under another name (check its "
        "real --json output), or the template should not be asking for it.")


def test_the_renderer_actually_reports_what_it_deleted(island):
    """Guards the guard.

    Every assertion above reads `window.pill._unfilled`. If the renderer ever
    stops recording — a refactor, a rename, someone restoring the silent
    `replace` — every test in this file passes against a broken product while
    measuring nothing at all. So one case deliberately renders a template with
    a field no payload can carry, and requires the page to say so.
    """
    js = """
      (function(){
        window.pill.registerModule("__probe__",
          "<div class='island-capsule'>{ticker} {no_such_field}</div>", "");
        var I = window.island;
        I.module = "__probe__";
        I._data = {ticker: "NVDA"};
        I.deck = [];
        window.pill._unfilled = null;
        I._go("capsule", I._data, "__probe__");
        return JSON.stringify(window.pill._unfilled || []);
      })()
    """
    unfilled = json.loads(evaluate(island, js))
    assert "{no_such_field}" in unfilled, (
        "the renderer no longer records unfilled placeholders, so every other "
        "assertion in this file is measuring nothing")
