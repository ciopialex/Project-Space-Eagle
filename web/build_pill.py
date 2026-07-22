"""Build web/pill.html — the Dynamic Island, rendered from the artifact's EXACT
pill CSS so it's pixel-identical to the mockup. Transparent background (only the
pill + its shadow paint); one pill that morphs between states via
`window.pill.set(state, data)`. No clock (per design call). Double-click →
window.pybridge.expand()."""
import base64
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
FONTS = HERE.parent / "assets" / "fonts"
b64 = lambda p: base64.b64encode(pathlib.Path(p).read_bytes()).decode()
doto, manrope = b64(FONTS / "Doto.ttf"), b64(FONTS / "Manrope-Variable.ttf")
eagle = b64(HERE.parent / "assets" / "images" / "eagle_white.png")
# tint the white emblem to silver at the source is done in-app; here the white
# PNG reads fine on the obsidian pill. Reuse the same asset the dashboard uses.

HTML = f"""<title>Aethelark — Dynamic Island</title>
<style>
@font-face{{font-family:'Doto';src:url(data:font/ttf;base64,{doto}) format('truetype');font-weight:100 900;font-display:block}}
@font-face{{font-family:'Manrope';src:url(data:font/ttf;base64,{manrope}) format('truetype');font-weight:200 800;font-display:block}}
:root{{--text2:#C8C8D0;--muted:#7C7C86;--silver:#C8C8D0;--silver-hi:#ECECF2;--green:#4ee08a;--amber:#ffb060}}
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{background:transparent !important;width:100%;height:100%;overflow:hidden;
  font-family:'Manrope',sans-serif;-webkit-font-smoothing:antialiased}}
.stage{{width:100vw;height:100vh;display:flex;align-items:flex-start;justify-content:center;padding-top:20px}}
.pill{{width:300px;height:62px;border-radius:31px;position:relative;overflow:hidden;display:flex;align-items:center;gap:14px;padding:0 22px;cursor:pointer;
  background:linear-gradient(180deg,#141419,#0a0a0d 55%,#000);
  box-shadow:0 16px 34px rgba(0,0,0,.7),inset 0 1px 0 rgba(255,255,255,.07),inset 0 0 22px rgba(0,0,0,.6)}}
.pill::after{{content:"";position:absolute;top:0;left:10%;right:10%;height:42%;border-radius:0 0 50% 50%;background:linear-gradient(180deg,rgba(255,255,255,.07),transparent);pointer-events:none}}
.pill.hard{{box-shadow:0 16px 34px rgba(0,0,0,.7),inset 0 1px 0 rgba(255,255,255,.07),inset 0 0 22px rgba(0,0,0,.6),inset 0 0 0 1px rgba(200,200,208,.14)}}
.pmark{{width:30px;flex:none;filter:drop-shadow(0 0 9px rgba(200,200,208,.45))}}
.pill .plabel{{font-size:9px;letter-spacing:.26em;color:var(--muted);margin-left:auto}}
.pdot{{width:8px;height:8px;border-radius:50%;background:currentColor;box-shadow:0 0 9px currentColor;flex:none;animation:pulse 1.6s ease-in-out infinite}}
.pwave{{flex:1;display:flex;align-items:center;gap:3px;height:26px}}
.pwave i{{flex:1;border-radius:2px;background:linear-gradient(180deg,var(--silver-hi),#5a5a62);opacity:.85;animation:eq 1.1s ease-in-out infinite}}
.pwave.mic i{{background:linear-gradient(180deg,#8effc0,#2a9d63)}}
.pdots{{flex:1;display:flex;align-items:center;gap:8px;padding-left:4px}}
.pdots i{{width:7px;height:7px;border-radius:50%;background:var(--silver);animation:pulse 1.2s ease-in-out infinite}}
.pdots i:nth-child(2){{animation-delay:.2s}}.pdots i:nth-child(3){{animation-delay:.4s}}
.pidle{{flex:1;display:flex;align-items:center;justify-content:center}}
.pidle i{{display:block;height:2px;width:34%;border-radius:2px;background:linear-gradient(90deg,transparent,rgba(200,200,208,.5),transparent)}}
.pagents{{flex:1;display:flex;flex-direction:column;gap:6px;justify-content:center}}
.pagents .row{{display:flex;align-items:center;gap:5px;font-size:10px;letter-spacing:.06em;color:var(--text2)}}
.pagents .row .dot{{width:6px;height:6px;border-radius:50%;box-shadow:0 0 5px currentColor}}
.phair{{height:2px;border-radius:2px;background:rgba(255,255,255,.08);overflow:hidden}}
.phair i{{display:block;height:100%;width:64%;background:linear-gradient(90deg,#6a6a72,var(--silver-hi));box-shadow:0 0 8px rgba(200,200,208,.4);animation:crawl 3.2s ease-in-out infinite}}
@keyframes eq{{0%,100%{{height:20%}}50%{{height:92%}}}}
@keyframes crawl{{0%{{width:52%}}50%{{width:71%}}100%{{width:52%}}}}
@keyframes pulse{{0%,100%{{opacity:.5}}50%{{opacity:1}}}}
@media(prefers-reduced-motion:reduce){{*{{animation:none!important}}}}
</style>

<div class="stage">
  <div class="pill" id="pill">
    <img class="pmark" src="data:image/png;base64,{eagle}">
    <span id="pbody"></span>
  </div>
</div>

<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<script>
var EAGLE = "data:image/png;base64,{eagle}";
function fillWave(el){{ var n=22; for(var i=0;i<n;i++){{var b=document.createElement('i');b.style.animationDelay=(i*0.045)+'s';b.style.animationDuration=(0.75+Math.random()*0.7)+'s';el.appendChild(b);}} }}

// ── drag from ANYWHERE in the window + double-click to expand (web captures
//    the mouse, so we route window moves through the bridge) ──────────────
(function(){{
  var dragging = false, moved = false;
  document.addEventListener('mousedown', function(e){{
    if (e.button !== 0) return;
    dragging = true; moved = false;
    if (window.pybridge) window.pybridge.begin_drag(e.screenX, e.screenY);
  }});
  document.addEventListener('mousemove', function(e){{
    if (dragging && window.pybridge) {{ moved = true; window.pybridge.drag_to(e.screenX, e.screenY); }}
  }});
  document.addEventListener('mouseup', function(){{ dragging = false; }});
  document.addEventListener('dblclick', function(){{ if (!moved && window.pybridge) window.pybridge.expand(); }});
}})();

window.pill = {{
  set: function(state, data){{
    data = data || {{}};
    var pill = document.getElementById('pill');
    var body = document.getElementById('pbody');
    pill.className = 'pill' + (state === 'swarm' ? ' hard' : '');
    body.className = ''; body.removeAttribute('style');
    if (state === 'listening') {{
      body.innerHTML = '<div class="pwave mic"></div><span class="pdot" style="color:var(--green)"></span>';
      body.style.cssText = 'flex:1;display:flex;align-items:center;gap:12px';
      fillWave(body.querySelector('.pwave'));
    }} else if (state === 'speaking') {{
      body.innerHTML = '<div class="pwave"></div>';
      body.style.cssText = 'flex:1;display:flex';
      fillWave(body.querySelector('.pwave'));
    }} else if (state === 'thinking') {{
      body.innerHTML = '<div class="pdots"><i></i><i></i><i></i></div><span class="plabel">THINKING</span>';
      body.style.cssText = 'flex:1;display:flex;align-items:center';
    }} else if (state === 'swarm') {{
      var working = data.working||0, needs = data.needs_you||0, total = Math.max(1, data.total||(working+needs));
      var dots = '';
      for (var i=0;i<working;i++) dots += '<span class="dot" style="background:var(--silver-hi)"></span>';
      for (var j=0;j<needs;j++) dots += '<span class="dot" style="background:var(--amber)"></span>';
      var label = working + ' working' + (needs ? ' · ' + needs + ' needs you' : '');
      body.innerHTML = '<div class="pagents"><div class="row">' + dots + '&nbsp;<span style="color:var(--muted)">' + label + '</span></div>'
        + '<div class="phair"><i style="width:' + Math.round(100*working/total) + '%"></i></div></div>';
      body.style.cssText = 'flex:1';
    }} else {{ // idle
      body.innerHTML = '<div class="pidle"><i></i></div>';
      body.style.cssText = 'flex:1;display:flex';
    }}
  }}
}};
window.pill.set('idle');

if (typeof QWebChannel !== 'undefined' && typeof qt !== 'undefined') {{
  new QWebChannel(qt.webChannelTransport, function(ch){{ window.pybridge = ch.objects.pybridge; }});
}}
</script>
"""

(HERE / "pill.html").write_text(HTML, encoding="utf-8")
print("wrote web/pill.html", len(HTML), "bytes")
