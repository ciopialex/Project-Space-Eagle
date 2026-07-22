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
                          QRect, QPropertyAnimation)
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QApplication, QMainWindow
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebChannel import QWebChannel

from ui import load_app_fonts, _metrics, make_spring_curve
from memory.memory_manager import load_memory

BASE = pathlib.Path(__file__).resolve().parent
DASHBOARD_HTML = BASE / "web" / "dashboard.html"
PILL_HTML = BASE / "web" / "pill.html"
API_KEYS = BASE / "config" / "api_keys.json"
PILL_W, PILL_H = 340, 120


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


class DashWindow(QMainWindow):
    def __init__(self, ui):
        super().__init__()
        self._ui = ui
        self.setWindowTitle("AETHELARK")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.view = QWebEngineView()
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


class WebShellUI(QObject):
    """The object AethelarkLive drives. Thread-safe: backend runs in a worker
    thread and calls these; GUI work is marshalled to the main thread via signals."""
    _state_sig    = pyqtSignal(str)
    _log_sig      = pyqtSignal(str)
    _audio_sig    = pyqtSignal(float)
    _content_sig  = pyqtSignal(str, str)
    _reconfig_sig = pyqtSignal()

    def __init__(self, face_path="face.png"):
        super().__init__()
        self._app = QApplication.instance() or QApplication(sys.argv)
        load_app_fonts()
        self._app.setStyle("Fusion")

        self.mode = "casual"
        self.pinned = False
        self._muted = False
        self._log_lines = []
        self._assistant_name = "Aethelark"
        self._ui_ready = False

        self.on_text_command = None
        self.on_remote_clicked = None
        self.on_interrupt = None

        self._win = _WinShim()
        self.root = _RootShim(self._app)

        self.pill_win = PillWebWindow(self)
        self.dashboard = DashWindow(self)
        self.dashboard.hide()

        geo = QApplication.primaryScreen().availableGeometry()
        self._pill_geo = QRect((geo.width() - PILL_W) // 2, 6, PILL_W, PILL_H)
        self._screen_geo = geo
        self._anim = None

        self._state_sig.connect(self._on_state)
        self._log_sig.connect(self._on_log)
        self._content_sig.connect(self._on_content)
        self._reconfig_sig.connect(self._on_reconfig)

        QShortcut(QKeySequence("F4"), self.dashboard, activated=self.toggle_mute)

        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._tick)
        self._metric_tmr.start(2000)

        self.show_pill()

    # ---- window transitions (main thread) ----
    def show_pill(self):
        self.pill_win.setGeometry(self._pill_geo)
        self.pill_win.show(); self.pill_win.raise_()

    def open_dashboard(self):
        self.pill_win.hide()
        self.dashboard.setGeometry(self._pill_geo)
        self.dashboard.show()
        self.dashboard.activateWindow(); self.dashboard.raise_()
        self._animate_dash(self._pill_geo, self._screen_geo)
        self._push_all()

    def collapse_to_pill(self):
        self._animate_dash(self.dashboard.geometry(), self._pill_geo, on_done=self._after_collapse)

    def _after_collapse(self):
        self.dashboard.hide()
        self.show_pill()

    def _animate_dash(self, start, end, on_done=None):
        """Snappy iOS spring morph between the pill and the full dashboard."""
        if self._anim is not None:
            self._anim.stop()
        self._anim = QPropertyAnimation(self.dashboard, b"geometry")
        self._anim.setDuration(550)   # matches the artifact's .55s collapse
        self._anim.setStartValue(QRect(start))
        self._anim.setEndValue(QRect(end))
        self._anim.setEasingCurve(make_spring_curve(260.0, 24.0))  # the artifact's exact spring
        if on_done:
            self._anim.finished.connect(on_done)
        self._anim.start()

    def on_dashboard_blur(self):
        if self.mode == "casual" and not self.pinned:
            QTimer.singleShot(120, self._blur_collapse)

    def _blur_collapse(self):
        if (self.dashboard.isVisible() and not self.dashboard.isActiveWindow()
                and self.mode == "casual" and not self.pinned):
            self.collapse_to_pill()

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

    # ---- pushing state to the web UI ----
    def _push(self, fn, payload):
        if self._ui_ready:
            self.dashboard.push(fn, payload)

    def _push_all(self):
        self._push("setState", "LISTENING" if not self._muted else "MUTED")
        self._push("setLog", self._log_lines[-40:])
        self._push_memory(); self._push_metrics()

    def _push_memory(self):
        self._push("setMemory", self._memory_facts())

    def _push_metrics(self):
        s = _metrics.snapshot()
        gpu = f"{s['gpu']:.0f}%" if s['gpu'] >= 0 else "N/A"
        self._push("setMetrics", {"cpu": f"{s['cpu']:.0f}%",
                                  "mem": f"{s['mem']:.0f}%", "gpu": gpu})

    def _tick(self):
        self._push_metrics(); self._push_memory()

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
    def set_state(self, state): self._state_sig.emit(state)
    def write_log(self, text): self._log_sig.emit(text)
    def set_audio_level(self, level): pass  # web pill waveform is CSS-animated
    def show_content(self, title, text): self._content_sig.emit(str(title)[:48], str(text)[:4000])
    def prompt_reconfig(self): self._win._ready = False; self._reconfig_sig.emit()
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
            except Exception:
                pass
            time.sleep(0.3)


def main():
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
    sys.exit(ui.root.mainloop() or 0)


if __name__ == "__main__":
    main()
