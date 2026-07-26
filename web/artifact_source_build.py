BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
SP = BASE_DIR / "assets" / "images"
FONTS = BASE_DIR / "assets" / "fonts"
b64 = lambda p: base64.b64encode(pathlib.Path(p).read_bytes()).decode()
doto, manrope, eagle = b64(FONTS/"Doto.ttf"), b64(FONTS/"Manrope-Variable.ttf"), b64(SP/"eagle_white.png")

def spring_linear(stiffness, damping, mass=1.0, n=46, settle=0.0015):
    """Sample a real spring step-response into a CSS linear() easing — the
    faithful way to reproduce iOS motion (mass/stiffness/damping), not a
    cubic-bezier approximation."""
    w0   = math.sqrt(stiffness / mass)
    zeta = damping / (2 * math.sqrt(stiffness * mass))
    T    = -math.log(settle) / (zeta * w0)          # time until envelope settles
    pts  = []
    for i in range(n + 1):
        t = T * i / n
        if zeta < 1:                                # underdamped → slight overshoot
            wd = w0 * math.sqrt(1 - zeta * zeta)
            x  = 1 - math.exp(-zeta*w0*t) * (math.cos(wd*t) + (zeta/math.sqrt(1-zeta*zeta))*math.sin(wd*t))
        else:                                       # critically damped → no overshoot
            x  = 1 - math.exp(-w0*t) * (1 + w0*t)
        pts.append(x)
    pts[-1] = 1.0
    return "linear(" + ", ".join(f"{p:.4f}" for p in pts) + ")"

SPRING_SNAPPY   = spring_linear(300, 20)   # iOS "snappy" — quick, faint overshoot
SPRING_SOFT     = spring_linear(190, 26)   # gentle settle for reveals, no bounce
SPRING_COLLAPSE = spring_linear(260, 24)   # geometry morph — snappy, ~3% overshoot (safe for width/height)

