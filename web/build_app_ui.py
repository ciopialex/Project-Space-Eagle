"""Build web/dashboard.html — the Aethelark web dashboard for the desktop app.

Strategy for EXACT visual parity: we do NOT rewrite the design. We take the
approved artifact (web/artifact_reference.html — the exact page the founder
signed off on) verbatim and apply only two additive layers:

  1. an override <style> that fills the viewport with the `.app` window and
     hides the presentation-only chrome (eyebrow, hint, pill showcase, notes),
  2. a bridge <script> that connects QWebChannel and routes the title-bar
     buttons + inputs to the native Python shell (window.pybridge), and exposes
     window.aethelark.* for the daemon to drive the UI (wired to real data in
     Phase 2).

Because the CSS/HTML/fonts/emblem are the untouched artifact, QWebEngine
(same Chromium engine as the browser) renders it pixel-identical.
"""
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
src = (HERE / "artifact_reference.html").read_text(encoding="utf-8")

OVERRIDE_STYLE = """
<style id="app-shell-overrides">
  html, body { padding:0 !important; margin:0 !important; overflow:hidden !important;
               background:transparent !important; width:100%; height:100%; }
  /* Presentation-only chrome from the artifact — hidden in the real app.
     The starfield was the web page's void; on the desktop the card floats on
     the real desktop, so it's dropped here. */
  .eyebrow, .hint, .island-wrap, .notes, #stars { display:none !important; }
  .wrap { max-width:none !important; width:100vw !important; height:100vh !important;
          gap:0 !important; padding:0 !important; margin:0 !important; }
  /* The dashboard is a floating rounded card at the artifact's proportions.
     A small margin lets its own shadow + the starfield void show around it —
     exactly like the artifact — so the collapse morph keeps the same ratio. */
  .app { width:calc(100vw - 44px) !important; height:calc(100vh - 44px) !important;
         max-width:none !important; margin:22px !important; }

  /* ── Expand / collapse morph ─────────────────────────────────────────────
     The native window is shown at its FINAL size; the "spring from the island"
     is done here as a GPU-composited transform on the card, so Chromium never
     re-lays-out the page mid-animation (the old geometry-resize morph did, which
     is what made it stutter). Origin near the top-centre so it reads as growing
     from the Dynamic Island's spot. */
  .app { transform-origin:50% 4%; }
  .app.ae-in  { animation:aeIn  .46s cubic-bezier(.22,1.12,.35,1) both; will-change:transform,opacity; }
  .app.ae-out { animation:aeOut .24s cubic-bezier(.4,0,.9,.4)     both; will-change:transform,opacity; }
  @keyframes aeIn  { from { transform:scale(.16) translateY(-11vh); opacity:0; }
                     60% { opacity:1; }
                     to   { transform:none; opacity:1; } }
  @keyframes aeOut { from { transform:none; opacity:1; }
                     to   { transform:scale(.16) translateY(-11vh); opacity:0; } }

  /* Let the user select & copy chat / timeline / memory text (the whole window
     is draggable and the artifact sets user-select:none, which blocked it). */
  .log, .log *, .tl, .tl *, .memcard, .memcard * { user-select:text !important;
    -webkit-user-select:text !important; cursor:auto; }

  /* Chat + swarm timeline must scroll through history (artifact clipped them). */
  .log, .tl { overflow-y:auto !important; overflow-x:hidden !important; overscroll-behavior:contain; }
  .log::-webkit-scrollbar, .tl::-webkit-scrollbar { width:8px; }
  .log::-webkit-scrollbar-thumb, .tl::-webkit-scrollbar-thumb { background:rgba(200,200,208,.18); border-radius:4px; }
  .log::-webkit-scrollbar-thumb:hover, .tl::-webkit-scrollbar-thumb:hover { background:rgba(200,200,208,.32); }
  .log::-webkit-scrollbar-track, .tl::-webkit-scrollbar-track { background:transparent; }
  /* Muted state for the mic button — mirrors the red 'interrupt' treatment. */
  .opbtn.mic.muted { color:var(--red) !important;
    background:linear-gradient(135deg,rgba(40,8,14,.6),rgba(20,4,8,.7)) !important;
    box-shadow:inset 0 0 0 1px rgba(239,77,92,.5) !important; }
  .opbtn.mic.muted .d { animation:none !important; }

  /* ── Settings panel (opened by the title-bar gear ⚙) ────────────────────
     A tech-noir sheet that slides over the dashboard, matching the onboarding
     card's language (silver strokes, Doto labels, glass fills). Built + driven
     entirely from the bridge script so artifact_reference.html stays untouched. */
  .stud { cursor:pointer; transition:.16s; }
  .stud:hover { color:var(--silver-hi,#ECECF2) !important;
                box-shadow:inset 0 0 0 1px rgba(200,200,208,.5); }
  #set-scrim { position:absolute; inset:0; z-index:120; display:none;
    background:rgba(4,4,6,.62); backdrop-filter:blur(3px);
    animation:setfade .18s ease both; }
  #set-scrim.open { display:block; }
  @keyframes setfade { from{opacity:0} to{opacity:1} }
  #set-panel { position:absolute; top:0; right:0; height:100%;
    width:min(460px,86%); display:flex; flex-direction:column;
    background:linear-gradient(180deg,rgba(18,18,24,.98),rgba(8,8,12,.99));
    box-shadow:-30px 0 80px rgba(0,0,0,.6), inset 1px 0 0 rgba(200,200,208,.14);
    transform:translateX(100%); transition:transform .26s cubic-bezier(.2,.9,.25,1); }
  #set-scrim.open #set-panel { transform:translateX(0); }
  .set-hd { display:flex; align-items:center; justify-content:space-between;
    padding:20px 24px 14px; flex:none;
    box-shadow:inset 0 -1px 0 rgba(200,200,208,.1); }
  .set-hd .ttl { font-family:'Doto'; font-weight:800; font-size:13px;
    letter-spacing:.34em; color:var(--silver,#C8C8D0); }
  .set-hd .x { width:28px; height:28px; border-radius:8px; display:grid;
    place-items:center; color:var(--muted,#7C7C86); cursor:pointer; font-size:14px;
    background:rgba(255,255,255,.03); box-shadow:inset 0 0 0 1px rgba(200,200,208,.14); }
  .set-hd .x:hover { color:#fff; box-shadow:inset 0 0 0 1px var(--silver,#C8C8D0); }
  .set-body { flex:1; overflow-y:auto; padding:8px 24px 26px; }
  .set-body::-webkit-scrollbar { width:8px; }
  .set-body::-webkit-scrollbar-thumb { background:rgba(200,200,208,.18); border-radius:4px; }
  .set-sec { margin-top:22px; }
  .set-sec > .lbl { font-family:'Doto'; font-weight:700; font-size:10px;
    letter-spacing:.2em; color:var(--muted,#7C7C86); text-transform:uppercase;
    margin-bottom:10px; }
  .set-row { display:flex; align-items:center; gap:14px; padding:14px 16px;
    border-radius:13px; margin-bottom:10px; background:rgba(255,255,255,.025);
    box-shadow:inset 0 0 0 1px rgba(200,200,208,.14); }
  .set-row .ic { flex:none; width:36px; height:36px; border-radius:10px;
    display:grid; place-items:center; font-family:'Doto'; font-weight:700; font-size:14px;
    background:linear-gradient(135deg,#2a2a32,#141419); color:var(--silver-hi,#ECECF2);
    box-shadow:inset 0 1px 0 rgba(255,255,255,.06); }
  .set-row .tt { flex:1; min-width:0; }
  .set-row .tt .t { font-size:13.5px; color:var(--text,#E5E5EA); font-weight:600;
    display:flex; align-items:center; gap:8px; }
  .set-row .tt .d { font-size:11.5px; color:var(--muted,#7C7C86); line-height:1.5;
    margin-top:3px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .set-mini { font-family:'Manrope'; font-weight:600; font-size:11px; letter-spacing:.06em;
    padding:8px 14px; border-radius:9px; cursor:pointer; flex:none; border:0;
    color:var(--text2,#C8C8D0); background:rgba(255,255,255,.04);
    box-shadow:inset 0 0 0 1px rgba(200,200,208,.16); transition:.16s; }
  .set-mini:hover { color:#fff; box-shadow:inset 0 0 0 1px var(--silver,#C8C8D0); }
  .set-mini.danger:hover { color:var(--red,#ef4d5c);
    box-shadow:inset 0 0 0 1px rgba(239,77,92,.6); }
  .set-mini.solid { color:#0A0A0A; background:linear-gradient(135deg,#ECECF2,#B0B0B8);
    box-shadow:0 8px 20px rgba(200,200,208,.16); }
  .set-mini:disabled { opacity:.45; cursor:default; }
  .set-badge { font-family:'Doto'; font-weight:700; font-size:8px; letter-spacing:.14em;
    padding:3px 7px; border-radius:6px; color:var(--green,#4ee08a);
    box-shadow:inset 0 0 0 1px rgba(78,224,138,.45); }
  .set-badge.off { color:var(--muted,#7C7C86); box-shadow:inset 0 0 0 1px rgba(200,200,208,.2); }
  /* toggle switch */
  .set-sw { flex:none; width:42px; height:24px; border-radius:13px; cursor:pointer;
    background:rgba(255,255,255,.06); box-shadow:inset 0 0 0 1px rgba(200,200,208,.2);
    position:relative; transition:.2s; }
  .set-sw::after { content:""; position:absolute; top:3px; left:3px; width:18px; height:18px;
    border-radius:50%; background:var(--silver,#C8C8D0); transition:.2s; }
  .set-sw.on { background:linear-gradient(135deg,#ECECF2,#B0B0B8);
    box-shadow:inset 0 0 0 1px rgba(200,200,208,.4); }
  .set-sw.on::after { left:21px; background:#0A0A0A; }
  .set-sw.disabled { opacity:.4; cursor:default; }
  .set-field { width:100%; height:42px; border:0; border-radius:11px; padding:0 14px;
    background:rgba(255,255,255,.04); color:var(--text,#E5E5EA);
    font-family:'Manrope'; font-size:13px; outline:none;
    box-shadow:inset 0 0 0 1px rgba(200,200,208,.14); }
  .set-field:focus { box-shadow:inset 0 0 0 1px var(--silver,#C8C8D0); }
  .set-field::placeholder { color:var(--muted,#7C7C86); }
  .set-stack { display:block; padding:14px 16px; }
  .set-stack .t { font-size:13.5px; color:var(--text,#E5E5EA); font-weight:600; margin-bottom:8px; }
  .set-inline { display:flex; gap:8px; margin-top:8px; }
  .set-inline .set-field { flex:1; }
  .set-chips { display:flex; flex-wrap:wrap; gap:8px; margin-top:8px; }
  .set-chip { padding:7px 13px; border-radius:18px; font-size:12px; color:var(--text2,#C8C8D0);
    cursor:pointer; background:rgba(255,255,255,.03);
    box-shadow:inset 0 0 0 1px rgba(200,200,208,.14); transition:.16s; }
  .set-chip:hover { color:#fff; box-shadow:inset 0 0 0 1px var(--silver,#C8C8D0); }
  .set-chip.sel { color:#0A0A0A; background:linear-gradient(135deg,#ECECF2,#B0B0B8);
    box-shadow:none; font-weight:600; }
  /* browser picker */
  .set-bwrap { display:flex; flex-wrap:wrap; gap:8px; margin-top:4px; }
  .set-bchip { display:flex; align-items:center; gap:9px; padding:9px 13px 9px 10px;
    border-radius:12px; cursor:pointer; font-size:12.5px; color:var(--text2,#C8C8D0);
    background:rgba(255,255,255,.03); box-shadow:inset 0 0 0 1px rgba(200,200,208,.14);
    transition:.16s; }
  .set-bchip:hover { color:#fff; box-shadow:inset 0 0 0 1px var(--silver,#C8C8D0); }
  .set-bchip .bg { flex:none; width:26px; height:26px; border-radius:8px; display:grid;
    place-items:center; font-family:'Doto'; font-weight:700; font-size:13px;
    color:var(--silver-hi,#ECECF2); background:linear-gradient(135deg,#2a2a32,#141419);
    box-shadow:inset 0 1px 0 rgba(255,255,255,.06); }
  .set-bchip.sel { color:#0A0A0A; background:linear-gradient(135deg,#ECECF2,#B0B0B8);
    box-shadow:0 6px 16px rgba(200,200,208,.16); font-weight:600; }
  .set-bchip.sel .bg { background:rgba(10,10,10,.14); color:#0A0A0A; box-shadow:none; }
  .set-bchip.sel .set-badge { color:#0A0A0A; box-shadow:inset 0 0 0 1px rgba(10,10,10,.3); }

  /* ══════════════════════════════════════════════════════════════════════════
     TITLE-BAR + CREST REFINEMENTS (founder pass, 2026-07)
     The 'AETHELARK' wordmark moves off the title bar and onto the crest; the
     CASUAL/HARDCORE toggle takes its old spot beside the eagle mark; the clock
     is real-time and sits clean on the background (no pill).
     ══════════════════════════════════════════════════════════════════════ */
  /* Wordmark leaves the title bar (re-homed on the crest, below). */
  .strip .brand .sig.word { display:none !important; }
  /* Toggle slides right up against the eagle mark, where the name used to be. */
  .strip .brand .modeseg { margin-left:4px !important; }

  /* Clock + date: no rounded button — clean on the background. Date a touch
     bigger, time real-time (driven by the script below). */
  .strip .chrono { background:none !important; box-shadow:none !important;
    padding:0 !important; gap:16px !important; }
  .strip .chrono .t { font-size:17px !important; }
  .strip .chrono .d { font-size:11px !important; color:var(--text2,#C8C8D0) !important; }

  /* The rotating sweep around the eagle — a smooth silver arc that fades in and
     out gradually (was a hard conic edge), softened with a feathered ring mask
     and a whisper of blur so it reads as light, not a white line. */
  .core .sweep {
    inset:5px !important;
    background:conic-gradient(from 0deg,
        rgba(226,226,234,0)    0deg,
        rgba(226,226,234,.03)  55deg,
        rgba(226,226,234,.12) 140deg,
        rgba(240,240,246,.30) 205deg,
        rgba(226,226,234,.12) 270deg,
        rgba(226,226,234,.03) 320deg,
        rgba(226,226,234,0)   360deg) !important;
    -webkit-mask:radial-gradient(circle, transparent 43%, #000 49%, #000 91%, transparent 99%) !important;
            mask:radial-gradient(circle, transparent 43%, #000 49%, #000 91%, transparent 99%) !important;
    filter:blur(1.3px) !important;
    animation:spin 9s linear infinite !important;
    opacity:.95 !important;
  }
  /* Calm the dashed spinning ring — it was reading as a hard white dashed line. */
  .core .ring.r3 { border-style:solid !important;
    border-color:rgba(200,200,208,.07) !important;
    animation:spin 64s linear infinite !important; }

  /* AETHELARK, in Doto, resting right above the eagle (injected by the script). */
  .core-wrap .crest-word { position:absolute; top:22%; left:0; right:0;
    text-align:center; z-index:6; pointer-events:none;
    font-family:'Doto'; font-weight:800; font-size:15px;
    letter-spacing:.5em; padding-left:.5em; color:var(--text,#E5E5EA);
    text-shadow:0 1px 5px rgba(0,0,0,.92); opacity:.95; }

  /* CPU / MEM / GPU (casual) + CPU / TASKS / ELAPSED (hardcore) tiles — a little
     bigger and easier to read at a glance, in BOTH modes so they stay matched. */
  .minibar { padding:11px 6px !important; border-radius:10px !important; }
  .minibar .v { font-size:15px !important; }
  .minibar .k { font-size:8.5px !important; margin-top:3px !important;
    letter-spacing:.16em !important; }

  /* ---- Aesthetic picker -------------------------------------------------
     Reads as part of the tech-noir shell: same silver-on-near-black, same
     hairline borders. The one saturated colour in the whole panel is the
     selected chip, so what you've chosen is readable at a glance across
     seven rows without hunting. */
  #ae-modal { position:fixed; inset:0; z-index:9999; display:flex;
    align-items:center; justify-content:center; background:rgba(4,6,9,.72);
    backdrop-filter:blur(6px); }
  #ae-modal .ae-card { width:min(560px,88vw); max-height:86vh; overflow-y:auto;
    background:#0d1014; border:1px solid #222a33; border-radius:14px;
    padding:22px 24px; box-shadow:0 24px 70px rgba(0,0,0,.6);
    font-family:inherit; color:#c8cfd6; }
  #ae-modal .ae-head { margin-bottom:16px; }
  #ae-modal .ae-title { font-size:17px; letter-spacing:.01em; color:#e6ebf0; }
  #ae-modal .ae-sub { font-size:11.5px; color:#6b7684; margin-top:4px; }
  #ae-modal .ae-row { display:flex; align-items:center; gap:12px;
    padding:7px 0; border-bottom:1px solid #171d24; }
  #ae-modal .ae-label { flex:0 0 74px; font-size:9.5px; letter-spacing:.16em;
    text-transform:uppercase; color:#6b7684; }
  #ae-modal .ae-chips { display:flex; flex-wrap:wrap; gap:6px; }
  #ae-modal .ae-chip { background:#141a21; border:1px solid #232b35;
    color:#9aa5b1; border-radius:999px; padding:5px 12px; font-size:11.5px;
    cursor:pointer; transition:all .13s ease; font-family:inherit; }
  #ae-modal .ae-chip:hover { border-color:#3a4553; color:#c8cfd6; }
  #ae-modal .ae-chip.on { background:#e0a33e; border-color:#e0a33e;
    color:#151a21; font-weight:600; }
  #ae-modal .ae-chip:focus-visible { outline:2px solid #e0a33e;
    outline-offset:2px; }
  #ae-modal .ae-free { width:100%; margin-top:16px; background:#141a21;
    border:1px solid #232b35; border-radius:8px; padding:10px 12px;
    color:#c8cfd6; font-size:12px; font-family:inherit; box-sizing:border-box; }
  #ae-modal .ae-free::placeholder { color:#5a6472; }
  #ae-modal .ae-foot { display:flex; justify-content:flex-end; gap:8px;
    margin-top:18px; }
  #ae-modal .ae-btn { border-radius:8px; padding:8px 15px; font-size:11.5px;
    cursor:pointer; font-family:inherit; border:1px solid #2a333e;
    background:transparent; color:#9aa5b1; }
  #ae-modal .ae-btn:hover { border-color:#3a4553; color:#c8cfd6; }
  #ae-modal .ae-save { background:#e0a33e; border-color:#e0a33e;
    color:#151a21; font-weight:600; }
  #ae-modal .ae-btn:focus-visible { outline:2px solid #e0a33e;
    outline-offset:2px; }
  @media (prefers-reduced-motion: reduce) {
    #ae-modal .ae-chip { transition:none; }
  }
</style>
"""

