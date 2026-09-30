"""A running activity opens at the depth asked for and always comes back.

By default the island rests small and a running print shows only on demand:
a status answer walks capsule -> glance -> expanded, a spoken view jumps to a
depth, and every card decays back to the small pill. With "Running prints on
the island" switched on, the print's compact bar is what it rests on instead:
compact bar --tap--> glance --tap--> expanded --tap/expiry--> compact bar. Live
media streams only while the expanded card is on screen. atrade's depth fetch
is untouched: a module that declares nothing keeps the old behaviour.

Driven through the real page with stub bridges that record what was called.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import PILL_HTML, evaluate, load, pump  # noqa: E402

TEMPLATE = ('<div class="island-glance">{printer} {pct_label}</div>'
            '<div class="island-expanded"><img data-live alt="">{printer}</div>')


def _job(name, key, pct="40%"):
    card = {"variant": "job", "printer": name, "printer_key": key, "pct_label": pct}
    return {"key": "a3d:" + name, "module": "a3d", "leading": name, "trailing": "30m",
            "progress": 0.4, "stale": False,
            "card": {"printer": name, "title": name + " · Printing", "card": card}}


def _setup(view, activities, meta=None, expanded_s=1.0, rest=True):
    load(view, PILL_HTML)
    meta = meta if meta is not None else {"depth": False, "about": "printer_key",
                                          "live": True, "dwell": {"expanded": expanded_s}}
    evaluate(view, """
        window.__calls = [];
        window.pybridge = {
          island_trace: function(){}, island_collapsed: function(){},
          island_opened: function(m){ window.__calls.push(['opened', m]); },
          island_deadline: function(){},
          live_media: function(m, s, on){ window.__calls.push(['live', m, s, on]); },
          request_depth: function(m, t){ window.__calls.push(['depth', m, t]); }
        };
        window.pill.registerModule("a3d", %s, "", {}, %s);
        window.island.DWELL_OPEN_MS = 3000;
        window.island.ttlMs = 0;
        window.island.restActivities = %s;
        window.island.setActivities(%s);
        1""" % (json.dumps(TEMPLATE), json.dumps(meta), "true" if rest else "false",
               json.dumps(activities)))
    assert evaluate(view, "window.island.state") == ("activity" if activities and rest else "idle")


def _state(view):
    return evaluate(view, "window.island.state")


def _tap(view):
    evaluate(view, "window.island.send('pill_click', {}), 1")
    pump(0.1)


def test_taps_walk_the_depths_and_come_back(web_view):
    _setup(web_view, [_job("Centauri Carbon", "CENTAURI_CARBON")])
    _tap(web_view)
    assert _state(web_view) == "glance"
    _tap(web_view)
    assert _state(web_view) == "expanded"
    assert evaluate(web_view, "document.getElementById('pill').hasAttribute('data-depth')") is False, (
        "a module with nothing to fetch was shown waiting for a fetch")
    _tap(web_view)
    assert _state(web_view) == "activity"


def test_the_expanded_card_decays_on_its_own_dwell_back_to_the_activity(web_view):
    _setup(web_view, [_job("Centauri Carbon", "CENTAURI_CARBON")], expanded_s=1.0)
    _tap(web_view)
    _tap(web_view)
    left = evaluate(web_view, "window.island.dwellRemaining()")
    assert 700 < left <= 1000, f"expanded card armed with {left} ms, not its own 1 s"
    pump(1.4)
    assert _state(web_view) == "activity"


def test_voice_opens_the_named_printer_not_the_one_whose_name_contains_it(web_view):
    _setup(web_view, [_job("Centauri Carbon 2", "ELEGOO_7526B5"),
                      _job("Centauri Carbon", "CENTAURI_CARBON")])
    evaluate(web_view, "window.island.send('view', {stage: 'expanded', subject: 'Centauri Carbon'}), 1")
    pump(0.1)
    assert _state(web_view) == "expanded"
    assert evaluate(web_view, "window.island._data.card.printer_key") == "CENTAURI_CARBON"
    evaluate(web_view, "window.island.send('view', {stage: 'idle'}), 1")
    assert _state(web_view) == "activity"
    evaluate(web_view, "window.island.send('view', {stage: 'glance', subject: 'carbon 2'}), 1")
    pump(0.1)
    assert _state(web_view) == "glance"
    assert evaluate(web_view, "window.island._data.card.printer_key") == "ELEGOO_7526B5"


def test_a_view_of_nothing_running_does_nothing(web_view):
    _setup(web_view, [_job("Centauri Carbon", "CENTAURI_CARBON")])
    assert evaluate(web_view, "window.island._view('expanded', 'a3d', 'Neptune')") is False
    assert _state(web_view) == "activity"


def test_the_camera_streams_only_while_the_expanded_card_is_up(web_view):
    _setup(web_view, [_job("Centauri Carbon", "CENTAURI_CARBON")], expanded_s=10)
    _tap(web_view)
    assert evaluate(web_view, "window.__calls.filter(function(c){return c[0]==='live'}).length") == 0, (
        "the camera was started for the controls card")
    _tap(web_view)
    assert json.loads(evaluate(web_view, "JSON.stringify(window.__calls.filter(function(c){return c[0]==='live'}))")) == \
        [["live", "a3d", "CENTAURI_CARBON", True]]
    evaluate(web_view, "window.island.setLive('a3d', 'CENTAURI_CARBON', 'http://10.0.0.6:3031/video'), 1")
    assert evaluate(web_view, "document.querySelector('#pbody [data-live]').getAttribute('src')") == \
        "http://10.0.0.6:3031/video"
    assert evaluate(web_view, "document.getElementById('pill').getAttribute('data-live')") == "on"
    _tap(web_view)
    assert _state(web_view) == "activity"
    assert json.loads(evaluate(web_view, "JSON.stringify(window.__calls.filter(function(c){return c[0]==='live'}).pop())")) == \
        ["live", "a3d", "CENTAURI_CARBON", False]
    assert evaluate(web_view, "document.querySelectorAll('[data-live][src]').length") == 0


def test_a_late_camera_answer_for_a_card_already_gone_is_ignored(web_view):
    _setup(web_view, [_job("Centauri Carbon", "CENTAURI_CARBON")], expanded_s=10)
    _tap(web_view)
    _tap(web_view)
    _tap(web_view)
    evaluate(web_view, "window.island.setLive('a3d', 'CENTAURI_CARBON', 'http://10.0.0.6:3031/video'), 1")
    assert evaluate(web_view, "document.querySelectorAll('[data-live][src]').length") == 0
    assert evaluate(web_view, "document.getElementById('pill').hasAttribute('data-live')") is False


def test_an_alert_that_asks_for_the_camera_lands_on_it(web_view):
    _setup(web_view, [_job("Centauri Carbon", "CENTAURI_CARBON")], expanded_s=10)
    payload = dict(_job("Centauri Carbon", "CENTAURI_CARBON")["card"], alert="j:first_layer",
                   _stage="expanded")
    evaluate(web_view, "window.island.apply('a3d', %s), 1" % json.dumps(payload))
    pump(0.1)
    assert _state(web_view) == "expanded"


def test_talking_over_the_camera_holds_it_and_the_turn_ending_resumes_it(web_view):
    _setup(web_view, [_job("Centauri Carbon", "CENTAURI_CARBON")], expanded_s=1.0)
    _tap(web_view)
    _tap(web_view)
    evaluate(web_view, "window.island.apply('speaking', {}), 1")
    pump(1.5)
    assert _state(web_view) == "expanded", "the camera card expired while the eagle was talking"
    evaluate(web_view, "window.island.apply('idle', {}), 1")
    pump(1.4)
    assert _state(web_view) == "activity"


def test_a_module_that_declares_nothing_still_fetches_its_depth(web_view):
    _setup(web_view, [], meta={})
    evaluate(web_view, """
        window.pill.registerModule("atrade", '<div class="island-glance">{ticker}</div>', "", {});
        window.island.apply("atrade", {ticker: "NVDA", title: "NVDA"}); 1""")
    assert _state(web_view) == "capsule"
    _tap(web_view)
    assert _state(web_view) == "glance"
    _tap(web_view)
    assert _state(web_view) == "expanded"
    assert evaluate(web_view, "window.__calls.filter(function(c){return c[0]==='depth'}).length") == 1


def test_closing_with_nothing_running_rests(web_view):
    _setup(web_view, [_job("Centauri Carbon", "CENTAURI_CARBON")])
    _tap(web_view)
    evaluate(web_view, "window.island.setActivities([]), 1")
    evaluate(web_view, "window.island.send('view', {stage: 'idle'}), 1")
    assert _state(web_view) == "idle"


# ── the default: the island rests small, a print shows only when asked ──────

def test_a_running_print_does_not_hold_the_island_by_default(web_view):
    _setup(web_view, [_job("Centauri Carbon", "CENTAURI_CARBON")], rest=False)
    assert _state(web_view) == "idle"
    assert "running: a3d Centauri Carbon" in evaluate(web_view, "window.island._subjectLine()")
    evaluate(web_view, "window.island.setActivities(%s), 1"
             % json.dumps([_job("Centauri Carbon", "CENTAURI_CARBON", "41%")]))
    assert _state(web_view) == "idle"


def test_the_camera_asked_for_goes_back_to_the_small_pill(web_view):
    _setup(web_view, [_job("Centauri Carbon", "CENTAURI_CARBON")], rest=False, expanded_s=1.0)
    evaluate(web_view, "window.island.send('view', {stage: 'expanded', subject: 'Centauri Carbon'}), 1")
    pump(0.1)
    assert _state(web_view) == "expanded"
    pump(1.4)
    assert _state(web_view) == "idle"


def test_a_status_answer_walks_capsule_glance_camera_then_rests(web_view):
    _setup(web_view, [_job("Centauri Carbon", "CENTAURI_CARBON")], rest=False, expanded_s=10)
    status = {"printer": "CENTAURI_CARBON", "state": "PRINTING",
              "_card": _job("Centauri Carbon", "CENTAURI_CARBON")["card"]["card"]}
    evaluate(web_view, "window.island.apply('a3d', %s), 1" % json.dumps(status))
    pump(0.1)
    assert _state(web_view) == "capsule"
    _tap(web_view)
    assert _state(web_view) == "glance"
    _tap(web_view)
    assert _state(web_view) == "expanded"
    _tap(web_view)
    assert _state(web_view) == "idle"


def test_a_started_print_replaces_the_browse_with_its_capsule_then_rests(web_view):
    _setup(web_view, [], rest=False)
    evaluate(web_view, """window.pill.registerModule("a3d", %s, "", {}, {depth: false});
        window.island.apply("a3d", {query: "comb", printer: null,
            candidates: [{id: 1, title: "Comb"}, {id: 2, title: "Pick"}]}); 1""" % json.dumps(TEMPLATE))
    assert _state(web_view) == "glance"
    started = {"success": True, "printer": "CENTAURI_CARBON", "_stage": "capsule",
               "_card": {"variant": "job", "title": "Centauri Carbon", "badge": "Started"}}
    evaluate(web_view, "window.island.apply('a3d', %s), 1" % json.dumps(started))
    pump(0.1)
    assert _state(web_view) == "capsule"
    assert evaluate(web_view, "window.island.deck.length") == 0