HTML = f"""<title>Aethelark — Adaptive Command Surface</title>
<style>
@font-face{{font-family:'Doto';src:url(data:font/ttf;base64,{doto}) format('truetype');font-weight:100 900;font-display:block}}
@font-face{{font-family:'Manrope';src:url(data:font/ttf;base64,{manrope}) format('truetype');font-weight:200 800;font-display:block}}
:root{{
  --void:#0A0A0A;--void2:#050506;--text:#E5E5EA;--text2:#C8C8D0;--muted:#7C7C86;
  --silver:#C8C8D0;--silver-hi:#ECECF2;--stroke:rgba(200,200,208,.14);
  --green:#4ee08a;--amber:#ffb060;--red:#ef4d5c;--violet:#c8a2ff;--blue:#8fb7ff;
  --ease-spring:{SPRING_SNAPPY};--ease-spring-soft:{SPRING_SOFT};--ease-collapse:{SPRING_COLLAPSE};
  --glass:repeating-linear-gradient(90deg,transparent 0,transparent 2px,rgba(255,255,255,.03) 2px,rgba(255,255,255,.03) 3px),linear-gradient(135deg,rgba(48,48,56,.42),rgba(12,12,15,.05));
  --metal:linear-gradient(135deg,rgba(24,24,28,.9),rgba(10,10,14,.94));
}}
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{background:var(--void2);color:var(--text);font-family:'Manrope',sans-serif;font-weight:350;letter-spacing:.02em;overflow-x:hidden}}
body{{min-height:100vh;display:flex;flex-direction:column;align-items:center;padding:28px 20px 56px;gap:20px}}
canvas#stars{{position:fixed;inset:0;z-index:0;pointer-events:none}}
.sig{{font-family:'Doto';font-weight:700;letter-spacing:.14em;text-shadow:0 1px 3px rgba(0,0,0,.9)}}
.etch{{display:inline-block;transform:scale(1.05,.9)}}
.etchL{{display:inline-block;transform:scale(1.05,.9);transform-origin:left center}}
.wrap{{position:relative;z-index:1;width:100%;max-width:1240px;display:flex;flex-direction:column;gap:20px;align-items:center}}
.eyebrow{{display:flex;align-items:center;gap:14px;color:var(--muted);font-size:11px;letter-spacing:.32em;text-transform:uppercase}}
.eyebrow .bar{{width:44px;height:1px;background:linear-gradient(90deg,transparent,var(--silver))}}
.eyebrow .bar.r{{background:linear-gradient(90deg,var(--silver),transparent)}}

/* mode toggle */
.modebar{{display:flex;align-items:center;gap:0;padding:4px;border-radius:13px;background:var(--metal);box-shadow:inset 0 1px 0 rgba(255,255,255,.06),0 8px 20px rgba(0,0,0,.5)}}
.modebtn{{padding:8px 22px;border-radius:10px;font-size:11px;letter-spacing:.2em;color:var(--muted);cursor:pointer;transition:.25s;display:flex;align-items:center;gap:9px;font-weight:600}}
.modebtn .d{{width:7px;height:7px;border-radius:50%;background:currentColor;box-shadow:0 0 8px currentColor}}
.modebtn.on{{color:var(--text);background:linear-gradient(135deg,rgba(200,200,208,.16),rgba(200,200,208,.05));box-shadow:inset 0 0 0 1px var(--stroke)}}
.modebtn.on.swarm{{color:var(--silver-hi)}}
.hint{{font-size:12px;color:var(--muted);letter-spacing:.04em;max-width:560px;text-align:center;line-height:1.65}}
.hint b{{color:var(--text2);font-weight:600}}

/* window */
.app{{position:relative;width:1200px;max-width:100%;height:726px;border-radius:16px;overflow:hidden;
  background:linear-gradient(135deg,rgba(16,16,20,.92),rgba(7,7,10,.96));backdrop-filter:blur(24px);
  box-shadow:0 24px 55px rgba(0,0,0,.62),0 0 30px rgba(255,255,255,.012);
  display:grid;grid-template-rows:54px 1fr 26px;outline:1px solid rgba(255,255,255,.05);outline-offset:-1px;
  transition:width .55s var(--ease-collapse),height .55s var(--ease-collapse),border-radius .55s var(--ease-collapse)}}
.app>.strip,.app>.body,.app>.foot{{transition:opacity .18s ease}}
.app.mini{{width:300px;height:84px;border-radius:42px}}
.app.mini>.strip,.app.mini>.body,.app.mini>.foot{{opacity:0;pointer-events:none}}
.livepill{{position:absolute;inset:0;z-index:5;display:flex;align-items:center;gap:14px;padding:0 26px;opacity:0;pointer-events:none;transition:opacity .28s ease .16s}}
.app.mini .livepill{{opacity:1;pointer-events:auto;cursor:pointer}}
.livepill::after{{content:"";position:absolute;top:0;left:12%;right:12%;height:42%;border-radius:0 0 50% 50%;background:linear-gradient(180deg,rgba(255,255,255,.07),transparent);pointer-events:none}}
.livepill img{{width:32px;flex:none;filter:drop-shadow(0 0 9px rgba(200,200,208,.45))}}
.livepill .lt{{font-size:15px;color:var(--text2);letter-spacing:.16em;margin-left:auto}}
.livepill .exp{{position:absolute;bottom:7px;left:50%;transform:translateX(-50%);font-size:7px;letter-spacing:.24em;color:var(--muted);opacity:.7}}
.strip{{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;padding:0 16px;background:linear-gradient(180deg,rgba(20,20,24,.96),rgba(9,9,12,.92));border-bottom:1px solid var(--stroke)}}
.brand{{display:flex;align-items:center;gap:12px;justify-self:start}}
.stud{{width:28px;height:28px;border-radius:9px;display:grid;place-items:center;color:var(--text2);font-size:13px;cursor:pointer;background:var(--metal);box-shadow:inset 0 1px 0 rgba(255,255,255,.06),0 2px 6px rgba(0,0,0,.4);transition:.18s}}
.stud:hover{{color:#fff;box-shadow:inset 0 0 0 1px var(--silver),0 0 14px rgba(200,200,208,.28)}}
.hmark{{width:30px;filter:drop-shadow(0 0 10px rgba(200,200,208,.28))}}
.word{{font-size:15px;letter-spacing:.42em;padding-left:.42em;color:var(--text)}}
.modeseg{{display:flex;gap:3px;padding:3px;border-radius:10px;background:rgba(0,0,0,.36);box-shadow:inset 0 0 0 1px var(--stroke);margin-left:12px}}
.modeseg button{{border:0;background:transparent;color:var(--muted);font-family:'Manrope';font-weight:600;font-size:10px;letter-spacing:.18em;padding:6px 14px;border-radius:7px;cursor:pointer;transition:color .35s var(--ease-spring),background .35s var(--ease-spring),box-shadow .35s var(--ease-spring)}}
.modeseg button:hover{{color:var(--text2)}}
.modeseg button.on{{color:var(--text);background:linear-gradient(135deg,rgba(200,200,208,.18),rgba(200,200,208,.05));box-shadow:inset 0 0 0 1px var(--stroke)}}
.chrono{{justify-self:center;display:flex;align-items:center;gap:12px;padding:5px 18px;border-radius:11px;background:linear-gradient(135deg,rgba(30,30,36,.5),rgba(10,10,14,.4));box-shadow:inset 0 1px 0 rgba(255,255,255,.05),0 6px 16px rgba(0,0,0,.45)}}
.chrono .t{{font-size:16px;color:#fff;letter-spacing:.16em}}.chrono .d{{font-size:9px;color:var(--text2);letter-spacing:.24em;text-transform:uppercase}}
.wctrl{{justify-self:end;display:flex;gap:7px}}
.wbtn{{height:26px;min-width:26px;padding:0 9px;border-radius:9px;display:grid;place-items:center;font-size:11px;color:var(--muted);cursor:pointer;background:var(--metal);box-shadow:inset 0 1px 0 rgba(255,255,255,.05);transition:.16s}}
.wbtn:hover{{color:#fff}}.wbtn.close:hover{{background:var(--red);color:#fff}}.wbtn.cl{{font-size:8px;font-weight:600;letter-spacing:.14em}}
.foot{{display:flex;align-items:center;justify-content:space-between;padding:0 16px;background:rgba(6,6,9,.9);border-top:1px solid var(--stroke)}}
.foot span{{font-size:9px;letter-spacing:.14em;color:var(--muted)}}.foot .r{{color:var(--text2);letter-spacing:.28em}}

.body{{position:relative;min-height:0}}
.view{{position:absolute;inset:0;display:grid;gap:14px;padding:14px;transition:opacity .3s ease,transform .62s var(--ease-spring)}}
.view.hide{{opacity:0;transform:scale(.978);pointer-events:none}}
#swarm:not(.hide) .lanes .lane{{animation:rise .62s var(--ease-spring) both}}
#swarm:not(.hide) .lanes .lane:nth-child(2){{animation-delay:.06s}}
#swarm:not(.hide) .lanes .lane:nth-child(3){{animation-delay:.12s}}
#swarm:not(.hide) .blackboard{{animation:rise .62s var(--ease-spring) .18s both}}
@keyframes rise{{from{{opacity:0;transform:translateY(16px)}}to{{opacity:1;transform:none}}}}
.rail{{border-radius:13px;background:var(--glass);box-shadow:inset 0 1px 0 rgba(255,255,255,.05),0 12px 30px rgba(0,0,0,.4);padding:15px 14px;display:flex;flex-direction:column;gap:13px;min-height:0}}
.sighead{{display:flex;align-items:center;gap:8px;font-size:10px;color:var(--text2);letter-spacing:.24em;padding-bottom:8px;border-bottom:1px solid var(--stroke)}}
.sighead .chev{{color:var(--silver)}}

/* ---------- RESTING ---------- */
#resting{{grid-template-columns:238px 1fr 300px}}
.memcard{{display:flex;flex-direction:column;gap:9px}}
.mempill{{display:flex;align-items:center;gap:9px;padding:9px 11px;border-radius:9px;background:rgba(0,0,0,.26);box-shadow:inset 0 1px 0 rgba(255,255,255,.04)}}
.mempill .ic{{width:22px;height:22px;border-radius:6px;display:grid;place-items:center;font-size:11px;color:var(--silver);background:linear-gradient(135deg,rgba(200,200,208,.14),rgba(200,200,208,.03));flex:none}}
.mempill .k{{font-size:8px;letter-spacing:.14em;color:var(--muted);text-transform:uppercase}}
.mempill .v{{font-size:12.5px;color:var(--text)}}
.minibars{{display:flex;gap:6px;margin-top:2px}}
.minibar{{flex:1;text-align:center;padding:7px 4px;border-radius:8px;background:rgba(0,0,0,.24)}}
.minibar .v{{font-family:'Doto';font-weight:700;font-size:12px;color:var(--text2);font-variant-numeric:tabular-nums}}
.minibar .k{{font-size:7px;letter-spacing:.14em;color:var(--muted);margin-top:2px}}
.core-wrap{{position:relative;border-radius:14px;overflow:hidden;min-height:0;background:radial-gradient(circle at 50% 44%,#20202a,#141419 34%,#0a0a0d 66%,#060608);box-shadow:inset 0 1px 0 rgba(255,255,255,.05),inset 0 0 60px rgba(0,0,0,.6),0 14px 34px rgba(0,0,0,.5);display:grid;place-items:center}}
.core{{position:relative;width:300px;height:300px;display:grid;place-items:center}}
.ring{{position:absolute;border-radius:50%;border:1px solid var(--stroke)}}
.ring.r1{{inset:0;border-color:rgba(200,200,208,.10)}}.ring.r2{{inset:34px;border-color:rgba(200,200,208,.16)}}
.ring.r3{{inset:70px;border-style:dashed;border-color:rgba(200,200,208,.13);animation:spin 44s linear infinite}}
.ring.breathe{{inset:14px;border-color:rgba(200,200,208,.22);animation:breathe 3.4s ease-in-out infinite}}
.sweep{{position:absolute;inset:2px;border-radius:50%;background:conic-gradient(from 0deg,rgba(200,200,208,.22),transparent 26%);-webkit-mask:radial-gradient(circle,transparent 44%,#000 45%);mask:radial-gradient(circle,transparent 44%,#000 45%);animation:spin 6.5s linear infinite;opacity:.7}}
.ticks{{position:absolute;inset:0;border-radius:50%}}.ticks i{{position:absolute;left:50%;top:50%;width:1px;height:7px;background:rgba(200,200,208,.35);transform-origin:0 150px}}
.crest{{position:relative;z-index:2;width:130px;filter:drop-shadow(0 0 18px rgba(200,200,208,.35));animation:breathe 3.4s ease-in-out infinite}}
.state{{position:absolute;bottom:18px;left:50%;transform:translateX(-50%);display:flex;align-items:center;gap:9px;padding:6px 15px;border-radius:20px;background:rgba(0,0,0,.4);box-shadow:inset 0 0 0 1px var(--stroke)}}
.state .dot{{width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 10px var(--green);animation:pulse 1.8s ease-in-out infinite}}
.state .lb{{font-size:11px;letter-spacing:.32em;color:var(--text2)}}
.corner{{position:absolute;width:16px;height:16px;border:1px solid rgba(200,200,208,.2)}}
.corner.tl{{top:12px;left:12px;border-right:0;border-bottom:0}}.corner.tr{{top:12px;right:12px;border-left:0;border-bottom:0}}
.corner.bl{{bottom:12px;left:12px;border-right:0;border-top:0}}.corner.br{{bottom:12px;right:12px;border-left:0;border-top:0}}
.stage{{display:flex;flex-direction:column;gap:14px;min-height:0}}
.saybar{{display:flex;gap:8px;flex-wrap:wrap;justify-content:center;padding:2px 4px 0}}
.chip{{font-size:11.5px;color:var(--text2);padding:7px 13px;border-radius:18px;background:rgba(0,0,0,.28);box-shadow:inset 0 0 0 1px var(--stroke);cursor:pointer;transition:.16s}}
.chip:hover{{box-shadow:inset 0 0 0 1px var(--silver);color:#fff}}
.log{{flex:1;min-height:0;overflow:hidden;border-radius:10px;background:rgba(0,0,0,.32);box-shadow:inset 0 1px 0 rgba(255,255,255,.03);padding:10px 11px;display:flex;flex-direction:column;gap:6px}}
.logline{{font-size:12px;line-height:1.55;display:flex;gap:8px}}
.tag{{flex:none;font-family:'Doto';font-weight:700;font-size:9px;letter-spacing:.1em;padding-top:1px}}
.tag.sys{{color:var(--silver)}}.tag.you{{color:var(--blue)}}.tag.ae{{color:var(--text)}}.tag.net{{color:var(--green)}}
.logline .msg{{color:#b9b9c2}}.logline.ae .msg{{color:#e7e7ee}}
.searchbar{{display:flex;align-items:center;gap:8px;height:30px;border-radius:15px;padding:0 13px;background:rgba(255,255,255,.03);box-shadow:inset 0 0 0 1px var(--stroke);color:var(--muted);font-size:11px;letter-spacing:.06em}}
.cmd{{display:flex;gap:7px}}
.cmd input{{flex:1;height:34px;border-radius:17px;border:0;padding:0 15px;background:rgba(255,255,255,.04);color:var(--text);font-family:'Manrope';font-size:12.5px;box-shadow:inset 0 0 0 1px var(--stroke);outline:none}}
.cmd input::placeholder{{color:var(--muted)}}.cmd input:focus{{box-shadow:inset 0 0 0 1px var(--silver)}}
.cmd .send{{width:34px;height:34px;border-radius:17px;display:grid;place-items:center;color:var(--text2);cursor:pointer;background:var(--metal);box-shadow:inset 0 1px 0 rgba(255,255,255,.06)}}
.cmd .send:hover{{color:#fff;box-shadow:inset 0 0 0 1px var(--silver)}}
.opbtn{{height:38px;border-radius:9px;display:flex;align-items:center;justify-content:center;gap:9px;font-size:11.5px;letter-spacing:.2em;cursor:pointer;transition:.18s;font-weight:600}}
.opbtn.interrupt{{color:var(--red);background:linear-gradient(135deg,rgba(40,8,14,.6),rgba(20,4,8,.7));box-shadow:inset 0 0 0 1px rgba(239,77,92,.5)}}
.opbtn.interrupt:hover{{box-shadow:inset 0 0 0 1px var(--red),0 0 16px rgba(239,77,92,.3)}}
.opbtn.mic{{color:var(--green);background:linear-gradient(135deg,rgba(10,34,22,.6),rgba(5,18,12,.7));box-shadow:inset 0 0 0 1px rgba(78,224,138,.45)}}
.opbtn .d{{width:6px;height:6px;border-radius:50%;background:currentColor;box-shadow:0 0 8px currentColor;animation:pulse 1.8s ease-in-out infinite}}

/* ---------- SWARM ---------- */
#swarm{{grid-template-columns:210px 1fr 286px}}
.conductor{{display:flex;align-items:center;gap:12px;padding:11px 13px;border-radius:12px;background:radial-gradient(circle at 20% 30%,rgba(200,200,208,.10),transparent 70%),rgba(0,0,0,.3);box-shadow:inset 0 1px 0 rgba(255,255,255,.05)}}
.conductor .badge{{position:relative;width:48px;height:48px;flex:none;display:grid;place-items:center}}
.conductor .badge img{{width:34px;filter:drop-shadow(0 0 10px rgba(200,200,208,.5));animation:breathe 3s ease-in-out infinite}}
.conductor .badge .rr{{position:absolute;inset:0;border-radius:50%;border:1px dashed rgba(200,200,208,.3);animation:spin 8s linear infinite}}
.conductor .txt .t{{font-size:12px;letter-spacing:.18em;color:var(--text)}}
.conductor .txt .s{{font-size:8px;letter-spacing:.14em;color:var(--muted);margin-top:3px}}
.swsum{{display:flex;flex-direction:column;gap:8px}}
.swrow{{display:flex;justify-content:space-between;font-size:11px;letter-spacing:.1em}}
.swrow .k{{color:var(--muted)}}.swrow .v{{font-family:'Doto';font-weight:700;color:var(--text2)}}
.gauge{{margin-top:4px}}.gauge .track{{height:6px;border-radius:3px;background:rgba(255,255,255,.05);overflow:hidden;box-shadow:inset 0 1px 1px rgba(0,0,0,.5)}}
.gauge .fill{{height:100%;width:64%;border-radius:3px;background:linear-gradient(90deg,#6a6a72,var(--silver-hi));box-shadow:0 0 10px rgba(200,200,208,.35)}}
.lanes{{flex:1;display:flex;flex-direction:column;gap:11px;min-height:0}}
.lane{{position:relative;border-radius:12px;overflow:hidden;background:var(--glass);box-shadow:inset 0 1px 0 rgba(255,255,255,.05),0 10px 24px rgba(0,0,0,.4);padding:12px 14px 12px 18px;display:flex;flex-direction:column;gap:8px;flex:1}}
.lane::before{{content:"";position:absolute;left:0;top:0;bottom:0;width:3px}}
.lane.work::before{{background:var(--silver-hi);box-shadow:0 0 14px rgba(200,200,208,.6)}}
.lane.review::before{{background:var(--green);box-shadow:0 0 14px rgba(78,224,138,.5)}}
.lane.block::before{{background:var(--amber);box-shadow:0 0 14px rgba(255,176,96,.5)}}
.lane .top{{display:flex;align-items:center;gap:10px}}
.lane .glyph{{width:26px;height:26px;border-radius:7px;display:grid;place-items:center;font-family:'Doto';font-weight:700;font-size:11px;color:var(--void);flex:none}}
.lane.work .glyph{{background:linear-gradient(135deg,#ECECF2,#B0B0B8)}}
.lane.review .glyph{{background:linear-gradient(135deg,#4ee08a,#2a9d63)}}
.lane.block .glyph{{background:linear-gradient(135deg,#ffb060,#c07a2a)}}
.lane .nm{{font-size:13.5px;color:var(--text);letter-spacing:.02em}}
.lane .br{{font-size:10px;color:var(--muted);letter-spacing:.08em;margin-top:1px}}
.lane .st{{margin-left:auto;font-size:8px;letter-spacing:.2em;padding:3px 9px;border-radius:6px;font-weight:700}}
.lane.work .st{{color:var(--silver-hi);box-shadow:inset 0 0 0 1px rgba(200,200,208,.4)}}
.lane.review .st{{color:var(--green);box-shadow:inset 0 0 0 1px rgba(78,224,138,.4)}}
.lane.block .st{{color:var(--amber);box-shadow:inset 0 0 0 1px rgba(255,176,96,.4)}}
.lane .think{{font-size:12px;line-height:1.55;color:#c3c3cc;font-family:'Manrope';padding:7px 10px;border-radius:8px;background:rgba(0,0,0,.32);box-shadow:inset 0 1px 0 rgba(255,255,255,.03)}}
.lane .think .cur{{color:var(--silver-hi)}}
.lane .meta{{display:flex;gap:14px;align-items:center;font-size:10px;letter-spacing:.06em;color:var(--muted)}}
.lane .meta b{{font-family:'Doto';font-weight:700;font-variant-numeric:tabular-nums}}
.lane .meta .add{{color:var(--green)}}.lane .meta .del{{color:var(--red)}}
.lane .meta .file{{color:var(--text2);padding:2px 7px;border-radius:5px;box-shadow:inset 0 0 0 1px var(--stroke)}}
.blackboard{{display:flex;align-items:center;gap:11px;padding:10px 13px;border-radius:10px;background:linear-gradient(135deg,rgba(200,162,255,.09),rgba(0,0,0,.3));box-shadow:inset 0 0 0 1px rgba(200,162,255,.2)}}
.blackboard .bi{{font-size:12px;color:var(--violet)}}
.blackboard .bt{{font-size:11px;color:#d8cdf0;line-height:1.55}}.blackboard .bt b{{color:#fff}}
.tl{{flex:1;min-height:0;overflow:hidden;border-radius:10px;background:rgba(0,0,0,.32);box-shadow:inset 0 1px 0 rgba(255,255,255,.03);padding:10px 11px;display:flex;flex-direction:column;gap:7px}}
.tlrow{{font-size:11px;line-height:1.5;display:flex;gap:8px}}
.tlrow .ts{{flex:none;font-family:'Doto';font-weight:700;font-size:8px;color:var(--muted);padding-top:1px}}
.tlrow .tx{{color:#b9b9c2}}.tlrow .tx b{{color:var(--text)}}
.tlrow.done .tx::before{{content:"\\2713 ";color:var(--green)}}

/* ---------- dynamic island ---------- */
.island-wrap{{display:flex;flex-direction:column;gap:16px;align-items:center;width:100%;margin-top:6px}}
.island-note{{font-size:12px;color:var(--muted);letter-spacing:.03em;max-width:660px;text-align:center;line-height:1.7}}
.island-note b{{color:var(--text2);font-weight:600}}
.pills{{display:flex;flex-wrap:wrap;gap:26px;justify-content:center}}
.pcol{{display:flex;flex-direction:column;align-items:center;gap:10px}}
.pill{{width:300px;height:62px;border-radius:31px;position:relative;overflow:hidden;display:flex;align-items:center;gap:14px;padding:0 22px;
  background:linear-gradient(180deg,#141419,#0a0a0d 55%,#000);
  box-shadow:0 16px 34px rgba(0,0,0,.7),inset 0 1px 0 rgba(255,255,255,.07),inset 0 0 22px rgba(0,0,0,.6)}}
.pill::after{{content:"";position:absolute;top:0;left:10%;right:10%;height:42%;border-radius:0 0 50% 50%;background:linear-gradient(180deg,rgba(255,255,255,.07),transparent);pointer-events:none}}
.pill.hard{{box-shadow:0 16px 34px rgba(0,0,0,.7),inset 0 1px 0 rgba(255,255,255,.07),inset 0 0 22px rgba(0,0,0,.6),inset 0 0 0 1px rgba(200,200,208,.14)}}
.pmark{{width:30px;flex:none;filter:drop-shadow(0 0 9px rgba(200,200,208,.45))}}
.pill .ptime{{font-size:14px;color:var(--text2);letter-spacing:.16em;margin-left:auto}}
.pill .plabel{{font-size:9px;letter-spacing:.26em;color:var(--muted);margin-left:auto}}
.pdot{{width:8px;height:8px;border-radius:50%;background:currentColor;box-shadow:0 0 9px currentColor;flex:none;animation:pulse 1.6s ease-in-out infinite}}
.pwave{{flex:1;display:flex;align-items:center;gap:3px;height:26px}}
.pwave i{{flex:1;border-radius:2px;background:linear-gradient(180deg,var(--silver-hi),#5a5a62);opacity:.85;animation:eq 1.1s ease-in-out infinite}}
.pwave.mic i{{background:linear-gradient(180deg,#8effc0,#2a9d63)}}
.pdots{{flex:1;display:flex;align-items:center;gap:8px;padding-left:4px}}
.pdots i{{width:7px;height:7px;border-radius:50%;background:var(--silver);animation:pulse 1.2s ease-in-out infinite}}
.pdots i:nth-child(2){{animation-delay:.2s}}.pdots i:nth-child(3){{animation-delay:.4s}}
.pagents{{flex:1;display:flex;flex-direction:column;gap:6px;justify-content:center}}
.pagents .row{{display:flex;align-items:center;gap:5px;font-size:10px;letter-spacing:.06em;color:var(--text2)}}
.pagents .row .dot{{width:6px;height:6px;border-radius:50%;box-shadow:0 0 5px currentColor}}
.phair{{height:2px;border-radius:2px;background:rgba(255,255,255,.08);overflow:hidden}}
.phair i{{display:block;height:100%;width:64%;background:linear-gradient(90deg,#6a6a72,var(--silver-hi));box-shadow:0 0 8px rgba(200,200,208,.4);animation:crawl 3.2s ease-in-out infinite}}
.pcap{{font-size:9px;letter-spacing:.18em;color:var(--muted);text-transform:uppercase}}
@keyframes eq{{0%,100%{{height:20%}}50%{{height:92%}}}}
@keyframes crawl{{0%{{width:52%}}50%{{width:71%}}100%{{width:52%}}}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
@keyframes breathe{{0%,100%{{transform:scale(1);opacity:.9}}50%{{transform:scale(1.045);opacity:1}}}}
@keyframes pulse{{0%,100%{{opacity:.55}}50%{{opacity:1}}}}
@keyframes typ{{0%,100%{{opacity:.3}}50%{{opacity:1}}}}
.blink{{animation:typ 1s steps(2) infinite}}
@media(prefers-reduced-motion:reduce){{*{{animation:none!important}}}}
</style>

<canvas id="stars"></canvas>
<div class="wrap">
  <div class="eyebrow"><span class="bar"></span>Aethelark · One surface, two states<span class="bar r"></span></div>
  <div class="hint" id="hint"><b>Casual mode.</b> What 90% of users live in — calm, voice-first, and it visibly remembers you. Flip the <b>CASUAL / HARDCORE</b> control in the title bar (it lives inside the app) to feel the surface escalate into the full swarm — with iOS-grade spring motion.</div>

  <div class="app">
    <div class="strip">
      <div class="brand">
        <div class="stud">&#9881;</div>
        <img class="hmark" src="data:image/png;base64,{eagle}">
        <span class="sig word">AETHELARK</span>
        <div class="modeseg" title="Aethelark auto-switches on intent — this is your manual override">
          <button id="seg-rest" class="on" onclick="setMode('rest')"><span class="etch">CASUAL</span></button>
          <button id="seg-swarm" onclick="setMode('swarm')"><span class="etch">HARDCORE</span></button>
        </div>
      </div>
      <div class="chrono"><span class="sig t">21:47:08</span><span class="sig d">Tue 21 Jul 2026</span></div>
      <div class="wctrl"><div class="wbtn">&#8211;</div><div class="wbtn cl" onclick="collapse()" title="Collapse to Dynamic Island"><span class="etch">COLLAPSE</span></div><div class="wbtn close">&#10005;</div></div>
    </div>

    <div class="body">
      <!-- ============ RESTING ============ -->
      <div class="view" id="resting">
        <div class="rail">
          <div class="sighead"><span class="chev">&#9670;</span><span class="etchL">AETHELARK REMEMBERS</span></div>
          <div class="memcard">
            <div class="mempill"><div class="ic">&#9670;</div><div><div class="k">You go by</div><div class="v">Shenny</div></div></div>
            <div class="mempill"><div class="ic">&#9906;</div><div><div class="k">Building</div><div class="v">WEB7 · Aethelark</div></div></div>
            <div class="mempill"><div class="ic">&#9889;</div><div><div class="k">Prefers</div><div class="v">Claude Code for dev</div></div></div>
            <div class="mempill"><div class="ic">&#9788;</div><div><div class="k">Rhythm</div><div class="v">Deep work after 9am</div></div></div>
          </div>
          <div style="flex:1"></div>
          <div class="minibars">
            <div class="minibar"><div class="v">34%</div><div class="k">CPU</div></div>
            <div class="minibar"><div class="v">61%</div><div class="k">MEM</div></div>
            <div class="minibar"><div class="v">08%</div><div class="k">GPU</div></div>
          </div>
        </div>

        <div class="stage">
          <div class="core-wrap">
            <span class="corner tl"></span><span class="corner tr"></span><span class="corner bl"></span><span class="corner br"></span>
            <div class="core">
              <div class="ring r1"></div><div class="ring r2"></div><div class="ring r3"></div><div class="ring breathe"></div>
              <div class="sweep"></div><div class="ticks" id="ticks"></div>
              <img class="crest" src="data:image/png;base64,{eagle}">
            </div>
            <div class="state"><span class="dot"></span><span class="sig lb">LISTENING</span></div>
          </div>
          <div class="saybar">
            <div class="chip">&#9835;&nbsp; Play my focus playlist</div>
            <div class="chip">&#9993;&nbsp; Read my new emails</div>
            <div class="chip">&#128196;&nbsp; Turn these notes into a PDF</div>
            <div class="chip">&#128172;&nbsp; Reply to Alex on WhatsApp</div>
          </div>
        </div>

        <div class="rail">
          <div class="sighead"><span class="chev">&#9656;</span><span class="etchL">TODAY</span></div>
          <div class="log">
            <div class="logline"><span class="tag sys">SYS</span><span class="msg">Good morning, Shenny.</span></div>
            <div class="logline you"><span class="tag you">YOU</span><span class="msg">play some lo-fi</span></div>
            <div class="logline ae"><span class="tag ae">AE</span><span class="msg">On it &mdash; opening YouTube.</span></div>
            <div class="logline you"><span class="tag you">YOU</span><span class="msg">any emails I should see?</span></div>
            <div class="logline ae"><span class="tag ae">AE</span><span class="msg">Two &mdash; one from your bank, one newsletter.</span></div>
          </div>
          <div class="searchbar">&#128269;&nbsp; Search everything Aethelark's done&hellip;</div>
          <div class="cmd"><input placeholder="Say it, or type it&hellip;"><div class="send">&#9656;</div></div>
          <div class="opbtn mic"><span class="d"></span><span class="etch">MICROPHONE ACTIVE</span></div>
        </div>
      </div>

      <!-- ============ SWARM ============ -->
      <div class="view hide" id="swarm">
        <div class="rail">
          <div class="conductor">
            <div class="badge"><div class="rr"></div><img src="data:image/png;base64,{eagle}"></div>
            <div class="txt"><div class="sig t">CONDUCTING</div><div class="s">3 AGENTS &middot; WEB7</div></div>
          </div>
          <div class="sighead"><span class="chev">&#9670;</span><span class="etchL">MISSION</span></div>
          <div class="swsum">
            <div class="swrow"><span class="k">REPO</span><span class="v">WEB7</span></div>
            <div class="swrow"><span class="k">WORKTREES</span><span class="v">3</span></div>
            <div class="swrow"><span class="k">MERGED</span><span class="v">1 / 3</span></div>
            <div class="swrow"><span class="k">CONFLICTS</span><span class="v" style="color:var(--amber)">1</span></div>
          </div>
          <div class="gauge"><div class="track"><div class="fill"></div></div></div>
          <div style="flex:1"></div>
          <div class="minibars">
            <div class="minibar"><div class="v">72%</div><div class="k">CPU</div></div>
            <div class="minibar"><div class="v">14</div><div class="k">TASKS</div></div>
            <div class="minibar"><div class="v">6:12</div><div class="k">ELAPSED</div></div>
          </div>
        </div>

        <div class="stage" style="gap:11px">
          <div class="lanes">
            <div class="lane work">
              <div class="top"><div class="glyph">C</div><div><div class="nm">Claude Code</div><div class="br">swarm/feat-ui &middot; feat/ui</div></div><div class="st sig">WORKING</div></div>
              <div class="think">Refactoring the hero into a reusable <span class="cur">&lt;GlassCard&gt;</span> so the shop and music player share one surface. Extracting the frost-titanium tokens now<span class="blink">&#9608;</span></div>
              <div class="meta"><span><b class="add">+142</b> <b class="del">&minus;38</b></span><span class="file">static/components.css</span><span>&#9201; 4:02</span></div>
            </div>
            <div class="lane review">
              <div class="top"><div class="glyph">A</div><div><div class="nm">Antigravity CLI</div><div class="br">swarm/feat-backend &middot; feat/backend</div></div><div class="st sig">IN REVIEW</div></div>
              <div class="think">Done &mdash; added the <span class="cur">/api/download</span> endpoint + rate limiter. Reviewer running py_compile and the test suite before merge.</div>
              <div class="meta"><span><b class="add">+206</b> <b class="del">&minus;11</b></span><span class="file">run.py</span><span>&#9201; 5:48</span></div>
            </div>
            <div class="lane block">
              <div class="top"><div class="glyph">O</div><div><div class="nm">OpenCode</div><div class="br">swarm/feat-tests &middot; feat/tests</div></div><div class="st sig">NEEDS YOU</div></div>
              <div class="think">Merge conflict in <span class="cur">db_schema.sql</span> against Antigravity's migration. Two ways to resolve &mdash; want me to take Antigravity's version or keep both columns?</div>
              <div class="meta"><span><b class="add">+64</b> <b class="del">&minus;9</b></span><span class="file">db_schema.sql</span><span>&#9201; 3:20</span></div>
            </div>
          </div>
          <div class="blackboard"><span class="bi">&#9673;</span><span class="bt"><b>Blackboard sync:</b> Antigravity broadcast the <b>download API schema</b> &rarr; Claude Code adapted the button contract automatically. No collision.</span></div>
        </div>

        <div class="rail">
          <div class="sighead"><span class="chev">&#9656;</span><span class="etchL">TIMELINE</span></div>
          <div class="searchbar">&#128269;&nbsp; Search the mission&hellip;</div>
          <div class="tl">
            <div class="tlrow"><span class="ts">6:12</span><span class="tx"><b>Claude</b> extracting GlassCard tokens</span></div>
            <div class="tlrow done"><span class="ts">5:48</span><span class="tx"><b>Antigravity</b> finished download API</span></div>
            <div class="tlrow"><span class="ts">5:20</span><span class="tx"><b>OpenCode</b> flagged schema conflict</span></div>
            <div class="tlrow done"><span class="ts">4:31</span><span class="tx"><b>Reviewer</b> merged feat/ui</span></div>
            <div class="tlrow done"><span class="ts">3:02</span><span class="tx">Blackboard: API schema shared</span></div>
            <div class="tlrow done"><span class="ts">0:00</span><span class="tx"><b>You:</b> "refactor WEB7, keep the vision"</span></div>
          </div>
          <div class="opbtn interrupt"><span class="etch">INTERJECT &middot; HALT SWARM</span></div>
          <div class="opbtn mic"><span class="d"></span><span class="etch">MICROPHONE ACTIVE</span></div>
        </div>
      </div>
    </div>

    <div class="foot"><span id="footl">[F4] MUTE · [F11] FULLSCREEN · [ESC] INTERRUPT</span><span class="sig r">SPACE&#8226;EAGLE</span></div>

    <!-- collapsed Dynamic Island (the whole window springs into this) -->
    <div class="livepill" onclick="expand()" title="Click to expand">
      <img src="data:image/png;base64,{eagle}">
      <div class="pwave" data-n="24"></div>
      <span class="lt sig">21:47</span>
      <span class="exp sig">CLICK TO EXPAND</span>
    </div>
  </div>

  <div class="hint" style="max-width:780px"><b>Same window. Same identity.</b> Casual is calm, remembers you, plug-and-play for the 90%. Hardcore summons itself the instant real work starts, shows every agent thinking, and recedes when the job's done. Nobody has to learn a command line to command a swarm.</div>

  <!-- ============ THE DYNAMIC ISLAND ============ -->
  <div class="island-wrap">
    <div class="eyebrow" style="margin-top:8px"><span class="bar"></span>The Dynamic Island · collapsed &amp; alive<span class="bar r"></span></div>
    <div class="island-note">Hit <b>COLLAPSE</b> in the title bar and the whole console springs down into this pill &mdash; then click the pill to pop it back. The pill keeps its exact <b>240&times;84</b> footprint &mdash; a facelift, not a redraw. Collapsed no longer means blind: it breathes with your voice, and in Hardcore it becomes an <b>ambient swarm readout</b> you can glance at from across the room.</div>
    <div class="pills">
      <div class="pcol"><div class="pill"><img class="pmark" src="data:image/png;base64,{eagle}"><span class="ptime sig">21:47</span></div><span class="pcap">Casual &middot; idle</span></div>
      <div class="pcol"><div class="pill"><img class="pmark" src="data:image/png;base64,{eagle}"><div class="pwave mic" data-n="22"></div><span class="pdot" style="color:var(--green)"></span></div><span class="pcap">Listening</span></div>
      <div class="pcol"><div class="pill"><img class="pmark" src="data:image/png;base64,{eagle}"><div class="pwave" data-n="22"></div></div><span class="pcap">Speaking</span></div>
      <div class="pcol"><div class="pill"><img class="pmark" src="data:image/png;base64,{eagle}"><div class="pdots"><i></i><i></i><i></i></div><span class="plabel sig">THINKING</span></div><span class="pcap">Thinking</span></div>
      <div class="pcol"><div class="pill hard">
        <img class="pmark" src="data:image/png;base64,{eagle}">
        <div class="pagents">
          <div class="row"><span class="dot" style="background:var(--silver-hi)"></span><span class="dot" style="background:var(--silver-hi)"></span><span class="dot" style="background:var(--amber);box-shadow:0 0 6px var(--amber)"></span>&nbsp;<span style="color:var(--muted)">2 working &middot; 1 needs you</span></div>
          <div class="phair"><i></i></div>
        </div>
      </div><span class="pcap">Hardcore &middot; ambient swarm</span></div>
    </div>
  </div>
</div>

<script>
function setMode(m){{
  const rest=document.getElementById('resting'),sw=document.getElementById('swarm');
  const sr=document.getElementById('seg-rest'),ss=document.getElementById('seg-swarm');
  const h=document.getElementById('hint'),fl=document.getElementById('footl');
  if(m==='swarm'){{rest.classList.add('hide');sw.classList.remove('hide');sr.classList.remove('on');ss.classList.add('on');
    h.innerHTML='<b>Hardcore mode.</b> The Crest stepped aside to conduct. Three agents working in isolated worktrees, thinking out loud, coordinating through the blackboard \\u2014 and one needs your call. Halt any of them by voice.';
    fl.textContent='[ESC] INTERJECT · SPEAK TO REDIRECT ANY AGENT';}}
  else{{sw.classList.add('hide');rest.classList.remove('hide');ss.classList.remove('on');sr.classList.add('on');
    h.innerHTML='<b>Casual mode.</b> What 90% of users live in \\u2014 calm, voice-first, and it visibly remembers you. The <b>CASUAL / HARDCORE</b> control lives in the title bar; Aethelark also flips it for you automatically when real work starts.';
    fl.textContent='[F4] MUTE · [F11] FULLSCREEN · [ESC] INTERRUPT';}}
}}
const _app=document.querySelector('.app');
function collapse(){{_app.classList.add('mini');}}
function expand(){{_app.classList.remove('mini');}}
const t=document.getElementById('ticks');
for(let i=0;i<60;i++){{const s=document.createElement('i');s.style.transform=`rotate(${{i*6}}deg) translateX(-50%)`;if(i%5)s.style.opacity=.4;t.appendChild(s);}}
document.querySelectorAll('.pwave').forEach(w=>{{const n=+w.dataset.n||22;for(let i=0;i<n;i++){{const b=document.createElement('i');b.style.animationDelay=(i*0.045)+'s';b.style.animationDuration=(0.75+Math.random()*0.7)+'s';w.appendChild(b);}}}});
const cv=document.getElementById('stars'),cx=cv.getContext('2d');let W,H,stars=[],comets=[];
const DIA=['#E8E8F2','#BFD8FF','#EDE0B0','#E6D6F0','#CFEFE8'];
function rz(){{W=cv.width=innerWidth;H=cv.height=innerHeight;stars=[];for(let i=0;i<160;i++){{const d=Math.random()<0.2;stars.push({{x:Math.random()*W,y:Math.random()*H,r:(Math.random()*1.3+0.5)*(d?1.5:1),a:Math.random(),sp:Math.random()*0.4+0.15,vy:-(Math.random()*0.06+0.03),d,col:d?DIA[(Math.random()*DIA.length)|0]:'180,180,186'}});}}}}
rz();addEventListener('resize',rz);
function draw(){{cx.clearRect(0,0,W,H);
 for(const s of stars){{s.a+=s.sp*0.02;const tw=(Math.sin(s.a)*0.5+0.5);s.y+=s.vy;if(s.y<-2)s.y=H+2;
  if(s.d){{const al=0.15+tw*0.85;cx.save();cx.translate(s.x,s.y);cx.fillStyle=s.col;cx.globalAlpha=al;
   cx.beginPath();for(let k=0;k<4;k++){{cx.rotate(Math.PI/2);cx.moveTo(0,0);cx.lineTo(s.r*0.6,s.r*0.6);cx.lineTo(0,s.r*4.2);cx.lineTo(-s.r*0.6,s.r*0.6);}}cx.fill();
   cx.beginPath();cx.arc(0,0,s.r*0.9,0,7);cx.fill();cx.restore();}}
  else{{cx.fillStyle=`rgba(${{s.col}},${{0.03+tw*0.5}})`;cx.beginPath();cx.arc(s.x,s.y,s.r,0,7);cx.fill();}}}}
 if(Math.random()<0.004&&comets.length<2)comets.push({{x:Math.random()*W*0.5,y:Math.random()*H*0.3,v:18+Math.random()*10,ang:(35+Math.random()*10)*Math.PI/180,life:1}});
 for(let i=comets.length-1;i>=0;i--){{const c=comets[i],dx=Math.cos(c.ang)*c.v,dy=Math.sin(c.ang)*c.v;c.x+=dx;c.y+=dy;c.life-=0.012;
  const tx=c.x-dx*11,ty=c.y-dy*11,g=cx.createLinearGradient(c.x,c.y,tx,ty);g.addColorStop(0,`rgba(255,255,255,${{c.life}})`);g.addColorStop(1,'rgba(255,255,255,0)');
  cx.strokeStyle=g;cx.lineWidth=2;cx.beginPath();cx.moveTo(c.x,c.y);cx.lineTo(tx,ty);cx.stroke();
  cx.fillStyle=`rgba(255,255,255,${{c.life}})`;cx.beginPath();cx.arc(c.x,c.y,1.6,0,7);cx.fill();
  if(c.life<=0||c.x>W||c.y>H)comets.splice(i,1);}}
 requestAnimationFrame(draw);}}
draw();
</script>
"""
out = SP/"aethelark_adaptive.html"
out.write_text(HTML, encoding="utf-8")
print("wrote", out, len(HTML))