# qwebchannel.js is served by Qt at this qrc path inside QWebEngine.
BRIDGE = """
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<script>
(function () {
  window.aethelark = window.aethelark || {};

  // Stop the artifact's starfield RAF loop — the desktop is the background now
  // (also saves CPU, which helps voice latency).
  try { var _r = requestAnimationFrame(function(){}); while (_r--) cancelAnimationFrame(_r); } catch (e) {}

  // ---- API the daemon calls to drive the UI (per message contract) ----
  function esc(s){ return String(s==null?'':s).replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];}); }

  window.aethelark.setMode = function (m) { try { setMode(m === 'hardcore' ? 'swarm' : 'rest'); } catch (e) {} };

  // Expand ('in') / collapse ('out') morph — a compositor-only transform on the
  // card (no window resize → no Chromium reflow). Restarting requires clearing
  // the class and forcing a reflow before re-adding it.
  window.aethelark.playMorph = function (dir) {
    var app = document.querySelector('.app'); if (!app) return;
    app.classList.remove('ae-in', 'ae-out');
    void app.offsetWidth;  // force reflow so the animation re-triggers
    app.classList.add(dir === 'out' ? 'ae-out' : 'ae-in');
  };

  window.aethelark.setState = function (s) {
    s = String(s || '').toUpperCase();
    var lb = document.querySelector('.state .lb'), dot = document.querySelector('.state .dot');
    if (lb) lb.textContent = s;
    var col = s === 'LISTENING' ? 'var(--green)' : s === 'SPEAKING' ? '#3B82F6'
            : s === 'MUTED' ? 'var(--red)' : 'var(--silver)';
    if (dot) { dot.style.background = col; dot.style.boxShadow = '0 0 10px ' + col; }
    // Reflect mute on the MIC button itself (was previously never updated).
    var muted = (s === 'MUTED');
    document.querySelectorAll('.opbtn.mic').forEach(function (b) {
      b.classList.toggle('muted', muted);
      var etch = b.querySelector('.etch');
      if (etch) etch.textContent = muted ? 'MICROPHONE MUTED' : 'MICROPHONE ACTIVE';
    });
  };

  window.aethelark.setMemory = function (list) {
    var box = document.querySelector('.memcard'); if (!box) return;
    if (!list || !list.length) {
      box.innerHTML = '<div style="font-size:11px;color:var(--muted);line-height:1.6">'
        + 'Getting to know you — I\\'ll remember what matters as we talk.</div>'; return;
    }
    box.innerHTML = list.map(function (m) {
      return '<div class="mempill"><div class="ic">' + esc(m.icon || '\\u25c8') + '</div>'
        + '<div><div class="k">' + esc(m.label) + '</div><div class="v">' + esc(m.value) + '</div></div></div>';
    }).join('');
  };

  window.aethelark.setLog = function (lines) {
    var box = document.querySelector('.log'); if (!box) return;
    // Stick to the bottom only if the user is already near it — so scrolling up
    // to read history is not yanked back down when a new line arrives.
    var atBottom = (box.scrollHeight - box.scrollTop - box.clientHeight) < 40;
    box.innerHTML = (lines || []).map(function (l) {
      var sp = (l.speaker || 'sys').toLowerCase();
      var cls = sp === 'you' ? ' you' : sp === 'ae' ? ' ae' : '';
      var tag = sp === 'you' ? 'YOU' : sp === 'ae' ? 'AE' : sp === 'net' ? 'NET' : sp === 'swarm' ? 'SWM' : 'SYS';
      return '<div class="logline' + cls + '"><span class="tag ' + sp + '">' + tag + '</span>'
        + '<span class="msg">' + esc(l.text) + '</span></div>';
    }).join('');
    if (atBottom) box.scrollTop = box.scrollHeight;
  };

  window.aethelark.setMetrics = function (m) {
    m = m || {}; var vals = [m.cpu, m.mem, m.gpu];
    document.querySelectorAll('#resting .minibars .minibar .v').forEach(function (t, i) {
      if (vals[i] != null) t.textContent = vals[i];
    });
  };

  window.aethelark.setSwarm = function (d) {
    d = d || {};
    if (d.mission) {
      var mm = d.mission, order = [mm.repo, mm.worktrees, mm.merged, mm.conflicts];
      document.querySelectorAll('#swarm .swrow .v').forEach(function (r, i) { if (order[i] != null) r.textContent = order[i]; });
      var fill = document.querySelector('#swarm .gauge .fill'); if (fill && mm.progress != null) fill.style.width = mm.progress + '%';
      var tv = [mm.cpu, mm.tasks, mm.elapsed];
      document.querySelectorAll('#swarm .minibars .minibar .v').forEach(function (t, i) { if (tv[i] != null) t.textContent = tv[i]; });
      // Conductor header — live agent count · repo, and the conductor's state.
      var cs = document.querySelector('#swarm .conductor .txt .s');
      if (cs && mm.conductor != null) cs.textContent = mm.conductor;
      var ct = document.querySelector('#swarm .conductor .txt .sig.t');
      if (ct && mm.state != null) ct.textContent = mm.state;
    }
    if (d.agents) {
      var badge = { work: 'WORKING', review: 'IN REVIEW', block: 'NEEDS YOU', idle: 'STANDBY' };
      document.querySelectorAll('#swarm .lane').forEach(function (lane, i) {
        var a = d.agents[i];
        if (!a) { lane.style.display = 'none'; return; }
        lane.style.display = ''; lane.className = 'lane ' + (a.lane || a.status || 'work');
        var q = function (s) { return lane.querySelector(s); };
        if (q('.glyph')) q('.glyph').textContent = a.glyph || '\\u2022';
        if (q('.nm')) q('.nm').textContent = a.name || '';
        if (q('.br')) q('.br').textContent = a.branch || '';
        if (q('.st')) q('.st').textContent = a.badge || badge[a.status] || '';
        if (q('.think')) q('.think').textContent = a.thought || '';
        if (q('.meta')) {
          var parts = '';
          if (a.adds != null || a.dels != null)
            parts += '<span><b class="add">+' + (a.adds || 0) + '</b> <b class="del">\\u2212' + (a.dels || 0) + '</b></span>';
          if (a.file) parts += '<span class="file">' + esc(a.file) + '</span>';
          if (a.elapsed) parts += '<span>\\u23f1 ' + esc(a.elapsed) + '</span>';
          q('.meta').innerHTML = parts;
        }
      });
    }
    if (d.timeline) {
      var tl = document.querySelector('#swarm .tl');
      if (tl) tl.innerHTML = d.timeline.map(function (e) {
        return '<div class="tlrow' + (e.done ? ' done' : '') + '"><span class="ts">' + esc(e.ts) + '</span>'
          + '<span class="tx">' + esc(e.text) + '</span></div>';
      }).join('');
    }
  };

  /* ---- Aesthetic picker ------------------------------------------------
     Seven rows of four plain words. Tapping chips takes about fifteen seconds
     and is mildly enjoyable; being ASKED the same seven questions out loud is
     an interrogation. Same information, opposite feeling — which is the whole
     reason this exists instead of a longer voice script.
     Everything here is optional: skip it and the eagle asks ONE question. */
  var AE_CHOICES = {};

  function openAesthetics() {
    if (document.getElementById('ae-modal') || !window.pybridge) return;
    window.pybridge.aesthetic_options(function (raw) {
      var sections = [];
      try { sections = JSON.parse(raw); } catch (e) { return; }

      var wrap = document.createElement('div');
      wrap.id = 'ae-modal';
      wrap.innerHTML =
        '<div class="ae-card">' +
          '<div class="ae-head">' +
            '<div class="ae-title">How should it look?</div>' +
            '<div class="ae-sub">Tap what you like. Skip anything you have no view on.</div>' +
          '</div>' +
          '<div class="ae-rows"></div>' +
          '<input class="ae-free" placeholder="…or just describe it in your own words">' +
          '<div class="ae-foot">' +
            '<button class="ae-btn ae-skip">Skip — you pick</button>' +
            '<button class="ae-btn ae-save">Use this look</button>' +
          '</div>' +
        '</div>';
      document.body.appendChild(wrap);

      var rows = wrap.querySelector('.ae-rows');
      sections.forEach(function (sec) {
        var row = document.createElement('div');
        row.className = 'ae-row';
        var lab = document.createElement('div');
        lab.className = 'ae-label';
        lab.textContent = sec.label;
        row.appendChild(lab);
        var chips = document.createElement('div');
        chips.className = 'ae-chips';
        sec.words.forEach(function (w) {
          var c = document.createElement('button');
          c.className = 'ae-chip';
          c.textContent = w;
          c.onclick = function () {
            /* tapping the active chip clears it — undoing a choice must be as
               easy as making one, or people just abandon the picker */
            if (AE_CHOICES[sec.key] === w) {
              delete AE_CHOICES[sec.key];
              c.classList.remove('on');
            } else {
              AE_CHOICES[sec.key] = w;
              chips.querySelectorAll('.ae-chip').forEach(function (o) {
                o.classList.remove('on');
              });
              c.classList.add('on');
            }
          };
          chips.appendChild(c);
        });
        row.appendChild(chips);
        rows.appendChild(row);
      });

      function close() { wrap.remove(); }
      wrap.querySelector('.ae-skip').onclick = function () {
        AE_CHOICES = {};
        window.pybridge.set_aesthetic('', '', function () {});
        close();
      };
      wrap.querySelector('.ae-save').onclick = function () {
        var free = wrap.querySelector('.ae-free').value || '';
        window.pybridge.set_aesthetic(JSON.stringify(AE_CHOICES), free, function () {});
        close();
      };
      wrap.addEventListener('click', function (e) { if (e.target === wrap) close(); });
    });
  }
  window.aethelark.openAesthetics = openAesthetics;

  function wireButtons() {
    document.querySelectorAll('.wbtn.cl').forEach(function (b) {
      b.onclick = function () { if (window.pybridge) window.pybridge.collapse(); };
    });
    document.querySelectorAll('.wbtn.close').forEach(function (b) {
      b.onclick = function () { if (window.pybridge) window.pybridge.quit(); };
    });
    var wc = document.querySelector('.wctrl');
    if (wc && wc.firstElementChild) {
      wc.firstElementChild.onclick = function () { if (window.pybridge) window.pybridge.minimize(); };
    }
  }

  function wireAestheticEntry() {
    /* Live alongside the other suggestion chips rather than in a settings
       panel: taste is part of asking for the thing, not configuration. */
    var bar = document.querySelector('.saybar');
    if (!bar || document.getElementById('ae-open')) return;
    var c = document.createElement('div');
    c.className = 'chip';
    c.id = 'ae-open';
    c.setAttribute('role', 'button');
    c.setAttribute('tabindex', '0');
    c.innerHTML = '&#9670;&nbsp; Choose a look for my next build';
    c.onclick = openAesthetics;
    c.onkeydown = function (e) {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openAesthetics(); }
    };
    bar.insertBefore(c, bar.firstChild);
  }

  function wireActions() {
    var inp = document.querySelector('.cmd input');
    if (inp) inp.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && inp.value.trim() && window.pybridge) {
        window.pybridge.send_command(inp.value.trim()); inp.value = '';
      }
    });
    document.querySelectorAll('.chip').forEach(function (c) {
      c.addEventListener('click', function () { if (window.pybridge) window.pybridge.send_command(c.textContent.trim()); });
    });
    var sr = document.getElementById('seg-rest'), ss = document.getElementById('seg-swarm');
    if (sr) sr.addEventListener('click', function () { if (window.pybridge) window.pybridge.set_mode('casual'); });
    if (ss) ss.addEventListener('click', function () { if (window.pybridge) window.pybridge.set_mode('hardcore'); });
    // INTERJECT · HALT SWARM (hardcore) → halt; ESC anywhere → interrupt
    document.querySelectorAll('.opbtn.interrupt').forEach(function (b) {
      b.addEventListener('click', function () { if (window.pybridge) window.pybridge.halt_swarm(); });
    });
    document.querySelectorAll('.opbtn.mic').forEach(function (b) {
      b.addEventListener('click', function () { if (window.pybridge) window.pybridge.toggle_mute(); });
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && window.pybridge) window.pybridge.interrupt();
    });

    // Drag the whole dashboard window from any non-interactive area. Text/scroll
    // regions (.log chat, .tl timeline, .memcard memory) are EXCLUDED so you can
    // select & copy their text instead of dragging the window.
    var DRAG_SKIP = 'button,input,textarea,select,a,.chip,.wbtn,.stud,.modeseg,.opbtn,.cmd,.searchbar,.log,.tl,.memcard,#ae-modal,.ae-chip,.ae-btn,.ae-free,[onclick]';
    var ddrag = false;
    document.addEventListener('mousedown', function (e) {
      if (e.button !== 0 || (e.target.closest && e.target.closest(DRAG_SKIP))) return;
      // Also don't start a drag if the user is actively selecting text.
      if (e.target.closest && e.target.closest('.log,.tl,.memcard')) return;
      ddrag = true;
      if (window.pybridge) window.pybridge.begin_drag(e.screenX, e.screenY);
    });
    document.addEventListener('mousemove', function (e) {
      if (ddrag && window.pybridge) window.pybridge.drag_to(e.screenX, e.screenY);
    });
    document.addEventListener('mouseup', function () { ddrag = false; });
  }

  wireButtons();  // safe even before the channel connects

  if (typeof QWebChannel !== 'undefined' && typeof qt !== 'undefined') {
    new QWebChannel(qt.webChannelTransport, function (channel) {
      window.pybridge = channel.objects.pybridge;
      wireButtons();
      wireActions();
      wireAestheticEntry();
      if (window.pybridge && window.pybridge.ready) window.pybridge.ready();
    });
  }
})();
</script>
"""

