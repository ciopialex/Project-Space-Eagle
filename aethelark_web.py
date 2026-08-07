"""Aethelark — integrated web-rendered app.

Runs the REAL voice/swarm backend (main.AethelarkLive) behind a native pill +
QWebEngine web dashboard (the exact artifact). `WebShellUI` is a drop-in
adapter exposing the interface AethelarkLive expects (write_log / set_state /
muted / set_audio_level / …), translating it into native-pill updates +
dashboard.push(...) per the message contract in Aethelark_Web_Pivot_Plan.md.

main.py and ui.py are UNTOUCHED — this is a parallel entry point so the
existing `eagle` launch keeps working. Run:  .venv/bin/python aethelark_web.py
"""
import json
import sys
import threading
import time
import pathlib

from PyQt6.QtCore import (Qt, QObject, pyqtSlot, pyqtSignal, QUrl, QEvent, QTimer,
                          QRect)
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLabel, QPushButton)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebChannel import QWebChannel

from ui import load_app_fonts, _metrics, make_spring_curve
from memory.memory_manager import load_memory
from core import user_paths

BASE = pathlib.Path(__file__).resolve().parent
DASHBOARD_HTML = BASE / "web" / "dashboard.html"
PILL_HTML = BASE / "web" / "pill.html"
API_KEYS = user_paths.api_keys_path()
PILL_W, PILL_H = 400, 170   # generous so the pill's drop-shadow isn't clipped


class _RootShim:
    def __init__(self, app): self._app = app
    def mainloop(self): return self._app.exec()
    def protocol(self, *_): pass
    def quit(self): self._app.quit()


class _WinShim:
    """Stands in for ui._win — the backend only touches ._ready."""
    def __init__(self): self._ready = False


class WebBridge(QObject):
    """window.pybridge — actions the web UI calls on Python."""
    def __init__(self, ui):
        super().__init__(); self._ui = ui

    @pyqtSlot()
    def ready(self): self._ui._on_ui_ready()
    @pyqtSlot()
    def collapse(self): self._ui.collapse_to_pill()
    @pyqtSlot()
    def minimize(self): self._ui.dashboard.showMinimized()
    @pyqtSlot()
    def quit(self): QApplication.instance().quit()
    @pyqtSlot(str)
    def send_command(self, text): self._ui._dispatch_command(text)
    @pyqtSlot(str)
    def set_mode(self, mode): self._ui.mode = "hardcore" if mode.lower() == "hardcore" else "casual"
    @pyqtSlot()
    def interrupt(self): self._ui._fire_interrupt()
    @pyqtSlot()
    def halt_swarm(self): self._ui._fire_interrupt()
    @pyqtSlot()
    def toggle_mute(self): self._ui.toggle_mute()

    # ---- Aesthetic picker ----
    @pyqtSlot(result=str)
    def aesthetic_options(self):
        """Sections + words for the picker to render."""
        import json as _j
        from core import aesthetics
        return _j.dumps(aesthetics.options())

    @pyqtSlot(str, str, result=str)
    def set_aesthetic(self, choices_json, free_text):
        """Park the user's taste until they say "build it".

        The picker and the build command are separate events — chips get
        tapped a minute before the mission starts — so the choice has to
        survive the gap rather than being asked for again at plan time.
        """
        import json as _j
        from actions.swarm_orchestrator import set_aesthetic
        try:
            choices = _j.loads(choices_json) if choices_json else None
        except ValueError:
            choices = None
        brief = set_aesthetic(choices, free_text or "")
        return "Saved — I'll build to that look." if brief else "Cleared."

    # ---- Settings panel (the title-bar gear) ----
    @pyqtSlot()
    def open_settings(self): self._ui.push_settings()
    @pyqtSlot(str)
    def save_settings(self, patch): self._ui.save_settings(patch)
    @pyqtSlot(bool)
    def set_autostart(self, on): self._ui.set_autostart(on)
    @pyqtSlot(str, str)
    def set_brain_key(self, provider, key): self._ui.set_brain_key(provider, key)
    @pyqtSlot()
    def connect_google(self): self._ui.connect_google()
    @pyqtSlot()
    def disconnect_google(self): self._ui.disconnect_google()
    @pyqtSlot()
    def link_whatsapp(self): self._ui.link_whatsapp()
    @pyqtSlot(str)
    def browser_sign_in(self, site): self._ui.browser_sign_in(site)
    @pyqtSlot()
    def rerun_onboarding(self): self._ui.rerun_onboarding()

    @pyqtSlot(int, int)
    def begin_drag(self, sx, sy):
        w = self._ui.dashboard
        w._drag_origin = (sx, sy, w.x(), w.y())

    @pyqtSlot(int, int)
    def drag_to(self, sx, sy):
        w = self._ui.dashboard
        o = getattr(w, "_drag_origin", None)
        if o:
            w.move(o[2] + (sx - o[0]), o[3] + (sy - o[1]))


