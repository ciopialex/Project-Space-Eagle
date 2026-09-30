"""The island's state machine, driven by random sequences of everything that
can happen to it, checked after every step and left to settle at the end.

Real page, real a3d and atrade card templates, stub bridges that answer late
or never. The clocks are shortened so a run takes seconds, not minutes.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import PILL_HTML, evaluate, load, pump  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "module_bus" / "island"

CHECK = r"""
window.__check = function(){
  var I = window.island, p = document.getElementById('pill'), v = [];
  var G = {idle:[168,46], activity:[320,62], listening:[320,62], thinking:[320,62], speaking:[320,62],
           swarm:[320,62], ready:[320,62], capsule:[320,62], notice:[320,52], glance:[440,236], expanded:[440,364]};
  if (!G[I.state]) { v.push('unknown state ' + I.state); return v; }
  var want = I.state === 'glance' ? 'glance' : (I.state === 'expanded' ? 'expanded' : 'capsule');
  if (p.getAttribute('data-stage') !== want) v.push('stage ' + p.getAttribute('data-stage') + ' in ' + I.state);
  var rest = I.state === 'idle' || I.state === 'activity';
  if (rest && I.module) v.push('module ' + I.module + ' left at rest');
  if (rest && I.deck.length) v.push('deck left at rest');
  if (rest && I._deadline) v.push('clock left running at rest');
  if (I.state === 'activity' && !(I.restActivities && I._activities.length)) v.push('activity with nothing to show');
  var card = I.state === 'glance' || I.state === 'expanded' || I.state === 'capsule';
  if (card && !I.module) v.push(I.state + ' with no module');
  if (card && I.module && !I.deck.length && !I._held && !I._awaitingDepth && !I._deadline)
    v.push('a ' + I.module + ' ' + I.state + ' with no clock: it would never leave');
  if (document.querySelectorAll('#pbody [data-live][src]').length && I.state !== 'expanded') v.push('camera streaming outside the camera card');
  if (p.hasAttribute('data-live') && I.state !== 'expanded') v.push('data-live outside expanded');
  if (I._live && I.state !== 'expanded') v.push('camera session held outside expanded');
  if (p.hasAttribute('data-module') && !I.module) v.push('data-module with no module');
  var g = G[I.state];
  if (p.style.width !== g[0] + 'px' || p.style.height !== g[1] + 'px')
    v.push('size ' + p.style.width + 'x' + p.style.height + ' in ' + I.state);
  return v;
};
1"""


def _setup(view):
    load(view, PILL_HTML)
    a3d_t = (FIX / "a3d" / "template.html").read_text()
    a3d_c = (FIX / "a3d" / "style.css").read_text()
    at_t = (FIX / "atrade" / "template.html").read_text()
    at_c = (FIX / "atrade" / "style.css").read_text()
    evaluate(view, """
        window.__lives = []; window.__depth = [];
        window.pybridge = {
          island_trace: function(){}, island_collapsed: function(){}, island_opened: function(){},
          island_deadline: function(){}, prefetch_depth: function(){}, refine_pick: function(){},
          module_action: function(){}, interrupt: function(){},
          live_media: function(m, s, on){ window.__lives.push([m, s, on]); },
          request_depth: function(m, t){ window.__depth.push([m, t]); }
        };
        window.pill.registerModule("a3d", %s, %s, {}, {depth: false, about: "printer_key", live: true, dwell: {expanded: 0.9}});
        window.pill.registerModule("atrade", %s, %s, {}, {depth: true, about: "ticker"});
        var I = window.island;
        I.DWELL_OPEN_MS = 400; I.DWELL_STEP_MS = 150; I.DWELL_CEIL_MS = 1100;
        I.HOLD_CEIL_MS = 1500; I.DEPTH_WAIT_MS = 900; I.ttlMs = 0;
        1""" % tuple(json.dumps(x) for x in (a3d_t, a3d_c, at_t, at_c)))
    evaluate(view, CHECK)


JOB = {"variant": "job", "title": "Centauri Carbon", "printer": "Centauri Carbon",
       "printer_key": "CENTAURI_CARBON", "pct_label": "40%", "badge": "40%"}
PRINTER = dict(JOB, variant="printer", badge="Idle", pct_label="—")
ACT = {"key": "a3d:Centauri Carbon", "module": "a3d", "leading": "Centauri Carbon",
       "trailing": "30m", "progress": 0.4, "stale": False, "card": {"printer": "Centauri Carbon", "card": JOB}}
ACT2 = dict(ACT, key="a3d:Centauri Carbon 2", leading="Centauri Carbon 2",
            card={"printer": "Centauri Carbon 2", "card": dict(JOB, printer="Centauri Carbon 2",
                                                                printer_key="ELEGOO_7526B5")})
DECK = {"query": "comb", "printer": None, "fleet": [{"key": "CENTAURI_CARBON", "name": "Centauri Carbon"}],
        "candidates": [{"id": i, "title": "Comb %d" % i, "cover_url": ""} for i in range(1, 4)]}
QUOTE = {"ticker": "TSLA", "company_name": "Tesla Inc.", "price": 355.6, "change_pct": -0.7}
DEPTH = dict(QUOTE, verdict="NEUTRAL", composite_score=57,
             layers={"layer_1_fundamentals": {"score": 59}, "layer_2_macro_gravity": {"score": 48}})

EVENTS = [
    ("browse", "window.island.apply('a3d', %s)" % json.dumps(DECK)),
    ("status job", "window.island.apply('a3d', %s)" % json.dumps({"printer": "CENTAURI_CARBON", "_card": JOB})),
    ("status idle", "window.island.apply('a3d', %s)" % json.dumps({"printer": "CENTAURI_CARBON", "_card": PRINTER})),
    ("alert camera", "window.island.apply('a3d', %s)" % json.dumps({"printer": "Centauri Carbon", "alert": "j:fl",
                                                                    "_stage": "expanded", "card": JOB})),
    ("started", "window.island.apply('a3d', %s)" % json.dumps({"printer": "CENTAURI_CARBON", "_stage": "capsule",
                                                              "_card": dict(JOB, badge="Started")})),
    ("quote", "window.island.apply('atrade', %s)" % json.dumps(QUOTE)),
    ("depth arrives", "window.island.apply('atrade', %s)" % json.dumps(DEPTH)),
    ("listening", "window.island.apply('listening', {})"),
    ("thinking", "window.island.apply('thinking', {})"),
    ("speaking", "window.island.apply('speaking', {})"),
    ("voice idle", "window.island.apply('idle', {})"),
    ("text answer", "window.island.send('text_return', {})"),
    ("notice", "window.island.apply('notice', {text: 'x'})"),
    ("swarm", "window.island.apply('swarm', {})"),
    ("tap", "window.island.send('pill_click', {})"),
    ("tap", "window.island.send('pill_click', {})"),
    ("deck next", "window.island.send('deck_move', {delta: 1})"),
    ("view summary", "window.island.send('view', {stage: 'glance'})"),
    ("view details", "window.island.send('view', {stage: 'expanded', subject: 'Centauri Carbon'})"),
    ("view close", "window.island.send('view', {stage: 'idle'})"),
    ("activities on", "window.island.setActivities(%s)" % json.dumps([ACT, ACT2])),
    ("activities off", "window.island.setActivities([])"),
    ("rest on", "window.island.restActivities = true, window.island.setActivities(window.island._activities)"),
    ("rest off", "window.island.restActivities = false, window.island.setActivities(window.island._activities)"),
    ("camera answers", "window.island.setLive('a3d', 'CENTAURI_CARBON', 'http://10.0.0.6:3031/video')"),
    ("camera streaming", "window.island.send('view', {stage: 'expanded', subject: 'Centauri Carbon'}),"
                         " window.island.apply('a3d', %s),"
                         " window.island.setLive('a3d', 'CENTAURI_CARBON', 'http://10.0.0.6:3031/video')"
     % json.dumps({"printer": "CENTAURI_CARBON", "_stage": "expanded", "_card": JOB})),
    ("camera fails", "window.island.setLive('a3d', 'CENTAURI_CARBON', '')"),
    ("hover", "window.island.send('mouse_enter', {})"),
    ("unhover", "window.island.send('mouse_leave', {})"),
    ("expire", "window.island.send('ttl_expire', {})"),
]


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_every_step_is_consistent_and_every_card_comes_back(web_view, seed):
    _setup(web_view)
    rng = random.Random(seed)
    trail = []
    for step in range(220):
        name, js = rng.choice(EVENTS)
        trail.append(name)
        evaluate(web_view, js + ", 1")
        pump(rng.choice((0.0, 0.02, 0.05, 0.3)))
        bad = evaluate(web_view, "window.__check()")
        assert not bad, f"seed {seed} step {step}: {bad} after …{trail[-8:]}"
    evaluate(web_view, "window.island.apply('idle', {}), window.island.send('mouse_leave', {}), 1")
    pump(4.0)
    state = evaluate(web_view, "window.island.state")
    deck = evaluate(web_view, "window.island.deck.length")
    assert state in ("idle", "activity") or deck, (
        f"seed {seed}: left alone, the island stayed on {state} after …{trail[-8:]}")
    assert not evaluate(web_view, "window.__check()")