# The Settings sheet — self-contained: builds its own DOM, wires the title-bar
# gear, and exposes window.aethelark.setSettings(snapshot) for the daemon to fill.
SETTINGS = """
<script>
(function () {
  var built = false, panel = null, scrim = null;
  function pb() { return window.pybridge; }

  var SHELL =
    '<div id="set-panel">'
    + '<div class="set-hd"><span class="ttl">SETTINGS</span>'
    +   '<div class="x" id="set-close" title="Close">\\u2715</div></div>'
    + '<div class="set-body">'

    + '<div class="set-sec"><div class="lbl">Connected accounts</div>'
    +   '<div class="set-row"><div class="ic">G</div><div class="tt">'
    +     '<div class="t">Google <span class="set-badge off" id="set-g-badge">NOT LINKED</span></div>'
    +     '<div class="d" id="set-g-d">Inbox briefings & calendar need your account.</div></div>'
    +     '<button class="set-mini" id="set-g-btn">Connect</button></div>'
    +   '<div class="set-row"><div class="ic">W</div><div class="tt">'
    +     '<div class="t">WhatsApp</div>'
    +     '<div class="d">Link via QR to send messages by voice.</div></div>'
    +     '<button class="set-mini" id="set-wa-btn">Link</button></div></div>'

    // The eagle's browser is separate from the user's Chrome ON PURPOSE, and
    // that was invisible everywhere: "how do I log in?" had no answer in the
    // interface, only a voice command you had to know to say. This row shows
    // which sites it can already use and opens a real window to add one.
    + '<div class="set-sec"><div class="lbl">Sites the eagle can use</div>'
    +   '<div class="set-row"><div class="ic">\u25C9</div><div class="tt">'
    +     '<div class="t">Signed in <span class="set-badge off" id="set-br-badge">NONE</span></div>'
    +     '<div class="d" id="set-br-d">The eagle browses in its own window, so being '
    +       'logged in on Chrome does not sign it in.</div></div>'
    +     '<button class="set-mini" id="set-br-btn">Sign in\u2026</button></div></div>'

    + '<div class="set-sec"><div class="lbl">Startup</div>'
    +   '<div class="set-row"><div class="ic">\\u23fb</div><div class="tt">'
    +     '<div class="t">Start on boot</div>'
    +     '<div class="d" id="set-boot-d">Wake the eagle when your computer starts.</div></div>'
    +     '<div class="set-sw" id="set-boot-sw"></div></div></div>'

    + '<div class="set-sec"><div class="lbl">Identity</div>'
    +   '<div class="set-row set-stack">'
    +     '<div class="t">The eagle\\'s name</div>'
    +     '<input class="set-field" id="set-asst" placeholder="Aethelark">'
    +     '<div class="t" style="margin-top:14px">It should call you</div>'
    +     '<input class="set-field" id="set-addr" placeholder="e.g. Sir, Boss, Shenny">'
    +     '<div class="set-inline"><button class="set-mini solid" id="set-id-save" style="flex:1">Save identity</button></div>'
    +   '</div></div>'

    + '<div class="set-sec"><div class="lbl">Brain</div>'
    +   '<div class="set-row set-stack">'
    +     '<div class="t">Where the eagle thinks</div>'
    +     '<div class="set-chips" id="set-mode">'
    +       '<span class="set-chip" data-mode="local">Local</span>'
    +       '<span class="set-chip" data-mode="api">Cloud</span></div>'
    +     '<div id="set-api-wrap">'
    +       '<div class="t" style="margin-top:14px">Provider</div>'
    +       '<div class="set-chips" id="set-prov">'
    +         '<span class="set-chip" data-prov="google">Google</span>'
    +         '<span class="set-chip" data-prov="anthropic">Anthropic</span>'
    +         '<span class="set-chip" data-prov="openai">OpenAI</span>'
    +         '<span class="set-chip" data-prov="other">Other</span></div>'
    +       '<div class="t" style="margin-top:14px">API key <span class="set-badge off" id="set-key-badge">NONE</span></div>'
    +       '<div class="set-inline"><input class="set-field" id="set-key" type="password" placeholder="Paste a new key to replace">'
    +         '<button class="set-mini solid" id="set-key-save">Update</button></div>'
    +     '</div>'
    +   '</div></div>'

    + '<div class="set-sec"><div class="lbl">Preferences</div>'
    +   '<div class="set-row"><div class="ic">\\u2600</div><div class="tt">'
    +     '<div class="t">Morning brief</div>'
    +     '<div class="d">A spoken summary to start the day.</div></div>'
    +     '<div class="set-sw" id="set-brief-sw"></div></div>'
    +   '<div class="set-row set-stack"><div class="t">Browser for logins & automation</div>'
    +     '<div class="set-bwrap" id="set-browsers"></div>'
    +     '<div class="d" id="set-browser-hint" style="margin-top:9px"></div></div></div>'

    + '<div class="set-sec"><div class="lbl">Session</div>'
    +   '<div class="set-row"><div class="ic">\\u21bb</div><div class="tt">'
    +     '<div class="t">Re-run setup</div><div class="d">Walk the ignition flow again.</div></div>'
    +     '<button class="set-mini" id="set-reonboard">Open</button></div>'
    +   '<div class="set-row"><div class="ic">\\u23fb</div><div class="tt">'
    +     '<div class="t">Quit Aethelark</div><div class="d">Shut the eagle down.</div></div>'
    +     '<button class="set-mini danger" id="set-quit">Quit</button></div></div>'

    + '</div></div>';

  var st = { mode: 'api', provider: 'google' };

  function setVal(id, v) {
    var el = document.getElementById(id);
    if (el && document.activeElement !== el) el.value = v == null ? '' : v;
  }
  function selChips(container, attr, val) {
    document.querySelectorAll('#' + container + ' .set-chip').forEach(function (c) {
      c.classList.toggle('sel', c.dataset[attr] === val);
    });
  }

  window.aethelark = window.aethelark || {};
  window.aethelark.setSettings = function (s) {
    if (!built) return; s = s || {};
    var g = (s.accounts || {}).google || {};
    var gb = document.getElementById('set-g-badge'),
        gd = document.getElementById('set-g-d'),
        gbtn = document.getElementById('set-g-btn');
    if (g.connected && g.needs_reconnect) {
      // Connected, and missing the scope the user is about to need. A green
      // LINKED badge here is true and misleading — they would only find out
      // by asking for a YouTube thing and being told to reconnect.
      gb.textContent = 'PARTIAL'; gb.className = 'set-badge off';
      gd.textContent = (g.email || 'Account connected') + ' \u2014 YouTube not granted yet';
      gbtn.textContent = 'Reconnect'; gbtn.className = 'set-mini solid'; gbtn.dataset.act = 'conn';
    } else if (g.connected) {
      gb.textContent = 'LINKED'; gb.className = 'set-badge';
      gd.textContent = g.email || g.name || 'Account connected';
      gbtn.textContent = 'Disconnect'; gbtn.className = 'set-mini danger'; gbtn.dataset.act = 'disc';
    } else if (g.configured === false) {
      // OAuth client id not set yet — be honest instead of a dead 'Connect'.
      gb.textContent = 'SETUP NEEDED'; gb.className = 'set-badge off';
      gd.textContent = 'Add a Google client ID to config to enable sign-in.';
      gbtn.textContent = 'Connect'; gbtn.className = 'set-mini'; gbtn.dataset.act = 'conn';
    } else {
      gb.textContent = 'NOT LINKED'; gb.className = 'set-badge off';
      gd.textContent = 'Inbox briefings & calendar need your account.';
      gbtn.textContent = 'Connect'; gbtn.className = 'set-mini'; gbtn.dataset.act = 'conn';
    }

    var sites = ((s.accounts || {}).browser || {}).sites || [];
    var brb = document.getElementById('set-br-badge'),
        brd = document.getElementById('set-br-d');
    if (brb) {
      if (sites.length) {
        brb.textContent = String(sites.length); brb.className = 'set-badge';
        // Named, not counted: "3 sites" tells the user nothing they can act on.
        brd.textContent = sites.join(' \u00B7 ');
      } else {
        brb.textContent = 'NONE'; brb.className = 'set-badge off';
        brd.textContent = 'The eagle browses in its own window, so being logged '
                        + 'in on Chrome does not sign it in.';
      }
    }

    var startup = s.startup || {};
    var bsw = document.getElementById('set-boot-sw');
    bsw.classList.toggle('on', !!startup.boot_enabled);
    if (startup.boot_supported === false) {
      bsw.classList.add('disabled');
      document.getElementById('set-boot-d').textContent = 'Not supported on this system.';
    } else { bsw.classList.remove('disabled'); }

    var id = s.identity || {};
    setVal('set-asst', id.assistant_name || '');
    setVal('set-addr', id.user_name || '');

    var brain = s.brain || {};
    st.mode = brain.mode || 'api'; st.provider = brain.provider || 'google';
    selChips('set-mode', 'mode', st.mode);
    selChips('set-prov', 'prov', st.provider);
    document.getElementById('set-api-wrap').style.display = st.mode === 'local' ? 'none' : '';
    var kb = document.getElementById('set-key-badge');
    if (brain.has_key) { kb.textContent = 'SET'; kb.className = 'set-badge'; }
    else { kb.textContent = 'NONE'; kb.className = 'set-badge off'; }

    var pref = s.preferences || {};
    document.getElementById('set-brief-sw').classList.toggle('on', !!pref.morning_brief_enabled);

    // Browser picker — click an installed browser instead of typing a name.
    var picker = document.getElementById('set-browsers');
    var list = s.browsers || [];
    var sel = (pref.default_browser || '').toLowerCase();
    if (!sel) { var d = list.filter(function (b) { return b.is_default; })[0]; sel = d ? d.id : ''; }
    picker.innerHTML = list.length ? list.map(function (b) {
      return '<div class="set-bchip' + (b.id === sel ? ' sel' : '') + '" data-bid="' + b.id + '">'
        + '<span class="bg">' + esc(b.glyph) + '</span><span class="bn">' + esc(b.name) + '</span>'
        + (b.is_default ? '<span class="set-badge">DEFAULT</span>' : '')
        + (b.is_snap ? '<span class="set-badge off">SNAP</span>' : '') + '</div>';
    }).join('') : '<div class="d">No browsers detected on this machine.</div>';
    var chosen = list.filter(function (b) { return b.id === sel; })[0];
    var hint = document.getElementById('set-browser-hint');
    hint.textContent = (chosen && chosen.is_snap)
      ? 'Snap browsers work, but a native install (e.g. Google Chrome) links accounts more reliably.'
      : '';
  };

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]; }); }

  function saveIdentity() {
    if (!pb()) return;
    pb().save_settings(JSON.stringify({
      assistant_name: (document.getElementById('set-asst').value || '').trim(),
      user_name: (document.getElementById('set-addr').value || '').trim(),
      address_style: 'custom'
    }));
  }

  function wire() {
    document.getElementById('set-close').onclick = close;
    scrim.addEventListener('click', function (e) { if (e.target === scrim) close(); });

    var brBtn = document.getElementById('set-br-btn');
    if (brBtn) brBtn.onclick = function () {
      var site = window.prompt(
        'Which site should the eagle sign in to?\\n\\n'
        + 'A window opens, you log in once, and it stays signed in.',
        'youtube.com');
      if (site && pb() && pb().browser_sign_in) pb().browser_sign_in(site.trim());
    };

    document.getElementById('set-g-btn').onclick = function () {
      if (!pb()) return;
      if (this.dataset.act === 'disc') pb().disconnect_google();
      else { this.textContent = 'Opening\\u2026'; pb().connect_google(); }
    };
    document.getElementById('set-wa-btn').onclick = function () {
      if (!pb()) return;
      var btn = this; btn.textContent = 'Opening\\u2026'; pb().link_whatsapp();
      // No reliable "done" signal (QR scan is user-paced) — restore the label
      // after the browser has had time to open so it isn't stuck on 'Opening…'.
      setTimeout(function () { btn.textContent = 'Link'; }, 4000);
    };
    document.getElementById('set-boot-sw').onclick = function () {
      if (this.classList.contains('disabled') || !pb()) return;
      pb().set_autostart(!this.classList.contains('on'));
    };
    document.getElementById('set-id-save').onclick = saveIdentity;
    document.getElementById('set-asst').addEventListener('keydown', function (e) { if (e.key === 'Enter') saveIdentity(); });
    document.getElementById('set-addr').addEventListener('keydown', function (e) { if (e.key === 'Enter') saveIdentity(); });

    document.getElementById('set-mode').addEventListener('click', function (e) {
      var c = e.target.closest('.set-chip'); if (!c || !pb()) return;
      st.mode = c.dataset.mode; selChips('set-mode', 'mode', st.mode);
      document.getElementById('set-api-wrap').style.display = st.mode === 'local' ? 'none' : '';
      pb().save_settings(JSON.stringify({ brain_mode: st.mode }));
    });
    document.getElementById('set-prov').addEventListener('click', function (e) {
      var c = e.target.closest('.set-chip'); if (!c || !pb()) return;
      st.provider = c.dataset.prov; selChips('set-prov', 'prov', st.provider);
      pb().save_settings(JSON.stringify({ brain_provider: st.provider }));
    });
    document.getElementById('set-key-save').onclick = function () {
      var f = document.getElementById('set-key'), v = (f.value || '').trim();
      if (v && pb()) { pb().set_brain_key(st.provider, v); f.value = ''; }
    };

    document.getElementById('set-brief-sw').onclick = function () {
      if (pb()) pb().save_settings(JSON.stringify({ morning_brief_enabled: !this.classList.contains('on') }));
    };
    document.getElementById('set-browsers').addEventListener('click', function (e) {
      var c = e.target.closest('.set-bchip'); if (!c || !pb()) return;
      pb().save_settings(JSON.stringify({ default_browser: c.dataset.bid }));
    });
    document.getElementById('set-reonboard').onclick = function () { if (pb()) { pb().rerun_onboarding(); close(); } };
    document.getElementById('set-quit').onclick = function () { if (pb()) pb().quit(); };
  }

  function build() {
    if (built) return;
    scrim = document.createElement('div'); scrim.id = 'set-scrim'; scrim.innerHTML = SHELL;
    var host = document.querySelector('.app') || document.body;
    if (getComputedStyle(host).position === 'static') host.style.position = 'relative';
    host.appendChild(scrim);
    panel = document.getElementById('set-panel');
    // Swallow mousedown inside the sheet so the dashboard's drag-from-anywhere
    // handler (on document) never drags the window while using Settings.
    scrim.addEventListener('mousedown', function (e) { e.stopPropagation(); });
    built = true; wire();
  }
  function open() {
    build();
    scrim.classList.add('open');
    if (pb() && pb().open_settings) pb().open_settings();
  }
  function close() { if (scrim) scrim.classList.remove('open'); }

  function attachGear() {
    var g = document.querySelector('.stud');
    if (g) { g.style.cursor = 'pointer'; g.title = 'Settings'; g.onclick = open; }
  }
  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', attachGear);
  else attachGear();
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && scrim && scrim.classList.contains('open')) { e.stopPropagation(); close(); }
  }, true);
})();
</script>
"""

