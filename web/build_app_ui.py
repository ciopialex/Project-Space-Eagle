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
</style>
"""

# qwebchannel.js is served by Qt at this qrc path inside QWebEngine.
BRIDGE = """
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<script>
(function () {
  window.aethelark = window.aethelark || {};

  // ---- API the daemon calls to drive the UI (per message contract) ----
  function esc(s){ return String(s==null?'':s).replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];}); }

  window.aethelark.setMode = function (m) { try { setMode(m === 'hardcore' ? 'swarm' : 'rest'); } catch (e) {} };

  window.aethelark.setState = function (s) {
    s = String(s || '').toUpperCase();
    var lb = document.querySelector('.state .lb'), dot = document.querySelector('.state .dot');
    if (lb) lb.textContent = s;
    var col = s === 'LISTENING' ? 'var(--green)' : s === 'SPEAKING' ? '#3B82F6'
            : s === 'MUTED' ? 'var(--red)' : 'var(--silver)';
    if (dot) { dot.style.background = col; dot.style.boxShadow = '0 0 10px ' + col; }
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
    box.innerHTML = (lines || []).map(function (l) {
      var sp = (l.speaker || 'sys').toLowerCase();
      var cls = sp === 'you' ? ' you' : sp === 'ae' ? ' ae' : '';
      var tag = sp === 'you' ? 'YOU' : sp === 'ae' ? 'AE' : sp === 'net' ? 'NET' : sp === 'swarm' ? 'SWM' : 'SYS';
      return '<div class="logline' + cls + '"><span class="tag ' + sp + '">' + tag + '</span>'
        + '<span class="msg">' + esc(l.text) + '</span></div>';
    }).join('');
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
    }
    if (d.agents) {
      var badge = { work: 'WORKING', review: 'IN REVIEW', block: 'NEEDS YOU', idle: 'STANDBY' };
      document.querySelectorAll('#swarm .lane').forEach(function (lane, i) {
        var a = d.agents[i];
        if (!a) { lane.style.display = 'none'; return; }
        lane.style.display = ''; lane.className = 'lane ' + (a.status || 'work');
        var q = function (s) { return lane.querySelector(s); };
        if (q('.glyph')) q('.glyph').textContent = a.glyph || '\\u2022';
        if (q('.nm')) q('.nm').textContent = a.name || '';
        if (q('.br')) q('.br').textContent = a.branch || '';
        if (q('.st')) q('.st').textContent = badge[a.status] || '';
        if (q('.think')) q('.think').textContent = a.thought || '';
        if (q('.meta')) q('.meta').innerHTML = '<span><b class="add">+' + (a.adds || 0) + '</b> <b class="del">\\u2212' + (a.dels || 0) + '</b></span>'
          + (a.file ? '<span class="file">' + esc(a.file) + '</span>' : '')
          + (a.elapsed ? '<span>\\u23f1 ' + esc(a.elapsed) + '</span>' : '');
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
  }

  wireButtons();  // safe even before the channel connects

  if (typeof QWebChannel !== 'undefined' && typeof qt !== 'undefined') {
    new QWebChannel(qt.webChannelTransport, function (channel) {
      window.pybridge = channel.objects.pybridge;
      wireButtons();
      wireActions();
      if (window.pybridge && window.pybridge.ready) window.pybridge.ready();
    });
  }
})();
</script>
"""

out = src.rstrip() + "\n" + OVERRIDE_STYLE + BRIDGE + "\n"
(HERE / "dashboard.html").write_text(out, encoding="utf-8")
print("wrote web/dashboard.html", len(out), "bytes")
