"""A malformed module payload must not take the card render down.

Modules are separate processes in separate repositories, and the island draws
whatever they send. `CLAUDE.md` is explicit that the seam between a module and
the host is where the defects live, and this is that seam in the direction
nobody had tested: not "does the card draw the right thing", but "what does the
card do when the payload is wrong".

The defect this pins was found by sending junk rather than by reading:

    {"_series": "nope"}

`generateSparkline` guarded with `!series || series.length < 2`. A STRING has a
length, so "nope" walked through the guard and `Math.min.apply(null, "nope")`
threw `CreateListFromArrayLike called on non-object`. That is not a blank chart
— the exception escapes the renderer, so the whole card fails to draw, at every
stage, for every field, because one optional key had the wrong type.

A module can produce that by shipping a bug, by a partial write, or by an
upgrade that changes a field's shape. None of those should reach the user as a
dead island.

Not a mirror: the assertion is that the page survives arbitrary input and still
draws, which cannot be satisfied by writing the payload and asserting it back.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import PILL_HTML, evaluate, load  # noqa: E402

ISLAND = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "module_bus" / "island"

#: Every shape a module has no business sending, and one it legitimately might.
PAYLOADS = {
    "empty": {},
    "nulls throughout": {"ticker": None, "price": None, "title": None,
                         "detail": None, "_series": None},
    "series is a string": {"ticker": "X", "price": 1, "_series": "nope"},
    "a range is a string": {"ticker": "X", "price": 1, "_series": {"1D": "nope"}},
    "series holds nulls": {"ticker": "X", "price": 1,
                           "_series": {"1D": [1, None, 3, 5]}},
    "series too short": {"ticker": "X", "price": 1, "_series": {"1D": [1]}},
    "layers is a string": {"ticker": "X", "layers": "nope"},
    "layers is a list": {"ticker": "X", "layers": [1, 2, 3]},
    "numbers arrived as prose": {"ticker": 123, "price": "not a number",
                                 "change_pct": "abc"},
    "very long strings": {"ticker": "X" * 400, "title": "T" * 600,
                          "detail": "D" * 900},
    "unicode and emoji": {"ticker": "ЖЮ😀🔥", "title": "日本語テスト",
                          "detail": "—–…‰"},
    "deeply nested": {"ticker": "X", "layers": {"a": {"b": {"c": {"d": 1}}}}},
}

STAGES = ("capsule", "glance", "expanded")


@pytest.fixture(scope="module")
def island(web_view):
    load(web_view, PILL_HTML)
    tpl = (ISLAND / "atrade" / "template.html").read_text(encoding="utf-8")
    css = (ISLAND / "atrade" / "style.css").read_text(encoding="utf-8")
    evaluate(web_view, "window.pybridge = {island_subject:function(){},"
                       "hit_region:function(){}}, 1")
    evaluate(web_view, "window.pill.registerModule(%s, %s, %s), 1"
             % (json.dumps("atrade"), json.dumps(tpl), json.dumps(css)))
    return web_view


def _render(view, payload: dict, stage: str) -> dict:
    """Draw one payload at one stage and report what survived."""
    js = """
      (function(){
        var I = window.island;
        try {
          I.module = "atrade";
          I._data = %s;
          I.deck = [];
          I._go(%s, I._data, "atrade");
        } catch (e) {
          return JSON.stringify({threw: String(e && e.message || e)});
        }
        var p = document.getElementById("pill");
        var b = p.getBoundingClientRect();
        return JSON.stringify({
          threw: null,
          w: Math.round(b.width), h: Math.round(b.height),
          rawBrace: /\\{[a-z0-9_]+\\}/i.test(document.body.innerText)
        });
      })()
    """ % (json.dumps(payload), json.dumps(stage))
    return json.loads(evaluate(view, js))


@pytest.mark.parametrize("name", sorted(PAYLOADS))
@pytest.mark.parametrize("stage", STAGES)
def test_a_bad_payload_does_not_throw(island, name, stage):
    """The card render must survive anything a module sends."""
    out = _render(island, PAYLOADS[name], stage)
    assert out["threw"] is None, (
        f"{name} at {stage} threw: {out['threw']}. A module sending the wrong "
        f"type for one optional field must not stop the card drawing.")


@pytest.mark.parametrize("name", sorted(PAYLOADS))
def test_the_pill_never_collapses_on_a_bad_payload(island, name):
    """Surviving is not enough; a pill of nothing is a dead island."""
    out = _render(island, PAYLOADS[name], "glance")
    assert out["w"] >= 100 and out["h"] >= 30, (
        f"{name}: the pill collapsed to {out['w']}x{out['h']}")


@pytest.mark.parametrize("name", sorted(PAYLOADS))
def test_no_template_variable_is_ever_shown_to_the_user(island, name):
    """A raw `{placeholder}` on screen is the failure the card contract test
    guards for good payloads. A bad one must not reintroduce it."""
    out = _render(island, PAYLOADS[name], "glance")
    assert not out["rawBrace"], f"{name}: a raw template brace reached the screen"


# ── and the good case still works ───────────────────────────────────────────

def test_a_real_series_still_draws_a_chart(island):
    """The guard has to reject junk without rejecting the product.

    Numbers that arrive as strings are a chart, not junk: a module sending
    ["229.6", "230.1"] means the same thing as sending floats, and refusing it
    would be the literalism that once drew em dashes over values that were
    present.
    """
    for label, series in {"floats": [229.0, 230.0, 231.0, 230.5],
                          "numeric strings": ["229.6", "230.1", "231.4"]}.items():
        payload = {"ticker": "NVDA", "price": 230.0, "change_pct": 0.3,
                   "_series": {"1D": series}}
        _render(island, payload, "glance")
        drawn = evaluate(island, "!!document.querySelector('.spark svg')")
        assert drawn, f"{label}: a valid series drew no sparkline"


def test_html_in_a_payload_is_escaped_not_executed(island):
    """A module is a separate process; its strings are data, never markup."""
    payload = {"ticker": "<img src=x onerror=window.__XSS=1>",
               "title": "<script>window.__XSS=1</script>",
               "detail": '"><b>bold</b>'}
    _render(island, payload, "capsule")
    assert not evaluate(island, "!!window.__XSS"), "injected script executed"
    assert not evaluate(
        island, "!!document.querySelector('#pbody img, #pbody script')"), (
        "a raw element from a payload was injected into the DOM")