# Live clock + the AETHELARK wordmark re-homed onto the crest. Kept separate so
# the artifact source stays untouched and these run after the DOM is present.
EXTRAS = """
<script>
(function () {
  var DOW = ['SUN','MON','TUE','WED','THU','FRI','SAT'];
  var MON = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
  function p(n){ return n < 10 ? '0' + n : '' + n; }

  // ── Real-time clock (the artifact had a hardcoded time/date) ──────────────
  function tickClock() {
    var t = document.querySelector('.strip .chrono .t');
    var d = document.querySelector('.strip .chrono .d');
    if (!t || !d) return;
    var n = new Date();
    t.textContent = p(n.getHours()) + ':' + p(n.getMinutes()) + ':' + p(n.getSeconds());
    d.textContent = DOW[n.getDay()] + ' ' + n.getDate() + ' ' + MON[n.getMonth()] + ' ' + n.getFullYear();
  }

  // ── Move the AETHELARK wordmark onto the crest, above the eagle ────────────
  function placeWordmark() {
    var wrap = document.querySelector('.stage .core-wrap');
    if (!wrap || wrap.querySelector('.crest-word')) return;
    var w = document.createElement('div');
    w.className = 'crest-word';
    w.textContent = 'AETHELARK';
    wrap.appendChild(w);
  }

  function start() { placeWordmark(); tickClock(); setInterval(tickClock, 1000); }
  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', start);
  else start();
})();
</script>
"""

out = src.rstrip() + "\n" + OVERRIDE_STYLE + BRIDGE + SETTINGS + EXTRAS + "\n"
(HERE / "dashboard.html").write_text(out, encoding="utf-8")
print("wrote web/dashboard.html", len(out), "bytes")
