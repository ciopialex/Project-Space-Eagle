"""Aethelark web-rendered shell (Phase 0/1 of the web pivot).

Two windows in ONE process:
  • the native QPainter pill (reused from ui.PillWidget) — always-on-top,
    top-centre; the ambient face.
  • a frameless QWebEngineView that renders web/dashboard.html — the EXACT
    web artifact — bridged to Python via QWebChannel (window.pybridge).

Locked interaction model (Aethelark_Web_Pivot_Plan.md §2):
  eagle → pill only → double-click pill → spring-expand into the maximized
  dashboard (pre-warmed) → Collapse / (blur in CASUAL, unpinned) → back to pill;
  HARDCORE stays open on blur.

Run standalone:  QT_QPA_PLATFORM=xcb .venv/bin/python web_shell.py
This module does NOT touch main.py/ui.py's app — integration is a later step.
"""
import json
import pathlib

from PyQt6.QtCore import Qt, QObject, pyqtSlot, QUrl, QEvent, QTimer
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QMainWindow
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebChannel import QWebChannel

from ui import PillWidget, load_app_fonts, make_spring_curve  # reuse native pieces

BASE = pathlib.Path(__file__).resolve().parent
DASHBOARD_HTML = BASE / "web" / "dashboard.html"


class Bridge(QObject):
    """window.pybridge — actions the web UI calls on Python."""

    def __init__(self, shell):
        super().__init__()
        self._shell = shell

    @pyqtSlot()
    def ready(self):
        print("[bridge] web UI ready")

    @pyqtSlot()
    def collapse(self):
        self._shell.collapse_to_pill()

    @pyqtSlot()
    def minimize(self):
        self._shell.dashboard.showMinimized()

    @pyqtSlot()
    def quit(self):
        QApplication.instance().quit()

    @pyqtSlot(str)
    def send_command(self, text):
        # Phase 2: dispatch to the daemon (on_text_command)
        print(f"[bridge] command: {text}")

    @pyqtSlot(str)
    def set_mode(self, mode):
        self._shell.mode = "hardcore" if mode.lower() == "hardcore" else "casual"
        print(f"[bridge] mode → {self._shell.mode}")


class DashboardWindow(QMainWindow):
    def __init__(self, shell):
        super().__init__()
        self._shell = shell
        self.setWindowTitle("AETHELARK")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)

        self.view = QWebEngineView()
        self.setCentralWidget(self.view)

        self.channel = QWebChannel()
        self.bridge = Bridge(shell)
        self.channel.registerObject("pybridge", self.bridge)
        self.view.page().setWebChannel(self.channel)
        self.view.load(QUrl.fromLocalFile(str(DASHBOARD_HTML)))

    def push(self, fn, payload):
        """Daemon → UI: call window.aethelark.<fn>(<payload>). This is the
        Python side of the message contract (setState/setLog/setMemory/
        setMetrics/setMode/setSwarm). Safe to call any time; no-ops if the
        page isn't ready yet."""
        js = "window.aethelark && window.aethelark.%s(%s)" % (fn, json.dumps(payload))
        self.view.page().runJavaScript(js)

    def changeEvent(self, e):
        # Mode-aware blur: CASUAL dashboard is ephemeral, HARDCORE persists.
        if (e.type() == QEvent.Type.ActivationChange
                and self.isVisible() and not self.isActiveWindow()):
            self._shell.on_dashboard_blur()
        super().changeEvent(e)


class PillHost(QWidget):
    """Frameless, translucent, always-on-top host for the native pill."""

    def __init__(self, shell):
        super().__init__()
        self._shell = shell
        self._drag = None
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.pill = PillWidget()
        lay.addWidget(self.pill)
        self.pill.installEventFilter(self)
        self.setFixedSize(240, 84)

    def eventFilter(self, obj, ev):
        if obj is self.pill:
            t = ev.type()
            if t == QEvent.Type.MouseButtonDblClick:
                self._shell.open_dashboard()
                return True
            if t == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton:
                self._drag = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
            elif t == QEvent.Type.MouseMove and self._drag and (ev.buttons() & Qt.MouseButton.LeftButton):
                self.move(ev.globalPosition().toPoint() - self._drag)
            elif t == QEvent.Type.MouseButtonRelease:
                self._drag = None
        return super().eventFilter(obj, ev)


class Shell(QObject):
    """Owns the pill + dashboard and the transitions between them."""

    def __init__(self):
        super().__init__()
        self.mode = "casual"
        self.pinned = False

        self.pill_host = PillHost(self)
        self.dashboard = DashboardWindow(self)   # pre-warmed, hidden
        self.dashboard.hide()

        self.show_pill()

    def show_pill(self):
        geo = QApplication.primaryScreen().availableGeometry()
        w, h = 240, 84
        self.pill_host.move((geo.width() - w) // 2, 12)
        self.pill_host.show()
        self.pill_host.raise_()

    def open_dashboard(self):
        self.pill_host.hide()
        self.dashboard.showMaximized()
        self.dashboard.activateWindow()
        self.dashboard.raise_()

    def collapse_to_pill(self):
        self.dashboard.hide()
        self.show_pill()

    def on_dashboard_blur(self):
        if self.mode == "casual" and not self.pinned:
            # small delay so a transient focus flicker doesn't collapse us
            QTimer.singleShot(120, self._blur_collapse)

    def _blur_collapse(self):
        if (self.dashboard.isVisible() and not self.dashboard.isActiveWindow()
                and self.mode == "casual" and not self.pinned):
            self.collapse_to_pill()


def main():
    import sys
    app = QApplication.instance() or QApplication(sys.argv)
    load_app_fonts()
    app.setStyle("Fusion")
    shell = Shell()
    app._aethelark_shell = shell   # keep alive
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