class DashWindow(QMainWindow):
    def __init__(self, ui):
        super().__init__()
        self._ui = ui
        self.setWindowTitle("AETHELARK")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view = QWebEngineView()
        self.view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view.page().setBackgroundColor(Qt.GlobalColor.transparent)
        self.setCentralWidget(self.view)
        self.channel = QWebChannel()
        self.bridge = WebBridge(ui)
        self.channel.registerObject("pybridge", self.bridge)
        self.view.page().setWebChannel(self.channel)
        self.view.load(QUrl.fromLocalFile(str(DASHBOARD_HTML)))

    def push(self, fn, payload):
        self.view.page().runJavaScript(
            "window.aethelark && window.aethelark.%s(%s)" % (fn, json.dumps(payload)))

    def changeEvent(self, e):
        if (e.type() == QEvent.Type.ActivationChange
                and self.isVisible() and not self.isActiveWindow()):
            self._ui.on_dashboard_blur()
        super().changeEvent(e)


class PillBridge(QObject):
    """window.pybridge on the pill page — double-click → expand, drag → move."""
    def __init__(self, ui): super().__init__(); self._ui = ui

    @pyqtSlot()
    def expand(self): self._ui.open_dashboard()

    @pyqtSlot(int, int)
    def begin_drag(self, sx, sy):
        w = self._ui.pill_win
        w._drag_origin = (sx, sy, w.x(), w.y())

    @pyqtSlot(int, int)
    def drag_to(self, sx, sy):
        w = self._ui.pill_win
        o = getattr(w, "_drag_origin", None)
        if o:
            # delta-based so it's correct regardless of screenX origin
            w.move(o[2] + (sx - o[0]), o[3] + (sy - o[1]))


class PillWebWindow(QMainWindow):
    """Transparent, frameless, always-on-top window rendering web/pill.html —
    the Dynamic Island, pixel-identical to the artifact."""
    def __init__(self, ui):
        super().__init__()
        self._ui = ui
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view = QWebEngineView()
        self.view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view.page().setBackgroundColor(Qt.GlobalColor.transparent)
        self.setCentralWidget(self.view)
        self.channel = QWebChannel()
        self.bridge = PillBridge(ui)
        self.channel.registerObject("pybridge", self.bridge)
        self.view.page().setWebChannel(self.channel)
        self._pending = ("idle", {})
        self.view.loadFinished.connect(lambda ok: self._apply())
        self.view.load(QUrl.fromLocalFile(str(PILL_HTML)))
        self.resize(PILL_W, PILL_H)

    def set_pill(self, state, data=None):
        # Persist the latest state so it survives (a) a set before the page has
        # loaded and (b) Chromium throttling the view while it's hidden.
        self._pending = (state, data or {})
        self._apply()

    def _apply(self):
        st, data = self._pending
        self.view.page().runJavaScript(
            "window.pill && window.pill.set(%s, %s)" % (json.dumps(st), json.dumps(data)))

    def showEvent(self, e):
        super().showEvent(e)
        self._apply()   # re-assert state on re-show (hidden views go stale)


class WebClipboardPanel(QWidget):
    """Clipboard Intelligence for the web app (ported from the classic UI):
    when the user copies text, a floating tech-noir panel offers Translate /
    Summarise / Explain / Fix — one click routes it to the brain."""
    action_requested = pyqtSignal(str)
    _ACTIONS = [
        ("TRANSLATE", "Translate this text to English: {text}"),
        ("SUMMARISE", "Summarise this: {text}"),
        ("EXPLAIN",   "Explain this: {text}"),
        ("FIX",       "Fix the grammar and spelling of this: {text}"),
    ]

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(360)
        self._text = ""

        wrap = QWidget(self)
        wrap.setObjectName("clipwrap")
        wrap.setStyleSheet("""
            #clipwrap{background:rgba(12,12,16,0.97);border:1px solid rgba(200,200,208,0.22);border-radius:14px;}
            QLabel#hdr{color:#C8C8D0;font-family:'Doto';font-weight:700;font-size:9px;letter-spacing:2px;background:transparent;}
            QLabel#prev{color:#E5E5EA;background:rgba(0,0,0,0.35);border:1px solid rgba(200,200,208,0.14);border-radius:7px;padding:6px 9px;font-size:11px;}
            QPushButton#act{color:#C8C8D0;background:rgba(255,255,255,0.04);border:1px solid rgba(200,200,208,0.16);border-radius:8px;font-family:'Manrope';font-weight:600;font-size:10px;letter-spacing:1px;padding:8px 0;}
            QPushButton#act:hover{color:#fff;border-color:#C8C8D0;background:rgba(255,255,255,0.08);}
            QPushButton#x{color:#7C7C86;background:transparent;border:none;font-size:13px;}
            QPushButton#x:hover{color:#fff;}
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(wrap)

        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(12, 10, 12, 11)
        lay.setSpacing(8)

        hdr = QHBoxLayout()
        h = QLabel("◈  CLIPBOARD DETECTED"); h.setObjectName("hdr")
        x = QPushButton("✕"); x.setObjectName("x"); x.setFixedSize(18, 18)
        x.setCursor(Qt.CursorShape.PointingHandCursor); x.clicked.connect(self.hide)
        hdr.addWidget(h); hdr.addStretch(); hdr.addWidget(x)
        lay.addLayout(hdr)

        self._preview = QLabel(); self._preview.setObjectName("prev"); self._preview.setWordWrap(False)
        lay.addWidget(self._preview)

        row = QHBoxLayout(); row.setSpacing(6)
        for label, fmt in self._ACTIONS:
            b = QPushButton(label); b.setObjectName("act")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _, c=fmt: self._fire(c))
            row.addWidget(b)
        lay.addLayout(row)

        self._timer = QTimer(self); self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self.hide()

    def _fire(self, fmt):
        if self._text:
            self.action_requested.emit(fmt.format(text=self._text[:800]))
        self.hide()

    def show_for(self, text, x, y):
        self._text = text
        prev = text[:64].replace("\n", " ")
        if len(text) > 64:
            prev += "…"
        self._preview.setText('"' + prev + '"')
        self.adjustSize()
        self.move(x, y)
        self.show(); self.raise_()
        self._timer.start(8000)


class WebShellUI(QObject):
    """The object AethelarkLive drives. Thread-safe: backend runs in a worker
    thread and calls these; GUI work is marshalled to the main thread via signals."""
    # The web pill's waveform is CSS-animated, so the backend must NOT spend CPU
    # computing a per-frame RMS envelope during speech (it's a no-op here). The
    # playback loop reads this to skip that work — keeps voice snappy.
    consumes_audio_level = False

    # Declared at class level so core.ui_contract can verify conformance
    # statically. The backend assigns these at startup (main.py:850-852).
    on_text_command = None
    on_remote_clicked = None
    on_interrupt = None

    _state_sig    = pyqtSignal(str)
    _log_sig      = pyqtSignal(str)
    _audio_sig    = pyqtSignal(float)
    _content_sig  = pyqtSignal(str, str)
    _reconfig_sig = pyqtSignal()
    _settings_sig = pyqtSignal(dict)   # worker threads → push settings snapshot

    def __init__(self, face_path="face.png"):
        super().__init__()
        self._app = QApplication.instance() or QApplication(sys.argv)
        load_app_fonts()
        self._app.setStyle("Fusion")

        self.mode = "casual"
        self.pinned = False
        self._muted = False
        self._last_state = None      # dedupe: backend re-asserts SPEAKING per audio frame
        self._log_lines = []
        self._assistant_name = "Aethelark"
        self._ui_ready = False
        self._routing = None      # cached capability routing; drives lane labels

        self.on_text_command = None
        self.on_remote_clicked = None
        self.on_interrupt = None

        self._win = _WinShim()
        self.root = _RootShim(self._app)

        self.pill_win = PillWebWindow(self)
        self.dashboard = DashWindow(self)
        self.dashboard.hide()

        geo = QApplication.primaryScreen().availableGeometry()
        self._pill_geo = QRect(geo.x() + (geo.width() - PILL_W) // 2, geo.y() + 6, PILL_W, PILL_H)
        # Expanded = a centered card at the artifact's screen proportions (~0.63 × 0.70),
        # NOT fullscreen — so the collapse morph keeps the artifact's exact ratio & feel.
        ew, eh = int(geo.width() * 0.63), int(geo.height() * 0.70)
        self._expanded_geo = QRect(geo.x() + (geo.width() - ew) // 2,
                                   geo.y() + (geo.height() - eh) // 2, ew, eh)

        self._state_sig.connect(self._on_state)
        self._log_sig.connect(self._on_log)
        self._content_sig.connect(self._on_content)
        self._reconfig_sig.connect(self._on_reconfig)
        self._settings_sig.connect(lambda snap: self._push("setSettings", snap))

        QShortcut(QKeySequence("F4"), self.dashboard, activated=self.toggle_mute)

        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._tick)
        self._metric_tmr.start(2000)

        # Clipboard Intelligence — copy text → floating quick-action panel.
        self._last_clip = ""
        self._clip_panel = WebClipboardPanel()
        self._clip_panel.action_requested.connect(self._dispatch_command)
        try:
            self._app.clipboard().dataChanged.connect(self._on_clipboard_changed)
        except Exception as e:
            print(f"[aethelark_web] clipboard watch unavailable: {e}")

        self.show_pill()

    def _on_clipboard_changed(self):
        try:
            text = self._app.clipboard().text().strip()
        except Exception:
            return
        if len(text) < 10 or text == self._last_clip:
            return
        self._last_clip = text
        geo = QApplication.primaryScreen().availableGeometry()
        w = self._clip_panel.width() or 360
        self._clip_panel.show_for(text, geo.x() + (geo.width() - w) // 2, geo.y() + 96)

    # ---- window transitions (main thread) ----
    def show_pill(self):
        self.pill_win.setGeometry(self._pill_geo)
        self.pill_win.show(); self.pill_win.raise_()

    # Expand/collapse is animated INSIDE the page (a GPU-composited transform on
    # the card), not by resizing the Chromium window. Resizing forced a full page
    # relayout every frame — the stutter you saw. The window is simply shown at
    # its final size and the card springs in via CSS.
    _MORPH_OUT_MS = 260   # must cover the aeOut keyframe duration in build_app_ui

    def open_dashboard(self):
        # Show the extended card at full size; the "spring from the island" is the
        # CSS aeIn animation growing the card from the top-centre.
        self.dashboard.setGeometry(self._expanded_geo)
        # Keep the window invisible for one frame so the CSS from-state (tiny +
        # transparent) is applied BEFORE it's shown — otherwise the full-size card
        # flashes for a frame. Fails safe: revealed unconditionally at 24ms even
        # if the morph JS didn't run.
        self.dashboard.setWindowOpacity(0.0)
        self.dashboard.show()
        self.dashboard.activateWindow(); self.dashboard.raise_()
        self.pill_win.hide()
        self.dashboard.push("playMorph", "in")
        QTimer.singleShot(24, lambda: self.dashboard.setWindowOpacity(1.0))
        self._push_all()

    def collapse_to_pill(self):
        # Shrink the card back toward the island (CSS aeOut), then hand off to the
        # live pill once the animation has finished.
        self.dashboard.push("playMorph", "out")
        QTimer.singleShot(self._MORPH_OUT_MS, self._after_collapse)

    def _after_collapse(self):
        self.dashboard.hide()
        self.dashboard.setWindowOpacity(1.0)
        self.dashboard.setGeometry(self._expanded_geo)  # reset for next open
        self.show_pill()

    def on_dashboard_blur(self):
        # Interaction model (revised): the dashboard NO LONGER collapses just
        # because it lost focus. Clicking another window (e.g. Claude Code next
        # to Aethelark) must keep the dashboard open so the two can sit side by
        # side. Collapse to the pill happens ONLY on an explicit trigger:
        #   • the Collapse button in the UI          → bridge.collapse()
        #   • a voice command ("go to pill mode", …) → collapse_to_pill()
        #   • another window going fullscreen         → (handled elsewhere)
        # so plain blur is intentionally a no-op now.
        return

    # ---- bridge-driven ----
    def _on_ui_ready(self):
        self._ui_ready = True; self._push_all()

    def _dispatch_command(self, text):
        text = (text or "").strip()
        if not text:
            return
        self.write_log(f"You: {text}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(text,), daemon=True).start()

    def _fire_interrupt(self):
        if self.on_interrupt:
            threading.Thread(target=self.on_interrupt, daemon=True).start()

    def toggle_mute(self):
        self.muted = not self._muted

    # ---- Settings panel (title-bar gear) ----
    def push_settings(self):
        """Send a fresh settings snapshot to the panel. Computed off the GUI
        thread — the first snapshot probes installed browsers (shells out to
        xdg-settings), which must never block the UI / steal the audio GIL."""
        def _work():
            try:
                from actions.app_settings import snapshot
                self._push_settings_async(snapshot())
            except Exception as e:
                print(f"[aethelark_web] settings snapshot failed: {e}")
        threading.Thread(target=_work, daemon=True).start()

    def _push_settings_async(self, snap):
        # From a worker thread we can't touch the web view directly.
        self._settings_sig.emit(snap or {})

    def save_settings(self, patch):
        try:
            data = json.loads(patch or "{}")
        except Exception:
            data = {}
        try:
            from actions.app_settings import save, set_brain_key
            key = (data.pop("brain_api_key", "") or "").strip()
            snap = save(data)
            if key:
                snap = set_brain_key(data.get("brain_provider")
                                     or snap["brain"]["provider"], key)
            self._push("setSettings", snap)
        except Exception as e:
            print(f"[aethelark_web] save_settings failed: {e}")

    def set_autostart(self, on):
        try:
            from actions.app_settings import set_autostart
            self._push("setSettings", set_autostart(bool(on)))
        except Exception as e:
            print(f"[aethelark_web] autostart toggle failed: {e}")

    def set_brain_key(self, provider, key):
        try:
            from actions.app_settings import set_brain_key
            self._push("setSettings", set_brain_key(provider, key))
        except Exception as e:
            print(f"[aethelark_web] set_brain_key failed: {e}")

    def connect_google(self):
        def _flow():
            try:
                from actions.google_auth import sign_in_google
                res = sign_in_google()
                status = res.get("status")
                if status == "ok":
                    self.write_log(f"NET: Google connected — {res.get('email', '')}")
                elif status == "not_configured":
                    # The OAuth seam has no client_id yet — tell the user plainly
                    # instead of the button silently snapping back to 'Connect'.
                    self.write_log("NET: Google sign-in isn't switched on yet "
                                   "(needs a google_client_id). Guest + browser "
                                   "automation still work in the meantime.")
                else:
                    self.write_log(f"NET: Google sign-in — {res.get('message', 'failed')}")
            except Exception as e:
                self.write_log(f"ERR: Google sign-in — {e}")
            try:
                from actions.app_settings import snapshot
                self._push_settings_async(snapshot())
            except Exception as _e:
                print(f"[aethelark_web.py] Non-fatal error at line 488: {_e}")
        threading.Thread(target=_flow, daemon=True).start()

    def browser_sign_in(self, site: str):
        """Open the eagle's own browser so the user can sign in to `site`.

        This is the answer to "how do I log in?", which until now existed only
        as a voice command you had to know to say. The eagle's browser is
        separate from the user's Chrome on purpose, and nothing in the
        interface said so or offered a way to act on it.

        Off the UI thread: making the browser visible restarts it, which takes
        a moment, and the panel must not freeze while it does.
        """
        import threading

        def _run():
            try:
                from actions.web_agency import web_agency
                from actions.app_settings import snapshot
                domain = (site or "").strip()
                for prefix in ("https://", "http://"):
                    if domain.startswith(prefix):
                        domain = domain[len(prefix):]
                domain = domain.strip("/").strip()
                if not domain:
                    return
                if "." not in domain:
                    domain += ".com"
                result = web_agency({"url": "https://" + domain,
                                     "action": "sign_in"})
                # ok=False is the NORMAL outcome here: the window is open and
                # the sign-in has not happened yet. Relayed as-is rather than
                # dressed up as an error.
                self.write_log("SYS: " + (result.message or "Sign-in window opened."))
                self._push("setSettings", snapshot())
            except Exception as e:
                self.write_log(f"SYS: Could not open the sign-in window: {e}")

        threading.Thread(target=_run, daemon=True).start()

    def disconnect_google(self):
        try:
            from actions.app_settings import disconnect_google
            self._push("setSettings", disconnect_google())
        except Exception as e:
            print(f"[aethelark_web] disconnect_google failed: {e}")

    def link_whatsapp(self):
        # Opens WhatsApp Web in the shared browser session so the user can scan
        # the QR once; the login then persists in that profile. Non-blocking.
        self.write_log("NET: Opening WhatsApp Web — scan the QR once to link.")
        def _flow():
            try:
                from actions.browser_control import _registry
                sess = _registry.get(None)
                async def _open(s):
                    page = await s._get_page()
                    await page.goto("https://web.whatsapp.com/",
                                    wait_until="domcontentloaded", timeout=45_000)
                    return "opened"
                sess.run(_open(sess), timeout=60)
            except Exception as e:
                self.write_log(f"ERR: WhatsApp link — {e}")
        threading.Thread(target=_flow, daemon=True).start()

    def rerun_onboarding(self):
        # Re-open the full ignition flow. on_done just refreshes the panel — the
        # main app keeps running behind it (config is merged, never wiped).
        def _done():
            try:
                self._reonboard.close()
            except Exception as _e:
                print(f"[aethelark_web.py] Non-fatal error at line 523: {_e}")
            self.push_settings()
        self._reonboard = OnboardingWindow(on_done=_done)
        self._reonboard.showMaximized()
        self._reonboard.raise_()

    # ---- pushing state to the web UI ----
    def _push(self, fn, payload):
        if self._ui_ready:
            self.dashboard.push(fn, payload)

    def _push_all(self):
        self._push("setState", "LISTENING" if not self._muted else "MUTED")
        self._push("setLog", self._log_lines[-40:])
        self._push_memory(); self._push_metrics(); self._push_swarm()

    def _push_memory(self):
        self._push("setMemory", self._memory_facts())

    def _memory_changed(self) -> bool:
        """True if the long-term memory file changed since we last read it.
        Avoids re-parsing memory from disk on every 2s tick (that GIL churn
        competed with the audio threads)."""
        try:
            from memory.memory_manager import MEMORY_PATH
            mtime = MEMORY_PATH.stat().st_mtime
        except Exception:
            return False
        if mtime != getattr(self, "_mem_mtime", None):
            self._mem_mtime = mtime
            return True
        return False

    def _push_metrics(self):
        s = _metrics.snapshot()
        gpu = f"{s['gpu']:.0f}%" if s['gpu'] >= 0 else "N/A"
        self._push("setMetrics", {"cpu": f"{s['cpu']:.0f}%",
                                  "mem": f"{s['mem']:.0f}%", "gpu": gpu})

    # ---- HARDCORE: the live swarm view ----
    # board status -> (lane css class [work|review|block], badge text)
    _SWARM_STMAP = {
        "working": ("work", "WORKING"), "review_blocked": ("block", "NEEDS YOU"),
        "failed": ("block", "FAILED"), "merged": ("review", "MERGED"),
        "stopped": ("review", "STOPPED"),
    }
    def _agent_label(self, assignee):
        """Name a swarm lane honestly, given how the agent is actually running.

        A CLI subprocess is "Claude Code"; the same agent driven through an SDK
        is "Claude Agent"; a local model is "Gemma Agent". Calling an SDK worker
        "Claude Code CLI" would claim a subprocess that does not exist.
        """
        from core.capability.identity import label_from_routing
        try:
            if self._routing is None:
                from core.capability.profile import load
                self._routing = load().route()
        except Exception:
            self._routing = None
        return label_from_routing(assignee, self._routing)

    def _swarm_view(self):
        """Transform the live swarm blackboard into the #swarm UI's shape.

        Returns None on error, an idle payload when no swarm is running, else the
        live mission/agents/timeline. This is what makes HARDCORE real instead of
        the static mockup — it reads the same blackboard the orchestrator writes.
        """
        try:
            from actions.swarm_orchestrator import swarm_snapshot
            snap = swarm_snapshot()
        except Exception:
            return None
        projects = snap.get("projects", {})
        best = None  # the active project = the one with the most registered agents
        for path, pdata in projects.items():
            n = len(pdata.get("agents", {}))
            if n and (best is None or n > len(best[1].get("agents", {}))):
                best = (path, pdata)
        if not best:
            return {"idle": True}

        import os
        import time as _t
        path, pdata = best
        agents = pdata.get("agents", {})
        decisions = pdata.get("decisions", [])
        sessions = snap.get("sessions", {})

        def age_for(worktree):
            if not worktree:
                return 0
            try:
                wt = os.path.realpath(worktree)
            except Exception:
                wt = worktree
            for k, v in sessions.items():
                sdir = k.split("@", 1)[-1]
                if sdir == wt or sdir == worktree:
                    return v.get("age_s", 0) or 0
            return 0

        def mmss(sec):
            sec = int(sec or 0)
            return f"{sec // 60}:{sec % 60:02d}"

        agent_list, merged, max_age = [], 0, 0
        for key, info in agents.items():
            st = info.get("status", "working")
            lane, badge = self._SWARM_STMAP.get(st, ("work", "WORKING"))
            if st == "merged":
                merged += 1
            assignee = info.get("assignee") or key
            name = self._agent_label(assignee)
            age = age_for(info.get("worktree", ""))
            max_age = max(max_age, age)
            agent_list.append({
                "glyph": (name[:1] or "•").upper(), "name": name,
                "branch": info.get("branch", ""), "lane": lane, "badge": badge,
                "thought": (info.get("last_thought") or "").strip() or "…",
                "elapsed": mmss(age) if age else "",
            })

        total = len(agents)
        s = _metrics.snapshot()
        repo = pathlib.Path(path).name
        mission = {
            "repo": repo, "worktrees": total, "merged": f"{merged} / {total}",
            "conflicts": 0, "progress": int(merged / total * 100) if total else 0,
            "cpu": f"{s['cpu']:.0f}%", "tasks": len(decisions), "elapsed": mmss(max_age),
            "conductor": f"{total} AGENT{'S' if total != 1 else ''} · {repo.upper()}",
            "state": "CONDUCTING",
        }
        timeline = [{
            "ts": _t.strftime("%H:%M", _t.localtime(d.get("ts", 0))) if d.get("ts") else "",
            "text": d.get("text", ""), "done": True,
        } for d in decisions[-14:]]
        return {"mission": mission, "agents": agent_list, "timeline": timeline}

    def _push_swarm(self):
        view = self._swarm_view()
        if view is None:
            return
        if view.get("idle"):
            view = {
                "mission": {"repo": "—", "worktrees": 0, "merged": "0 / 0",
                            "conflicts": 0, "progress": 0, "cpu": "0%", "tasks": 0,
                            "elapsed": "0:00", "conductor": "NO ACTIVE SWARM",
                            "state": "STANDBY"},
                "agents": [],
                "timeline": [{"ts": "", "text": "No active swarm. Say "
                              "“build me…” to start one.", "done": False}],
            }
        self._push("setSwarm", view)
        self._push_phase(view)

    # Institutional phase, not agent chatter. The pill used to read "2 working",
    # which is a subprocess count the user has to translate. What they want to
    # know is what is happening to THEIR thing.
    _PHASES = {
        "working": "Building your project",
        "review":  "Checking the work",
        "merged":  "Finishing up",
        "block":   "Needs your call",
    }

    def _push_phase(self, view):
        """Drive the pill from mission state so it narrates, not enumerates."""
        try:
            agents = view.get("agents") or []
            if not agents:
                return
            lanes = [a.get("lane") or "" for a in agents]
            needs = sum(1 for ln in lanes if ln == "block")
            working = sum(1 for ln in lanes if ln == "work")
            done = sum(1 for ln in lanes if ln == "review")
            total = max(1, len(lanes))

            if needs:
                phase = self._PHASES["block"]
            elif working:
                phase = self._PHASES["working"]
            elif done == total:
                # Everything merged — the preview opens separately; this is the
                # pill's own moment.
                self.pill_win.set_pill("ready", {"label": "Your project is ready"})
                return
            else:
                phase = self._PHASES["review"]

            self.pill_win.set_pill("swarm", {
                "working": working, "needs_you": needs, "total": total,
                "phase": phase,
                "progress": int(100 * done / total),
            })
        except Exception as e:
            print(f"[aethelark_web] phase push skipped: {e}")

    def _tick(self):
        # Metrics are cheap (a psutil snapshot); memory is only re-read + pushed
        # when the file actually changed, not every 2 seconds.
        self._push_metrics()
        self._push_swarm()
        if self._memory_changed():
            self._push_memory()

    def _memory_facts(self):
        try:
            mem = load_memory()
        except Exception:
            return []
        ident = mem.get("identity", {}) if isinstance(mem, dict) else {}

        def val(d, k):
            e = d.get(k)
            if isinstance(e, dict):
                return str(e.get("value") or "").strip()
            return str(e).strip() if isinstance(e, str) else ""

        facts = []
        for icon, label, key in (("◈", "You go by", "name"), ("⌖", "Based in", "city"),
                                 ("✦", "Work", "job"), ("◈", "Speaks", "language")):
            v = val(ident, key)
            if v:
                facts.append({"icon": icon, "label": label, "value": v})
        for cat, icon, label in (("projects", "⬢", "Building"),
                                 ("preferences", "⚡", None), ("wishes", "✧", "Wants")):
            for k, e in list(mem.get(cat, {}).items())[:1]:
                v = e.get("value") if isinstance(e, dict) else e
                if v:
                    facts.append({"icon": icon, "label": label or k.replace("_", " ").title(),
                                  "value": str(v)})
        return facts[:5]

    # ---- signal slots (main thread) ----
    def _on_state(self, state):
        self.pill_win.set_pill(self._pill_state(state))
        self._push("setState", state)

    def _pill_state(self, s):
        s = (s or "").upper()
        if s == "LISTENING":
            return "listening"
        if s == "SPEAKING":
            return "speaking"
        if s in ("THINKING", "PROCESSING", "WORKING"):
            return "thinking"
        return "idle"

    def _on_log(self, text):
        sp, msg = "sys", text
        if ":" in text:
            pre, rest = text.split(":", 1)
            p = pre.strip().lower()
            if p == "you":
                sp, msg = "you", rest.strip()
            elif p in ("sys", "err", "file"):
                sp, msg = "sys", rest.strip()
            elif p == "net":
                sp, msg = "net", rest.strip()
            elif p in ("swarm", "swm"):
                sp, msg = "swarm", rest.strip()
            else:
                sp, msg = "ae", rest.strip()   # assistant lines ("Aethelark: …")
        self._log_lines.append({"speaker": sp, "text": msg})
        self._log_lines = self._log_lines[-60:]
        self._push("setLog", self._log_lines[-40:])

    def _on_content(self, title, text):
        self._log_lines.append({"speaker": "ae", "text": f"{title}: {text[:400]}"})
        self._log_lines = self._log_lines[-60:]
        self._push("setLog", self._log_lines[-40:])

    def _on_reconfig(self):
        print("[aethelark_web] API key required — set config/api_keys.json, then relaunch.")

    # ---- the interface AethelarkLive expects (thread-safe) ----
    def set_state(self, state):
        # The playback loop re-asserts SPEAKING on EVERY audio frame (~20/s).
        # In the web app each change rebuilds the pill DOM via runJavaScript, so
        # firing 20x/s glitched the waveform and stole CPU from audio (the old
        # QPainter UI didn't care). Only act on real transitions.
        if state == self._last_state:
            return
        self._last_state = state
        self._state_sig.emit(state)
    def write_log(self, text): self._log_sig.emit(text)
    def set_audio_level(self, level): pass  # web pill waveform is CSS-animated
    def show_content(self, title, text): self._content_sig.emit(str(title)[:48], str(text)[:4000])
    def prompt_reconfig(self): self._win._ready = False; self._reconfig_sig.emit()

    def reconfig_complete(self) -> bool:
        """Has the user finished re-entering their credentials?"""
        return bool(self._win._ready)

    def request_shutdown(self) -> None:
        """Ask the UI to close."""
        self.root.quit()

    def notify_phone_connected(self): pass
    def show_camera_frame(self, img_bytes): pass
    def start_camera_stream(self): pass
    def stop_camera_stream(self): pass

    @property
    def assistant_name(self): return self._assistant_name

    @property
    def muted(self): return self._muted

    @muted.setter
    def muted(self, v):
        self._muted = bool(v)
        self.set_state("MUTED" if self._muted else "LISTENING")

    @property
    def current_file(self): return None

    def start_speaking(self): self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self._muted:
            self.set_state("LISTENING")

    def wait_for_api_key(self):
        while True:
            try:
                d = json.loads(API_KEYS.read_text(encoding="utf-8"))
                if d.get("gemini_api_key"):
                    self._win._ready = True
                    return
            except Exception as _e:
                print(f"[aethelark_web.py] Non-fatal error at line 686: {_e}")
            time.sleep(0.3)


ONBOARDING_HTML = BASE / "web" / "onboarding.html"


def _is_onboarded() -> bool:
    try:
        cfg = json.loads(API_KEYS.read_text(encoding="utf-8"))
        return bool(cfg.get("onboarded"))
    except Exception:
        return False


class OnboardBridge(QObject):
    """window.pybridge on the onboarding page."""
    def __init__(self, win): super().__init__(); self._win = win

    @pyqtSlot()
    def onboard_ready(self): self._win.on_ready()
    @pyqtSlot()
    def google_login(self): self._win.start_google_login()
    @pyqtSlot(str)
    def complete(self, payload): self._win.complete(payload)
    @pyqtSlot()
    def quit(self): QApplication.instance().quit()

    @pyqtSlot(int, int)
    def begin_drag(self, sx, sy):
        w = self._win; w._drag_origin = (sx, sy, w.x(), w.y())

    @pyqtSlot(int, int)
    def drag_to(self, sx, sy):
        w = self._win; o = getattr(w, "_drag_origin", None)
        if o:
            w.move(o[2] + (sx - o[0]), o[3] + (sy - o[1]))


class OnboardingWindow(QMainWindow):
    """First-run ignition flow. Renders web/onboarding.html, persists the user's
    choices to config, then hands control to on_done()."""
    _push_sig = pyqtSignal(str, str)   # (fn, json-payload) — marshals to GUI thread

    def __init__(self, on_done):
        super().__init__()
        self._on_done = on_done
        self.setWindowTitle("AETHELARK — Ignition")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view = QWebEngineView()
        self.view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view.page().setBackgroundColor(Qt.GlobalColor.transparent)
        self.setCentralWidget(self.view)
        self.channel = QWebChannel()
        self.bridge = OnboardBridge(self)
        self.channel.registerObject("pybridge", self.bridge)
        self.view.page().setWebChannel(self.channel)
        self._push_sig.connect(self._do_push)
        self.view.load(QUrl.fromLocalFile(str(ONBOARDING_HTML)))

    def _do_push(self, fn, payload):
        self.view.page().runJavaScript(
            "window.onboarding && window.onboarding.%s(%s)" % (fn, payload))

    def push(self, fn, payload):
        self._push_sig.emit(fn, json.dumps(payload))

    def on_ready(self):
        # Detect the machine off-thread (nvidia-smi / lspci can block briefly).
        # This is tier 1 of the capability audit: the one expensive full scan,
        # persisted so every later launch can load it in ~1ms instead of
        # rediscovering the machine — and so the eagle still knows what it is
        # running on when you ask it for something hard.
        def _probe():
            try:
                from core.capability.profile import save, scan
                profile = scan(full=True)
                save(profile)
                routing = profile.route()
                self.push("setMachine", profile.hardware)
                self.push("setCapabilities", {
                    "case": routing.case,
                    "label": routing.label,
                    "metric": routing.metric,
                    "reason": routing.reason,
                    "brain": routing.brain,
                    "labour": routing.labour,
                    "cli_agents": [a["key"] for a in profile.cli_agents],
                    "gui_apps": profile.gui_apps,
                    "providers": sorted(profile.providers),
                })
            except Exception as e:
                print(f"[onboarding] capability scan failed: {e}")
                try:
                    from actions.machine_profile import detect_machine
                    self.push("setMachine", detect_machine())
                except Exception as inner:
                    print(f"[onboarding] machine probe failed: {inner}")
        threading.Thread(target=_probe, daemon=True).start()

    def start_google_login(self):
        def _flow():
            try:
                from actions.google_auth import sign_in_google
                self.push("setAuth", sign_in_google())
            except Exception as e:
                self.push("setAuth", {"status": "error", "message": str(e)})
        threading.Thread(target=_flow, daemon=True).start()

    def complete(self, payload):
        try:
            data = json.loads(payload or "{}")
        except Exception:
            data = {}
        try:
            cfg = json.loads(API_KEYS.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}

        # Skip-safe: only overwrite a field when the flow actually provided a
        # value, so clicking "Skip" never wipes an already-configured setup.
        cfg["onboarded"] = True
        un = (data.get("user_name") or "").strip()
        if un:
            cfg["user_name"] = un
        elif "user_name" not in cfg:
            cfg["user_name"] = ""
        addr = data.get("address_style") or ""
        if addr:
            cfg["address_style"] = addr
        cfg["brain_mode"] = data.get("brain_mode") or cfg.get("brain_mode") or "api"
        cfg["auth_provider"] = data.get("auth") or cfg.get("auth_provider") or "guest"
        if cfg["brain_mode"] == "api":
            prov = data.get("provider") or cfg.get("brain_provider") or "google"
            key = (data.get("api_key") or "").strip()
            cfg["brain_provider"] = prov
            if key:
                # The live voice runtime is Gemini today, so a Google key becomes
                # the runtime key; other providers are stored for the (later)
                # multi-brain router.
                if prov == "google":
                    cfg["gemini_api_key"] = key
                else:
                    cfg["brain_api_key"] = key
        try:
            API_KEYS.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
        except Exception as e:
            print(f"[onboarding] could not write config: {e}")

        self.close()
        self._on_done()


def _launch_main_app():
    ui = WebShellUI("face.png")

    def runner():
        ui.wait_for_api_key()
        from main import AethelarkLive
        import asyncio
        live = AethelarkLive(ui)
        try:
            asyncio.run(live.run())
        except KeyboardInterrupt:
            pass

    threading.Thread(target=runner, daemon=True).start()
    # keep a reference so the shell isn't garbage-collected
    QApplication.instance()._aethelark_ui = ui


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    load_app_fonts()
    app.setStyle("Fusion")

    if _is_onboarded():
        _launch_main_app()
    else:
        onboard = OnboardingWindow(on_done=_launch_main_app)
        app._aethelark_onboard = onboard   # keep alive
        onboard.showMaximized()

    sys.exit(app.exec() or 0)


if __name__ == "__main__":
    main()
