"""Aethelark: the app's entry point.

Runs the voice backend (main.AethelarkLive) behind the island (web/pill.html) and
the dashboard (web/dashboard.html). WebShellUI is the interface AethelarkLive
talks to. The installed `eagle` command execs this file.

Run:  .venv/bin/python aethelark_web.py
"""
import json
import sys

# Line-buffered, so a crash does not lose the end of a redirected log.
from core import logsetup  # noqa: E402,F401
import os
# Software compositing: GLX pixmap import aborts Qt at random ("Failed to restore OpenGL
# context"). WebGL and raster stay on the GPU.
if sys.platform.startswith("linux"):
    _flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    if "--disable-gpu-compositing" not in _flags:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (_flags + " --disable-gpu-compositing").strip()
import threading
import time
import pathlib

from PyQt6.QtCore import (Qt, QObject, pyqtSlot, pyqtSignal, QUrl, QEvent, QTimer,
                          QRect)
from PyQt6.QtGui import QKeySequence, QShortcut, QRegion
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLabel, QPushButton)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PyQt6.QtWebChannel import QWebChannel

from ui import load_app_fonts, _metrics, make_spring_curve
from memory.memory_manager import load_memory
from core import user_paths

BASE = pathlib.Path(__file__).resolve().parent
DASHBOARD_HTML = BASE / "web" / "dashboard.html"
PILL_HTML = BASE / "web" / "pill.html"
API_KEYS = user_paths.api_keys_path()
# The island window is sized once, for the largest state, and never resized: resizing
# relayouts Chromium every frame. Every morph happens inside the page.
# ISLAND_TOP_PAD must match `.stage{padding-top}` in pill.html. ISLAND_MARGIN is room
# for the shadow and the spring's overshoot.
ISLAND_TOP_PAD = 20
ISLAND_MARGIN = 44

# The floor: what the cards shipped today need.
_MIN_PILL_W, _MIN_PILL_H = 560, 480


def _island_window_size() -> tuple[int, int]:
    """Big enough for the largest card any installed module declares, never smaller
    than the floor. A broken manifest falls back to the floor.
    """
    width, height = _MIN_PILL_W, _MIN_PILL_H
    try:
        from core.module_bus import default_manifest_dirs, load_manifests
        for manifest in load_manifests(default_manifest_dirs()):
            for card in (manifest.island.cards if manifest.island else ()):
                width = max(width, card.width + 2 * ISLAND_MARGIN)
                height = max(height, card.height + ISLAND_TOP_PAD + ISLAND_MARGIN)
    except Exception as e:
        print(f"[aethelark_web] island sizes unreadable, using defaults: {e}")
    return width, height


def island_assets(manifest) -> tuple[str, str]:
    """The card template and stylesheet to register for a module. `css` names a file
    beside the manifest; its content is what gets registered.
    """
    from core.module_bus.bus import island_dirs
    tpl = css = ""
    for island in island_dirs(manifest.source, manifest.key):
        if not island.is_dir():
            continue
        tpl_f, css_f = island / manifest.template, island / manifest.stylesheet
        if not tpl and tpl_f.is_file():
            tpl = tpl_f.read_text(encoding="utf-8")
        if not css and css_f.is_file():
            css = css_f.read_text(encoding="utf-8")
    return tpl, css


def island_meta(manifest) -> dict:
    """What the page needs to know about a module's island to run its FSM:
    per-stage dwell, the field that names the subject, whether the large card
    has anything to fetch, and whether it carries live media."""
    raw = getattr(manifest, "island_config", None) or {}
    face = getattr(manifest, "island", None)
    meta: dict = {"depth": bool(face and any(c.prefetch for c in face.cards))}
    dwell = raw.get("dwell")
    if isinstance(dwell, dict):
        meta["dwell"] = {k: float(v) for k, v in dwell.items()
                         if k in ("capsule", "glance", "expanded")
                         and isinstance(v, (int, float)) and v > 0}
    about = raw.get("about")
    if isinstance(about, str) and about.strip():
        meta["about"] = about.strip()
    if isinstance(raw.get("live"), str) and raw["live"].strip():
        meta["live"] = True
    return meta


def island_logos(manifest) -> dict:
    """Company marks a module ships in island/logos.json, keyed by symbol."""
    from core.module_bus.bus import island_dirs
    for island in island_dirs(manifest.source, manifest.key):
        f = island / "logos.json"
        try:
            if f.is_file():
                data = json.loads(f.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
        except Exception as e:
            print(f"[aethelark_web] {manifest.key} logos unreadable: {e}")
    return {}


PILL_W, PILL_H = _island_window_size()

# No voice state interrupts a card: the voice shows in #plive, so the card and the
# waveform never share a node.


import ctypes
import ctypes.util

_X11_LIB = None
_XEXT_LIB = None
_X11_LOADED = False

# The installed X error handler, kept alive: Xlib holds only the raw pointer.
_X_ERROR_HANDLER = None


def _install_x_error_handler() -> None:
    """Stop an X protocol error from killing the process. X errors are asynchronous
    and Xlib's default handler calls exit(), so a Python except cannot catch them.
    Errors are swallowed: they are advisory, and printing would spam a frame loop.
    """
    global _X_ERROR_HANDLER
    if _X_ERROR_HANDLER is not None or _X11_LIB is None:
        return
    try:
        handler_t = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p,
                                     ctypes.c_void_p)
        _X_ERROR_HANDLER = handler_t(lambda display, event: 0)
        _X11_LIB.XSetErrorHandler(_X_ERROR_HANDLER)
    except Exception:
        _X_ERROR_HANDLER = None


class _XRectangle(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_short),
        ("y", ctypes.c_short),
        ("width", ctypes.c_ushort),
        ("height", ctypes.c_ushort),
    ]


def _init_x11():
    """Load libX11/libXext once, with explicit restype: the default c_int truncates a
    64-bit Display pointer.
    """
    global _X11_LIB, _XEXT_LIB, _X11_LOADED
    if _X11_LOADED:
        return _X11_LIB is not None and _XEXT_LIB is not None
    _X11_LOADED = True
    try:
        x11_path = ctypes.util.find_library("X11")
        xext_path = ctypes.util.find_library("Xext")
        if not (x11_path and xext_path):
            return False
        x11 = ctypes.cdll.LoadLibrary(x11_path)
        xext = ctypes.cdll.LoadLibrary(xext_path)
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XCloseDisplay.restype = ctypes.c_int
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        x11.XFlush.restype = ctypes.c_int
        x11.XFlush.argtypes = [ctypes.c_void_p]
        xext.XShapeCombineRectangles.restype = None
        xext.XShapeCombineRectangles.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.POINTER(_XRectangle), ctypes.c_int,
            ctypes.c_int, ctypes.c_int,
        ]
        _X11_LIB, _XEXT_LIB = x11, xext
        # Install before the first request: the default handler exits the process.
        _install_x_error_handler()
        return True
    except Exception:
        _X11_LIB = _XEXT_LIB = None
        return False


def _rounded_spans(x: int, y: int, w: int, h: int, radius: int):
    """A rounded rectangle as horizontal strips, so Qt's region and the X11 shape
    take the same list.
    """
    r = max(0, min(int(radius), min(w, h) // 2))
    spans = [(x, y + r, w, h - 2 * r)] if h - 2 * r > 0 else []
    for i in range(r):
        dy = r - i - 0.5
        dx = int(round(r - (max(0.0, r * r - dy * dy) ** 0.5)))
        width = w - 2 * dx
        if width <= 0:
            continue
        spans.append((x + dx, y + i, width, 1))
        spans.append((x + dx, y + h - 1 - i, width, 1))
    return spans


def _apply_x11_input_shape(widget, spans) -> bool:
    """Set the window's input shape, leaving its pixels unclipped, so the drop shadow
    stays smooth.
    """
    if not spans or not _init_x11():
        return False
    display = None
    try:
        display = _X11_LIB.XOpenDisplay(None)
        if not display:
            return False
        array = (_XRectangle * len(spans))()
        for i, (sx, sy, sw, sh) in enumerate(spans):
            array[i] = _XRectangle(x=int(sx), y=int(sy),
                                   width=int(sw), height=int(sh))
        # ShapeInput = 2, ShapeSet = 0, Unsorted = 0
        _XEXT_LIB.XShapeCombineRectangles(
            display, ctypes.c_ulong(int(widget.winId())), 2, 0, 0,
            array, len(spans), 0, 0)
        _X11_LIB.XFlush(display)
        return True
    except Exception:
        return False
    finally:
        if display:
            try:
                _X11_LIB.XCloseDisplay(display)
            except Exception:
                pass


def apply_hit_region(widget, x: int, y: int, w: int, h: int,
                     radius: int = 32) -> str:
    """Make only the visible card accept the mouse. Returns "x11" or "mask" and never
    returns without one in place. The X11 input shape is preferred; the Qt mask
    clips the shadow.
    """
    from PyQt6.QtGui import QRegion

    spans = _rounded_spans(x, y, w, h, radius)
    if _apply_x11_input_shape(widget, spans):
        widget.clearMask()          # only now: the shape is already in place
        return "x11"

    region = QRegion()
    for sx, sy, sw, sh in spans:
        region = region.united(QRegion(sx, sy, sw, sh))
    widget.setMask(region)
    return "mask"


class _RootShim:
    def __init__(self, app): self._app = app
    def mainloop(self): return self._app.exec()
    def protocol(self, *_): pass
    def quit(self): self._app.quit()


class _WinShim:
    """Stands in for ui._win — the backend only touches ._ready."""
    def __init__(self): self._ready = False


class _LocalPage(QWebEnginePage):
    """A page that can only show the app's own files: the bridge installs modules and
    runs tools, so links open in the user's browser instead.
    """

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if not is_main_frame or url.isLocalFile() or url.scheme() in ("about", "data", "qrc"):
            return True
        if nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            from PyQt6.QtGui import QDesktopServices
            QDesktopServices.openUrl(url)
        return False


def _local_view() -> QWebEngineView:
    view = QWebEngineView()
    view.setPage(_LocalPage(view))
    return view


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
    @pyqtSlot()
    def interrupt(self): self._ui.request_stop()
    @pyqtSlot()
    def halt_swarm(self): self._ui.halt_swarm()
    @pyqtSlot(str)
    def set_mode(self, mode): self._ui.set_mode(mode)
    @pyqtSlot(str)
    def module_get(self, name): self._ui.install_module(name)
    @pyqtSlot(str)
    def module_remove(self, name): self._ui.remove_module(name)
    @pyqtSlot()
    def toggle_mute(self): self._ui.toggle_mute()

    @pyqtSlot(result=str)
    def aesthetic_options(self):
        """Sections + words for the picker to render."""
        import json as _j
        from core import aesthetics
        return _j.dumps(aesthetics.options())

    @pyqtSlot(str, str, result=str)
    def set_aesthetic(self, choices_json, free_text):
        """Keep the user's taste until they say "build it"."""
        import json as _j
        from actions.swarm_orchestrator import set_aesthetic
        try:
            choices = _j.loads(choices_json) if choices_json else None
        except ValueError:
            choices = None
        brief = set_aesthetic(choices, free_text or "")
        return "Saved — I'll build to that look." if brief else "Cleared."

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
    def connect_youtube(self): self._ui.connect_youtube()
    @pyqtSlot()
    def chrome_setup(self): self._ui.chrome_setup()
    @pyqtSlot()
    def chrome_off(self): self._ui.chrome_off()
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


def _module_accounts() -> dict:
    """{module key: [{site, why, signed_in}]} for every installed module that
    declares an account, read from disk.
    """
    try:
        from actions.grounding.web import sessions
        from core import user_paths
        from core.module_bus import accounts, default_manifest_dirs
        from core.module_bus.manifest import load_manifests
        return accounts.status(load_manifests(default_manifest_dirs()),
                               sessions.signed_in_sites(user_paths.browser_profile_dir()))
    except Exception as e:
        print(f"[aethelark_web] module accounts unavailable: {e}")
        return {}


def _lend_accounts_after_install(key: str) -> list:
    """Hand a just-installed module any session the browser already holds. Returns
    the sites that still need a sign-in.
    """
    if not key:
        return []
    try:
        from actions.grounding.web import sessions
        from core import user_paths
        from core.module_bus import accounts, default_manifest_dirs, which_module
        from core.module_bus.manifest import load_manifests
        mods = [m for m in load_manifests(default_manifest_dirs()) if m.key == key]
        signed = sessions.signed_in_sites(user_paths.browser_profile_dir())
        from actions.grounding.web import chrome_link
        if chrome_link.usable():
            signed += [a.site for m in mods for a in m.accounts]
        done = accounts.sync(mods, signed, which_module)
        lent = {site for _, site, r in done if r.get("ok")}
        return [a.site for m in mods for a in m.accounts if a.site not in lent]
    except Exception as e:
        print(f"[aethelark_web] could not lend accounts to {key}: {e}")
        return []


class DashWindow(QMainWindow):
    def __init__(self, ui):
        super().__init__()
        self._ui = ui
        self.setWindowTitle("AETHELARK")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view = _local_view()
        self.view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view.page().setBackgroundColor(Qt.GlobalColor.transparent)
        self.setCentralWidget(self.view)
        self.channel = QWebChannel()
        self.bridge = WebBridge(ui)
        self.channel.registerObject("pybridge", self.bridge)
        self.view.page().setWebChannel(self.channel)
        self._push_sig.connect(self._push_on_gui_thread)
        self.view.load(QUrl.fromLocalFile(str(DASHBOARD_HTML)))

    # QWebEnginePage is GUI-thread-only and the dispatcher thread can reach _push, so it
    # is marshalled here.
    _push_sig = pyqtSignal(str, str)

    def push(self, fn, payload):
        self._push_sig.emit(fn, json.dumps(payload))

    def _push_on_gui_thread(self, fn: str, payload_json: str) -> None:
        self.view.page().runJavaScript(
            "window.aethelark && window.aethelark.%s(%s)" % (fn, payload_json))

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

    @pyqtSlot(str, str)
    def answer_request(self, esc_id, verdict):
        """Allow / Don't allow, tapped on the island."""
        self._ui.answer_request(esc_id, verdict)

    @pyqtSlot()
    def interrupt(self):
        """A tap on the island while the eagle is talking: stop."""
        self._ui.request_stop()

    @pyqtSlot()
    def open_key_settings(self):
        """A tap on the 'key refused' notice."""
        self._ui._on_reconfig()

    @pyqtSlot(str, str)
    def request_depth(self, module, ticker):
        """The page drilled into a view the card on screen cannot fill."""
        self._ui.fetch_card_depth(module or "", ticker or "")

    @pyqtSlot(str, str)
    def prefetch_depth(self, module, ticker):
        self._ui.fetch_card_depth(module or "", ticker or "", quiet=True)

    @pyqtSlot(int, int, int, int)
    def hit_region(self, x, y, w, h):
        """The page telling Qt which pixels are the island. Called on every geometry
        change; everything else stays transparent to the mouse.
        """
        try:
            apply_hit_region(self._ui.pill_win, x, y, w, h)
        except Exception as e:
            print(f"[aethelark_web] hit region ignored: {e}")

    @pyqtSlot(int)
    def deck_size(self, size):
        """How many candidates are on screen, so a jump can be bounds-checked."""
        self._ui._deck_len = int(size)

    @pyqtSlot(bool)
    def deck_state(self, is_open):
        """The page telling Python whether a row of candidates is on screen. Pushed,
        not polled: it is read on the voice turn path.
        """
        self._ui._deck_open = bool(is_open)

    @pyqtSlot(str)
    def island_subject(self, line):
        """One line saying what the island is showing, on change only."""
        self._ui.note_island_subject(line)

    @pyqtSlot(str)
    def fleet_state(self, keys_json):
        """The printers the module reported, so a misheard name can be caught."""
        self._ui._deck_fleet = keys_json or "[]"

    @pyqtSlot(str)
    def selection_state(self, picks_json):
        """The page telling Python which candidates are picked, and where for."""
        self._ui._deck_picks = picks_json or "[]"

    @pyqtSlot(str)
    def island_trace(self, line):
        """One island state change, into the session log."""
        print(f"[island] {line}")

    @pyqtSlot(str)
    def open_activity(self, key): self._ui.open_activity(key)

    @pyqtSlot(str)
    def refine_pick(self, value):
        handler = self._ui.on_refine_pick
        if handler is not None:
            handler(value)

    @pyqtSlot(str, str, str, str)
    def module_action(self, action_id, module, action, args_json):
        self._ui.module_action(action_id, module, action, args_json)

    @pyqtSlot()
    def island_collapsed(self):
        """The page's decay timer ended and the card is gone, so Python stops
        suppressing state repaints.
        """
        self._ui.note_island_collapsed()

    @pyqtSlot(str)
    def island_opened(self, module):
        """The page opened a card on the user's tap or word, not Python."""
        self._ui.note_island_opened(module or "")

    @pyqtSlot(int)
    def island_deadline(self, left_ms):
        """How long the card on screen has left, whenever that changes."""
        self._ui.note_island_deadline(int(left_ms))

    @pyqtSlot(str, str, bool)
    def live_media(self, module, subject, on):
        self._ui.live_media(module or "", subject or "", bool(on))

    @pyqtSlot(int, int)
    def begin_drag(self, sx, sy):
        w = self._ui.pill_win
        w._drag_origin = (sx, sy, w.x(), w.y())

    @pyqtSlot(int, int)
    def drag_to(self, sx, sy):
        w = self._ui.pill_win
        o = getattr(w, "_drag_origin", None)
        if o:
            w.move(o[2] + (sx - o[0]), o[3] + (sy - o[1]))


class PillWebWindow(QMainWindow):
    """Transparent, frameless, always-on-top window rendering web/pill.html."""

    # Every write to the page goes through this signal, from any thread: runJavaScript
    # is not thread-safe and tools call it from the dispatcher thread.
    _run_js_sig = pyqtSignal(str)

    def __init__(self, ui):
        super().__init__()
        self._ui = ui
        self._run_js_sig.connect(self._run_js_on_gui_thread)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view = _local_view()
        self.view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view.page().setBackgroundColor(Qt.GlobalColor.transparent)
        self.view.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
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
        # Keep the latest state, so it survives a set before the page loads and Chromium
        # throttling while hidden.
        self._pending = (state, data or {})
        self._apply()

    def set_island_ttl(self, seconds: float):
        """The page owns the decay clock because only it knows about hover; Python
        supplies the number.
        """
        self.run_js(
            "window.island && (window.island.ttlMs = %d);"
            % max(0, int(seconds * 1000)))

    def register_module(self, key: str, template: str = "", style: str = "",
                        logos: dict | None = None, meta: dict | None = None):
        # window.pill is the <div id="pill"> until the page's script replaces it, so
        # test for the function.
        js = ("window.pill && typeof window.pill.registerModule === 'function' "
              "&& window.pill.registerModule(%s, %s, %s, %s, %s);") % (
            json.dumps(key), json.dumps(template), json.dumps(style),
            json.dumps(logos or {}), json.dumps(meta or {})
        )
        self.run_js(js)

    def run_js(self, js: str) -> None:
        """Fire-and-forget JavaScript at the pill page, from any thread. Emitting a
        signal runs it on the GUI thread; the one place Python touches the page.
        """
        self._run_js_sig.emit(js)

    def _run_js_on_gui_thread(self, js: str) -> None:
        self.view.page().runJavaScript(js)

    def _apply(self):
        st, data = self._pending
        # Through run_js, so nothing bypasses the marshal. Guard on the function:
        # window.pill is the #pill div until the page script runs; loadFinished re-
        # applies _pending.
        self.run_js(
            "window.pill && typeof window.pill.set === 'function' "
            "&& window.pill.set(%s, %s)" % (json.dumps(st), json.dumps(data)))

    def showEvent(self, e):
        super().showEvent(e)
        self._apply()   # re-assert state on re-show (hidden views go stale)


class WebClipboardPanel(QWidget):
    """Clipboard panel: when the user copies text, offer Translate / Summarise /
    Explain / Fix.
    """
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
        # Log the size, never the content: the clipboard carries passwords and codes.
        words = len(text.split())
        self._preview.setText(f"{words} word{'' if words == 1 else 's'} copied")
        self.adjustSize()
        self.move(x, y)
        self.show(); self.raise_()
        self._timer.start(8000)


class WebShellUI(QObject):
    """The object AethelarkLive drives. Thread-safe: the backend calls from worker
    threads and GUI work is marshalled by signals.
    """
    # The waveform is CSS-animated, so the backend skips the per-frame level envelope.
    consumes_audio_level = True

    # Declared at class level so core.ui_contract can check conformance; the backend
    # assigns them at startup.
    on_text_command = None
    on_remote_clicked = None
    on_interrupt = None

    _state_sig        = pyqtSignal(str)
    _log_sig          = pyqtSignal(str)
    _audio_sig        = pyqtSignal(float)
    _content_sig      = pyqtSignal(str, str)
    _reconfig_sig     = pyqtSignal()
    _settings_sig     = pyqtSignal(dict)   # worker threads → push settings snapshot
    _pill_context_sig = pyqtSignal(str, dict, float, bool)  # (module, data, ttl_s, ambient) -> island morph
    _hearing_sig      = pyqtSignal(bool)
    _modules_sig      = pyqtSignal()
    _ambient_refresh_sig = pyqtSignal()
    _notice_sig       = pyqtSignal(str, str)   # (text, action) -> island notice
    _ask_sig          = pyqtSignal()           # a held request appeared or was answered
    _chrome_setup_sig = pyqtSignal()
    _shop_sig         = pyqtSignal()           # an install moved on -> repaint the shop
    _module_ready_sig = pyqtSignal(str)        # (title) an install finished
    _module_gone_sig = pyqtSignal()            # a module was removed

    def __init__(self, face_path="face.png"):
        super().__init__()
        self._app = QApplication.instance() or QApplication(sys.argv)
        _name_the_app(self._app)
        load_app_fonts()
        self._app.setStyle("Fusion")

        icon_file = BASE / "assets" / "images" / "app_icon.png"
        if not icon_file.is_file():
            icon_file = BASE / "web" / "public" / "favicon-512x512.png"
        if icon_file.is_file():
            from PyQt6.QtGui import QIcon
            self._app.setWindowIcon(QIcon(str(icon_file)))

        self.pinned = False

        # Modules the user brought up this session; until then a module reaches the
        # island only with live work (_ambient_may_speak).
        self._ambient_engaged: set[str] = set()
        # "<module>:<printer>" for every machine seen with a job this session, so only
        # the end of a watched job may speak.
        self._ambient_live_jobs: set[str] = set()
        from core.activities import ActivityBoard
        self._activity_board = ActivityBoard()
        self._asking = ""
        self._ask_sig.connect(self._sync_ask)
        from core import escalations
        escalations.add_listener(self._on_escalation_ui)
        self._activity_sig: tuple = ()
        self._agent_busy: dict[str, tuple[bool, float]] = {}
        self._level_at = 0.0
        self._muted = False
        self._last_state = None      # dedupe: backend re-asserts SPEAKING per audio frame
        self._active_pill_context = None
        # Whether what is on screen arrived unprompted (_may_take_the_screen).
        self._active_pill_is_ambient = False
        self._log_lines = []
        self._assistant_name = "Aethelark"
        self._ui_ready = False
        self._routing = None      # cached capability routing; drives lane labels

        self.on_text_command = None
        # Set by AethelarkLive: called with one line whenever the island starts showing
        # something different.
        self.on_island_subject = None
        self.on_remote_clicked = None
        self.on_interrupt = None
        #: Set by AethelarkLive: the user tapped the island to stop it talking.
        self.on_stop = None
        #: Set by AethelarkLive: the installed modules changed on disk.
        self.on_modules_changed = None
        self.on_coding_mode = None
        self.on_card_action = None
        self.on_session_refresh = None
        self.on_refine_pick = None
        # Whether the user is talking now, from the voice loop's speech detector.
        self._hearing = False
        self._reconfig_win = None

        self._win = _WinShim()
        self.root = _RootShim(self._app)

        self.pill_win = PillWebWindow(self)
        self.dashboard = DashWindow(self)
        self.dashboard.hide()

        geo = QApplication.primaryScreen().availableGeometry()
        self._pill_geo = QRect(geo.x() + (geo.width() - PILL_W) // 2, geo.y() + 6, PILL_W, PILL_H)
        # Expanded is a centred card at about 0.63 × 0.70 of the screen, not fullscreen.
        ew, eh = int(geo.width() * 0.63), int(geo.height() * 0.70)
        self._expanded_geo = QRect(geo.x() + (geo.width() - ew) // 2,
                                   geo.y() + (geo.height() - eh) // 2, ew, eh)

        self._state_sig.connect(self._on_state)
        self._log_sig.connect(self._on_log)
        self._content_sig.connect(self._on_content)
        self._reconfig_sig.connect(self._on_reconfig)
        self._settings_sig.connect(lambda snap: self._push("setSettings", snap))
        self._pill_context_sig.connect(self._on_pill_context)
        self._hearing_sig.connect(self._on_hearing)
        self._modules_sig.connect(self._on_modules_changed)
        self._ambient_refresh_sig.connect(self._refresh_ambient)
        self._chrome_setup_sig.connect(self.chrome_setup)
        self._notice_sig.connect(
            lambda text, action: self.pill_win.set_pill(
                "notice", {"text": text, "action": action}))
        # Installs started from Settings: name -> {"state", "step"}. Kept here so
        # closing Settings does not affect them.
        self._installs: dict[str, dict] = {}
        self._install_lock = threading.Lock()
        self._shop_sig.connect(self._push_shop)
        self._module_ready_sig.connect(self._on_module_ready)
        self._module_gone_sig.connect(lambda: self._mod_settle.start(200))

        self._live_lock = threading.Lock()
        self._live_want: dict = {}
        self._live_busy: set = set()
        self._pill_context_timer = QTimer(self)
        self._pill_context_timer.setSingleShot(True)
        self._pill_context_timer.timeout.connect(self._on_pill_context_expired)

        QShortcut(QKeySequence("F4"), self.dashboard, activated=self.toggle_mute)

        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._tick)
        self._metric_tmr.start(2000)

        # Clipboard assist, opt-in: the clipboard is where passwords and codes pass
        # through.
        self._last_clip = ""
        self._clip_panel = WebClipboardPanel()
        self._clip_panel.action_requested.connect(self._dispatch_command)
        try:
            self._app.clipboard().dataChanged.connect(self._on_clipboard_changed)
        except Exception as e:
            print(f"[aethelark_web] clipboard watch unavailable: {e}")

        # Module island templates and the modules folder watcher.
        self._setup_module_watcher()
        self._setup_ambient_events()
        self._prune_undo_journal()

        self.show_pill()

    def _prune_undo_journal(self):
        """Drop staged undo blobs nothing refers to any more. Best-effort; never
        stops a launch.
        """
        try:
            from core import journal
            removed = journal.prune()
            if removed:
                print(f"[journal] pruned {removed} staged blob(s) nothing "
                      f"refers to any more")
        except Exception as e:
            print(f"[journal] prune skipped: {e}")

    def _setup_module_watcher(self):
        """Register module island templates with the pill and watch the modules
        folder for installs and removals.
        """
        import os
        import pathlib
        from pathlib import Path
        from PyQt6.QtCore import QFileSystemWatcher
        from core.module_bus import default_manifest_dirs, load_manifests

        def _sync():
            self._fit_island_window()
            try:
                manifests = load_manifests(default_manifest_dirs())
                for m in manifests:
                    tpl, css = island_assets(m)
                    self.pill_win.register_module(m.key, tpl, css,
                                                  island_logos(m), island_meta(m))
            except Exception as e:
                print(f"[aethelark_web] module sync error: {e}")
            self.sync_island_prefs()
            self._push_suggestions()

        self._sync_modules = _sync
        self.pill_win.view.loadFinished.connect(lambda ok: _sync())

        user_mod_dir = str(default_manifest_dirs()[0])
        os.makedirs(user_mod_dir, exist_ok=True)
        # The settle timer turns a burst of writes from `<module> register` into one
        # refresh.
        self._mod_settle = QTimer(self)
        self._mod_settle.setSingleShot(True)
        self._mod_settle.timeout.connect(lambda: (_sync(), self._modules_sig.emit()))
        try:
            self._mod_watcher = QFileSystemWatcher([user_mod_dir], self)
            self._mod_watcher.directoryChanged.connect(
                lambda p: self._mod_settle.start(1500))
            self._mod_watcher.fileChanged.connect(
                lambda p: self._mod_settle.start(1500))
        except Exception as e:
            print(f"[aethelark_web] mod watcher error: {e}")


    def _fit_island_window(self):
        """Grow the island's window when a newly installed module brings a bigger
        card. One relayout per install; never shrinks.
        """
        try:
            w, h = _island_window_size()
            cur = self._pill_geo
            if w <= cur.width() and h <= cur.height():
                return
            w, h = max(w, cur.width()), max(h, cur.height())
            screen = QApplication.primaryScreen().availableGeometry()
            self._pill_geo = QRect(screen.x() + (screen.width() - w) // 2,
                                   cur.y(), w, h)
            self.pill_win.resize(w, h)
            if self.pill_win.isVisible():
                self.pill_win.setGeometry(self._pill_geo)
            print(f"[aethelark_web] island window grown to {w}x{h} for a new card")
        except Exception as e:
            print(f"[aethelark_web] island window left as it was: {e}")

    def _setup_ambient_events(self):
        """Let the modules speak without being asked."""
        from PyQt6.QtCore import QTimer

        from core.ambient import AmbientWatcher, ListenerSupervisor, listener_specs

        try:
            from main import MODULE_BUS
            manifests = [m for m in MODULE_BUS._manifests
                         if getattr(m, "events", None)]
        except Exception as e:
            print(f"[ambient] module bus unavailable: {e}")
            return
        if not manifests:
            return

        self._ambient = AmbientWatcher(
            {m.key: m.events.island_file for m in manifests},
            {m.key: m.events.priority for m in manifests if m.events.priority})
        self._ambient_listeners = ListenerSupervisor(
            listener_specs(manifests, MODULE_BUS._resolved))

        # Two seconds: these events are minutes apart, and a stat() per module is cheap.
        self._ambient_timer = QTimer(self)
        self._ambient_timer.timeout.connect(self._drain_ambient_events)
        self._ambient_timer.start(2000)

        # Clear listeners left by an eagle that died: enough of them exhaust a printer's
        # websocket.
        self._ambient_listeners.sweep_orphans()

        started = self._ambient_listeners.ensure_running()
        print(f"[ambient] watching {len(manifests)} module(s); "
              f"started {started or 'no'} listener(s)")

    # A machine with a job on it right now may speak unasked.
    _LIVE_JOB_STATES = frozenset({
        "PRINTING", "PAUSED", "PREPARING", "HEATING", "LEVELING", "UPLOADING"})

    # How a job ends: these speak only about a job this session watched start.
    _JOB_ENDED_STATES = frozenset({"COMPLETED", "STOPPED", "ERROR"})
    _STATUS_ONLY_STATES = frozenset({"IDLE", "STANDBY", "DISCONNECTED", "OFFLINE",
                                     "UNREACHABLE", "UNKNOWN", "UPLOADING"})

    def _ambient_may_speak(self, event) -> bool:
        """Whether an unasked-for event has earned the island: the user brought this
        module up this session, or a job is running or just ended on a watched
        machine. Anything else is a status.
        """
        payload = event.payload if isinstance(event.payload, dict) else {}
        telemetry = payload.get("telemetry")
        state = ""
        if isinstance(telemetry, dict):
            state = str(telemetry.get("state") or "").upper()
        # The state can arrive as "PrinterState.PRINTING", so match the tail.
        state = state.rsplit(".", 1)[-1]

        # An idle machine is status, not news, even in a session that used the module.
        if state in self._STATUS_ONLY_STATES:
            return False
        if event.module in self._ambient_engaged:
            return True
        who = f"{event.module}:{payload.get('printer') or ''}"

        if state in self._LIVE_JOB_STATES:
            self._ambient_live_jobs.add(who)
            return True
        if state in self._JOB_ENDED_STATES and who in self._ambient_live_jobs:
            self._ambient_live_jobs.discard(who)
            return True
        return False

    def _drain_ambient_events(self):
        """Show the most important thing a module has said since last look."""
        try:
            self._ambient_listeners.ensure_running()
            events = self._ambient.poll()
        except Exception as e:
            print(f"[ambient] poll failed: {e}")
            return
        for module, payload in self._ambient.heard.items():
            self._activity_board.heard(module, payload)
        cards = []
        for e in events:
            kind = self._activity_board.apply(e.module, e.payload)
            if kind is None:
                if self._ambient_may_speak(e):
                    cards.append(e)
                else:
                    print(f"[ambient] {e.module} {e.event}: status only, not shown")
            elif kind != "gone" and self._activity_board.first_alert(e.module, e.payload):
                print(f"[ambient] {e.module} {e.event}: activity {kind}, alert {e.payload.get('alert')}")
                cards.append(e)
            elif kind in ("started", "ended", "gone"):
                print(f"[ambient] {e.module} {e.event}: activity {kind}")
        self._push_activities()
        if not cards:
            return

        allowed = cards
        if not allowed:
            # Quiet and not a failure: a fleet nobody is using.
            return

        # poll() has ordered by the module's priority: show the head, drop the rest.
        top = allowed[0]
        if not self._may_take_the_screen(top):
            return
        if len(allowed) > 1:
            print(f"[ambient] {len(allowed)} events; showing {top.event} "
                  f"(priority {top.priority})")

        # duration_ms: how long the module says its own event is worth looking at.
        ttl = float(top.payload.get("duration_ms") or 0) / 1000.0
        self.set_pill_context(top.module, dict(top.payload),
                              ttl_s=ttl if ttl > 0 else 8.0, ambient=True)

    # What an unprompted event must score to replace a card the user asked for. On a3d's
    # scale: errors, pauses and low spools clear it; progress and standby do not.
    # Nothing is dropped from the bus either way.
    AMBIENT_INTERRUPTS_A_CARD = 80

    def _may_take_the_screen(self, event) -> bool:
        """Whether an unprompted event may replace what is up: anything may take an
        empty island; only an interrupt-grade event replaces a card the user asked
        for; a card an earlier ambient event put up is replaceable.
        """
        if self._active_pill_context is None:
            return True
        if getattr(self, "_active_pill_is_ambient", False):
            return True
        if event.priority >= self.AMBIENT_INTERRUPTS_A_CARD:
            return True
        module, _data = self._active_pill_context
        print(f"[ambient] holding {event.module} {event.event} "
              f"(priority {event.priority} < {self.AMBIENT_INTERRUPTS_A_CARD}) "
              f"— the user is reading a {module} card they asked for")
        return False

    def _push_activities(self):
        views = self._activity_board.visible()
        sig = self._activity_board.signature(views)
        if sig == self._activity_sig:
            return
        self._activity_sig = sig
        print("[activities] " + (" | ".join(
            f"{v['key']} {v['trailing']}{' (stale)' if v['stale'] else ''}" for v in views)
            or "none"))
        self.pill_win.run_js("window.island && window.island.setActivities(%s)"
                             % json.dumps(views, default=str))

    def module_action(self, action_id: str, module: str, action: str, args_json: str):
        handler = self.on_card_action

        def _work():
            ok, message = False, "The eagle is still starting."
            if handler is not None:
                try:
                    args = json.loads(args_json or "{}")
                    ok, message = handler(module, action, args if isinstance(args, dict) else {})
                except Exception as e:
                    ok, message = False, f"That did not work: {e}"
            self.pill_win.run_js("window.island && window.island.actionDone(%s, %s, %s)"
                                 % (json.dumps(action_id), "true" if ok else "false",
                                    json.dumps(message or "")))
        threading.Thread(target=_work, daemon=True).start()

    def show_mode(self, mode: str):
        self._push("setMode", mode)

    def set_mode(self, mode: str):
        handler = self.on_coding_mode
        if handler is not None:
            threading.Thread(target=handler, args=(mode == "coding",),
                             daemon=True).start()

    def open_activity(self, key: str):
        if key.startswith("swarm:"):
            self.open_dashboard()
            self._push("setMode", "coding")
            return
        self.open_dashboard()

    def stop_ambient_events(self):
        """Kill the listeners. The eagle exiting must not leave them behind."""
        timer = getattr(self, "_ambient_timer", None)
        if timer is not None:
            timer.stop()
        listeners = getattr(self, "_ambient_listeners", None)
        if listeners is not None:
            listeners.stop_all()

    def _on_clipboard_changed(self):
        try:
            from core import prefs
            if not prefs.enabled("clipboard_assist_enabled"):
                return
            text = self._app.clipboard().text().strip()
        except Exception:
            return
        if len(text) < 10 or text == self._last_clip:
            return
        self._last_clip = text
        geo = QApplication.primaryScreen().availableGeometry()
        w = self._clip_panel.width() or 360
        self._clip_panel.show_for(text, geo.x() + (geo.width() - w) // 2, geo.y() + 96)

    def show_pill(self):
        self.pill_win.setGeometry(self._pill_geo)
        self.pill_win.show(); self.pill_win.raise_()

    # Expand and collapse animate inside the page; the window is shown at its final
    # size.
    _MORPH_OUT_MS = 260   # must cover the aeOut keyframe duration in web/dashboard.html

    def open_dashboard(self):
        # Show the card at full size; CSS aeIn grows it from the top centre.
        self.dashboard.setGeometry(self._expanded_geo)
        # Keep the window invisible for one frame so the CSS from-state applies first;
        # revealed at 24 ms regardless.
        self.dashboard.setWindowOpacity(0.0)
        self.dashboard.show()
        self.dashboard.activateWindow(); self.dashboard.raise_()
        self.pill_win.hide()
        self.dashboard.push("playMorph", "in")
        QTimer.singleShot(24, lambda: self.dashboard.setWindowOpacity(1.0))
        self._push_all()

    def collapse_to_pill(self):
        # Shrink toward the island (CSS aeOut), then hand off to the pill.
        self.dashboard.push("playMorph", "out")
        QTimer.singleShot(self._MORPH_OUT_MS, self._after_collapse)

    def _after_collapse(self):
        self.dashboard.hide()
        self.dashboard.setWindowOpacity(1.0)
        self.dashboard.setGeometry(self._expanded_geo)  # reset for next open
        self.show_pill()

    def on_dashboard_blur(self):
        # The dashboard does not collapse on focus loss, so it can sit beside another
        # window. It collapses only on the Collapse button, a voice command, or another
        # window going fullscreen.
        return

    def _on_ui_ready(self):
        self._ui_ready = True; self._push_all()

    def _dispatch_command(self, text):
        text = (text or "").strip()
        if not text:
            return
        self.write_log(f"You: {text}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(text,), daemon=True).start()

    def halt_swarm(self):
        """The eagle stops talking and every agent stops."""
        self.request_stop()

        def _halt():
            try:
                from actions.swarm_orchestrator import halt_all
                self.write_log(f"SWARM: {halt_all()}")
            except Exception as e:
                self.write_log(f"ERR: could not stop the agents: {e}")
        threading.Thread(target=_halt, daemon=True).start()

    def toggle_mute(self):
        self.muted = not self._muted

    def install_module(self, name: str) -> None:
        """"Get" in the shop. Installs in the background; the island says when it is
        ready and the running session picks it up.
        """
        from core.module_bus.installer import catalog_name
        name = catalog_name(name or "") or ""
        if not name:
            self.write_log("ERR: that module is not in the catalog, so it was not installed.")
            return
        if (self._installs.get(name) or {}).get("state") in ("queued", "installing"):
            return
        self._installs[name] = {"state": "queued", "step": "Waiting to start…"}
        self._shop_sig.emit()
        threading.Thread(target=self._install_job, args=(name,), daemon=True,
                         name=f"install-{name}").start()

    def remove_module(self, name: str) -> None:
        """"Remove" in the shop: its actions leave the eagle's tool list."""
        name = (name or "").strip()
        if not name:
            return
        threading.Thread(target=self._remove_job, args=(name,), daemon=True,
                         name=f"remove-{name}").start()

    def _remove_job(self, name: str) -> None:
        from core.module_bus import installer
        with self._install_lock:
            try:
                installer.remove(name)
            except Exception as e:
                first = (str(e).strip().splitlines() or ["It did not remove."])[0]
                self.write_log(f"ERR: removing {name} failed: {first}")
                self._shop_sig.emit()
                return
        self._installs.pop(name, None)
        self.write_log(f"SYS: {name} removed.")
        self._shop_sig.emit()
        self._module_gone_sig.emit()

    def _install_job(self, name: str) -> None:
        from core.module_bus import installer
        # One install at a time: they share the download tool and the modules folder.
        with self._install_lock:
            self._installs[name] = {"state": "installing", "step": "Starting…"}
            self._shop_sig.emit()

            def progress(msg: str) -> None:
                if msg and not msg.startswith(("✓", "If the eagle")):
                    self._installs[name] = {"state": "installing",
                                            "step": msg.rstrip("…").strip() + "…"}
                    self._shop_sig.emit()
            try:
                result = installer.install(name, interactive=False, progress=progress)
            except Exception as e:
                first = (str(e).strip().splitlines() or ["It did not install."])[0]
                self._installs[name] = {"state": "failed", "step": first[:200]}
                self._shop_sig.emit()
                self.write_log(f"ERR: installing {name} failed: {first}")
                return
        self._installs.pop(name, None)       # the catalog now says installed
        self.write_log(f"SYS: {result.get('title') or name} installed.")
        missing = _lend_accounts_after_install(str(result.get("key") or ""))
        self._shop_sig.emit()
        title = str(result.get("title") or name)
        self._module_ready_sig.emit(
            f"{title} is ready. Sign in to {missing[0]} in Settings" if missing
            else title)

    def _on_module_ready(self, title: str) -> None:
        """GUI thread. Load the module now, then say so on the island."""
        try:
            self._mod_settle.start(200)
        except Exception as e:
            print(f"[aethelark_web] module refresh not scheduled: {e}")
        label = title if " is ready" in title else f"{title} is ready"
        QTimer.singleShot(1200, lambda: self.pill_win.set_pill(
            "ready", {"label": label}))

    def _push_shop(self) -> None:
        """GUI thread. The catalog, with any install in flight laid over it."""
        try:
            from core.module_bus import installer
            items = installer.shop()
        except Exception as e:
            print(f"[aethelark_web] module shop unavailable: {e}")
            items = []
        needs = _module_accounts()
        for it in items:
            job = self._installs.get(it["name"])
            it["state"] = (job["state"] if job else
                           ("installed" if it.get("installed") else "available"))
            it["step"] = job["step"] if job else ""
            it["accounts"] = needs.get(it.get("key") or "", [])
        self._push("setModuleShop", items)

    def refresh_module_shop(self) -> None:
        """Any thread. Redraw the shop -- an account was just handed over."""
        self._shop_sig.emit()

    def push_settings(self):
        """Send a fresh settings snapshot to the panel, computed off the GUI thread
        (it shells out to xdg-settings).
        """
        def _work():
            try:
                from actions.app_settings import snapshot
                self._push_settings_async(snapshot())
            except Exception as e:
                print(f"[aethelark_web] settings snapshot failed: {e}")
            self._shop_sig.emit()
        threading.Thread(target=_work, daemon=True).start()

    def _push_settings_async(self, snap):
        self._settings_sig.emit(snap or {})

    def sync_island_prefs(self):
        """What the island rests on is the user's choice, read from prefs."""
        from core import prefs
        self.pill_win.run_js(
            "window.island && (window.island.restActivities = %s, window.island.setActivities(window.island._activities))"
            % ("true" if prefs.enabled("island_live_activities") else "false"))

    def save_settings(self, patch):
        try:
            data = json.loads(patch or "{}")
        except Exception:
            data = {}
        try:
            from actions.app_settings import save, set_brain_key
            key = (data.pop("brain_api_key", "") or "").strip()
            from core import prefs
            before = prefs.enabled("labs_tools_enabled")
            # Names live in the session's instructions, read when it connects: a
            # rename the live session never heard was answered with the old name.
            was = _config()
            renamed = any(k in data and (data[k] or "") != (was.get(k) or "")
                          for k in ("user_name", "assistant_name"))
            snap = save(data)
            if key:
                provider = (data.get("brain_provider") or snap["brain"]["provider"]
                            or "google").lower()
                if provider == "google":
                    self.set_brain_key("google", key)
                else:
                    snap = set_brain_key(provider, key)
            self._push("setSettings", snap)
            self.sync_island_prefs()
            if (renamed or prefs.enabled("labs_tools_enabled") != before) and self.on_session_refresh:
                threading.Thread(target=self.on_session_refresh, daemon=True).start()
        except Exception as e:
            print(f"[aethelark_web] save_settings failed: {e}")

    def set_autostart(self, on):
        try:
            from actions.app_settings import set_autostart
            self._push("setSettings", set_autostart(bool(on)))
        except Exception as e:
            print(f"[aethelark_web] autostart toggle failed: {e}")

    def set_brain_key(self, provider, key):
        """Check a new key with Google and keep it only if Google accepts it. Off the
        GUI thread. A key refused mid-session is replaced here too.
        """
        def _work():
            try:
                from actions.app_settings import set_brain_key, snapshot
                from core import gemini_key
                ok, message = gemini_key.check(key)
                snap = set_brain_key("google", key) if ok else snapshot()
                snap["key_ok"] = ok
                snap["key_message"] = "Key updated." if ok else message
                self._push_settings_async(snap)
                if ok:
                    self._win._ready = True
            except Exception as e:
                print(f"[aethelark_web] set_brain_key failed: {e}")
        threading.Thread(target=_work, daemon=True).start()

    def connect_google(self):
        def _flow():
            try:
                from actions.google_auth import sign_in_google
                res = sign_in_google()
                status = res.get("status")
                if status == "ok":
                    self.write_log(f"NET: Google connected — {res.get('email', '')}")
                elif status == "not_configured":
                    # No OAuth client is configured: say so.
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

    def connect_youtube(self):
        """Connect YouTube: does whichever step the user is actually stuck at, and
        says what it did.
        """
        import threading

        def _run():
            try:
                from actions.youtube_setup import youtube_status, enable_api
                from actions.app_settings import snapshot
                status = youtube_status()
                state = status.get("state")

                if state == "ready":
                    self.write_log("SYS: YouTube is already connected.")
                elif state in ("needs_google", "needs_scope"):
                    self.write_log("SYS: Opening Google sign-in — this also "
                                   "grants YouTube.")
                    self.connect_google()
                    return                     # connect_google pushes settings
                elif state == "needs_api":
                    ok, detail = enable_api(status.get("project", ""))
                    self.write_log("SYS: " + detail)
                else:
                    self.write_log("SYS: " + status.get("action", "Try again."))

                self._push_settings_async(snapshot())
            except Exception as e:
                self.write_log(f"SYS: Could not connect YouTube: {e}")

        threading.Thread(target=_run, daemon=True).start()

    def browser_sign_in(self, site: str):
        """Open the eagle's own browser so the user can sign in to `site`. Off the UI
        thread: making the browser visible restarts it.
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
                # ok=False is the normal outcome: the window is open and the sign-in has
                # not happened yet.
                self.write_log("SYS: " + (result.message or "Sign-in window opened."))
                self._shop_sig.emit()
                # From a worker thread, only through the settings signal: _push touches
                # the web view.
                self._push_settings_async(snapshot())
            except Exception as e:
                self.write_log(f"SYS: Could not open the sign-in window: {e}")

        threading.Thread(target=_run, daemon=True).start()

    def chrome_setup(self):
        """Let the eagle use the user's Chrome. Chrome allows it only from its own
        settings page and will not open that page for another app, so the address
        goes on the clipboard and Chrome comes forward.
        """
        from actions.grounding.web import chrome_link
        st = chrome_link.begin_setup()
        name = st["browser"] or "Chrome"
        if st["state"] != "off":
            self.show_notice(f"The eagle uses your {name} sign-ins")
            self.push_settings()
            return
        QApplication.clipboard().setText(st["setup_url"])
        self.show_notice(f"In {name}: paste the copied address, press Enter, "
                         "tick Allow remote debugging")

        def _wait():
            if chrome_link.wait_until_on():
                self.show_notice(f"Done. Sites you are signed in to in {name} "
                                 "now work for the eagle")
            self.push_settings()
        threading.Thread(target=_wait, daemon=True).start()

    def chrome_off(self):
        from core import prefs
        from actions.grounding.web import chrome_link
        prefs.save({"chrome_signins": False})
        if chrome_link._DEFAULT is not None:
            threading.Thread(target=chrome_link._DEFAULT.close, daemon=True).start()
        self.push_settings()

    def disconnect_google(self):
        try:
            from actions.app_settings import disconnect_google
            self._push("setSettings", disconnect_google())
        except Exception as e:
            print(f"[aethelark_web] disconnect_google failed: {e}")

    def link_whatsapp(self):
        # Open WhatsApp Web in the eagle's own browser for a one-time QR scan; the login
        # persists in send_message's profile. Non-blocking.
        self.write_log("NET: Opening WhatsApp Web — scan the QR once to link.")
        self.browser_sign_in("web.whatsapp.com")

    def rerun_onboarding(self):
        # Re-open setup. The main app keeps running; config is merged, never wiped.
        def _done():
            try:
                self._reonboard.close()
            except Exception as _e:
                print(f"[aethelark_web.py] Non-fatal error at line 523: {_e}")
            self.push_settings()
        self._reonboard = OnboardingWindow(on_done=_done, on_cancel=lambda: None)
        self._reonboard.showMaximized()
        self._reonboard.raise_()

    def _push(self, fn, payload):
        if self._ui_ready:
            self.dashboard.push(fn, payload)

    def _push_all(self):
        self._push("setState", "LISTENING" if not self._muted else "MUTED")
        self._push("setLog", self._log_lines[-40:])
        self._push_memory(); self._push_metrics(); self._push_swarm()
        self._push_suggestions(); self._push_shop()
        try:
            from core import prefs
            self._push("setMode", "coding" if prefs.enabled("coding_mode") else "casual")
        except Exception as e:
            print(f"[aethelark_web] mode unreadable: {e}")

    _BASE_SUGGESTIONS = ["What's the weather like today?", "What's on my screen?"]

    def _push_suggestions(self):
        chips: list[str] = []
        try:
            from main import MODULE_BUS
            for m in MODULE_BUS.available():
                chips += list(getattr(m, "examples", ()) or ())[:2]
        except Exception:
            pass
        chips += self._BASE_SUGGESTIONS
        self._push("setSuggestions", chips[:4])

    def _push_memory(self):
        self._push("setMemory", self._memory_facts())

    def _memory_changed(self) -> bool:
        """True if the long-term memory file changed since it was last read."""
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

    # Board status -> (lane CSS class, badge text).
    _SWARM_STMAP = {
        "working": ("work", "WORKING"), "review_blocked": ("block", "NEEDS YOU"),
        "failed": ("block", "FAILED"), "merged": ("review", "MERGED"),
        "stopped": ("review", "STOPPED"),
    }
    def _agent_label(self, assignee):
        """Name a swarm lane by how the agent actually runs: CLI, SDK or local model."""
        from core.capability.identity import label_from_routing
        try:
            if self._routing is None:
                from core.capability.profile import load
                self._routing = load().route()
        except Exception:
            self._routing = None
        return label_from_routing(assignee, self._routing)

    def _swarm_view(self):
        """The live swarm blackboard in the #swarm UI's shape. None on error, an idle
        payload when no swarm runs.
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
            return self._solo_view(snap.get("sessions", {}))

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

    def _solo_view(self, sessions: dict):
        from actions.pty_session import POOL
        from actions.swarm_sentinel import swarm_root_of
        lanes = []
        for (key, sdir), sess in POOL.all_sessions().items():
            if (swarm_root_of(sdir) is not None or not sess.is_alive()
                    or getattr(sess, "role", "") == "chief"):
                continue
            busy, _ = self._agent_busy.get(f"{key}@{pathlib.Path(sdir).name}", (True, 0))
            tail = sessions.get(f"{key}@{sdir}", {}).get("tail", "")
            last = next((ln.strip() for ln in reversed(tail.splitlines()) if ln.strip()), "")
            age = int(time.time() - sess.created_at)
            lanes.append({
                "glyph": (sess.agent_name[:1] or "•").upper(), "name": sess.agent_name,
                "branch": pathlib.Path(sdir).name, "lane": "work" if busy else "review",
                "badge": "WORKING" if busy else "DONE", "thought": last[-160:] or "…",
                "elapsed": f"{age // 60}:{age % 60:02d}",
            })
        if not lanes:
            return {"idle": True}
        s = _metrics.snapshot()
        mission = {
            "repo": lanes[0]["branch"], "worktrees": len(lanes),
            "merged": f"{sum(1 for l in lanes if l['badge'] == 'DONE')} / {len(lanes)}",
            "conflicts": 0, "progress": 0, "cpu": f"{s['cpu']:.0f}%", "tasks": 0,
            "elapsed": lanes[0]["elapsed"],
            "conductor": f"{len(lanes)} AGENT{'S' if len(lanes) != 1 else ''} · DIRECT",
            "state": "CONDUCTING",
        }
        return {"mission": mission, "agents": lanes, "timeline": [], "solo": True}

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
        if not view.get("solo"):
            self._push_phase(view)

    _AGENT_QUIET_S = 12.0
    _AGENT_DISMISS_S = 300.0

    def _push_agents(self):
        try:
            from actions.pty_session import POOL
            from actions.swarm_sentinel import swarm_root_of
        except Exception:
            return
        from core import escalations
        waiting = {e.agent for e in escalations.pending()}
        seen = set()
        for (key, sdir), sess in POOL.all_sessions().items():
            if swarm_root_of(sdir) is not None or getattr(sess, "role", "") == "chief":
                continue
            ident = f"{key}@{pathlib.Path(sdir).name}"
            if not sess.is_alive():
                continue
            seen.add(ident)
            watcher = getattr(sess, "watcher", None)
            if hasattr(sess, "turn_open"):
                busy = sess.turn_open
            elif watcher is not None:
                busy = watcher.seconds_since_activity() < self._AGENT_QUIET_S
            else:
                busy = True
            was, since = self._agent_busy.get(ident, (None, time.time()))
            if busy != was:
                since = time.time()
            self._agent_busy[ident] = (busy, since)
            done_for = 0.0 if busy else time.time() - since
            self._activity_board.apply("agent", {"activity": {
                "id": ident,
                "state": "ended" if done_for > self._AGENT_DISMISS_S else "active",
                "leading": f"{sess.agent_name} · {pathlib.Path(sdir).name}",
                "trailing": ("needs your OK" if sess.agent_name in waiting
                             else "working" if busy else "done"),
                "relevance": 90 if sess.agent_name in waiting else 70 if busy else 40,
                "stale_after_s": 86400}})
            if was and not busy:
                self.pill_win.set_pill("ready", {"label": f"{sess.agent_name} finished",
                                                 "id": f"{ident}:{int(since)}"})
        for ident in [i for i in self._agent_busy if i not in seen]:
            del self._agent_busy[ident]
            self._activity_board.apply("agent", {"activity": {"id": ident, "state": "ended"}})
        self._push_activities()

    def _push_phase(self, view):
        try:
            agents = view.get("agents") or []
            if not agents:
                if self._activity_board.end_module("swarm"):
                    self._push_activities()
                return
            lanes = [a.get("lane") or "" for a in agents]
            needs = sum(1 for ln in lanes if ln == "block")
            working = sum(1 for ln in lanes if ln == "work")
            done = sum(1 for ln in lanes if ln == "review")
            total = max(1, len(lanes))

            if done == total and not needs:
                if self._activity_board.end_module("swarm"):
                    self._push_activities()
                    if any(a.get("badge") == "MERGED" for a in agents):
                        self.pill_win.set_pill("ready", {"label": "Your project is ready"})
                return

            repo = (view.get("mission") or {}).get("repo") or "project"
            leading = ("Needs your call" if needs
                       else f"Building {repo}" if working else f"Checking {repo}")
            self._activity_board.apply("swarm", {"activity": {
                "id": repo, "leading": leading, "trailing": f"{done}/{total}",
                "progress": done / total, "relevance": 90 if needs else 60,
                "stale_after_s": 3600}})
            self._push_activities()
        except Exception as e:
            print(f"[aethelark_web] phase push skipped: {e}")

    def _tick(self):
        # Metrics every tick; memory only when the file changed.
        self._push_metrics()
        self._push_swarm()
        self._push_agents()
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

    def _on_hearing(self, on: bool):
        self._hearing = bool(on)
        if (self._last_state or "").upper() == "LISTENING":
            self.pill_win.set_pill(self._pill_state("LISTENING"),
                                   self._pill_data("LISTENING"))

    def _on_modules_changed(self):
        """Installed modules changed: refresh the session, then the event listeners.
        Off the GUI thread: the refresh waits for any reply in flight.
        """
        def _work():
            handler = self.on_modules_changed
            if handler is not None:
                try:
                    handler()
                except Exception as e:
                    print(f"[aethelark_web] module refresh failed: {e}")
            self._ambient_refresh_sig.emit()
        threading.Thread(target=_work, daemon=True).start()

    def _refresh_ambient(self):
        self.stop_ambient_events()
        self._setup_ambient_events()

    def _on_state(self, state):
        # Forwarded unconditionally: only the page knows whether a card is up, so only
        # it can place a voice state.
        self.pill_win.set_pill(self._pill_state(state), self._pill_data(state))
        self._push("setState", state)

    def _on_pill_context(self, module: str, data: dict, ttl_s: float,
                         ambient: bool = False):
        self._active_pill_context = (module, data)
        # Whether what is on screen is something the user asked for
        # (_may_take_the_screen). Travels on the signal so it cannot disagree with the
        # context.
        self._active_pill_is_ambient = bool(ambient)
        self.pill_win.set_island_ttl(ttl_s)
        self.pill_win.set_pill(module, data)
        # The page owns decay. This timer is the backstop if the page never reports
        # back.
        self._pill_context_timer.stop()
        self._pill_context_timer.start(
            int(ttl_s * 1000) + self._BACKSTOP_GRACE_MS)

    def note_island_opened(self, module: str):
        """A card the page opened itself holds the screen like one the user asked
        for.
        """
        self._active_pill_context = (module, {})
        self._active_pill_is_ambient = False

    def note_island_deadline(self, left_ms: int):
        """The backstop follows the page's own clock instead of guessing it."""
        if self._active_pill_context is None:
            return
        self._pill_context_timer.stop()
        self._pill_context_timer.start(max(0, left_ms) + self._BACKSTOP_GRACE_MS)

    def live_media(self, module: str, subject: str, on: bool):
        """Switch a module's live media (a printer camera) on or off. Requests for
        one subject run in order and the last one wins.
        """
        from main import MODULE_BUS
        with self._live_lock:
            self._live_want[(module, subject)] = on
            if (module, subject) in self._live_busy:
                return
            self._live_busy.add((module, subject))

        def _run():
            done = None
            try:
                while True:
                    with self._live_lock:
                        want = self._live_want.get((module, subject))
                        if want == done:
                            self._live_busy.discard((module, subject))
                            return
                    url = MODULE_BUS.live_media(module, subject, want)
                    done = want
                    print(f"[live] {module} {subject} {'on' if want else 'off'}: "
                          f"{url or 'no stream'}")
                    if want:
                        self.pill_win.run_js("window.island && window.island.setLive(%s, %s, %s)"
                                             % (json.dumps(module), json.dumps(subject),
                                                json.dumps(url or "")))
            except Exception as e:
                print(f"[live] {module} {subject}: {e}")
                with self._live_lock:
                    self._live_busy.discard((module, subject))

        threading.Thread(target=_run, name="live-media", daemon=True).start()

    def note_island_collapsed(self):
        """The page's decay finished. Stop suppressing state repaints."""
        self._active_pill_context = None
        self._active_pill_is_ambient = False
        self._pill_context_timer.stop()

    # How long the backstop waits past the context's own decay.
    _BACKSTOP_GRACE_MS = 10_000

    def _on_pill_context_expired(self):
        if self._active_pill_context is None:
            return                      # the page already collapsed it
        if self.deck_is_open():
            # A deck is a decision in progress: re-arm instead of clearing it.
            self._pill_context_timer.start(self._BACKSTOP_GRACE_MS)
            return
        if self._pill_state(self._last_state) != "idle":
            # A turn is in flight and the page holds cards through voice turns: re-arm.
            self._pill_context_timer.start(self._BACKSTOP_GRACE_MS)
            return
        self._active_pill_context = None
        self.pill_win.set_pill(self._pill_state(self._last_state),
                               self._pill_data(self._last_state))

    def _pill_state(self, s):
        """Which face the island shows for a voice-loop state. LISTENING rests unless
        the user is actually talking.
        """
        s = (s or "").upper()
        if s == "LISTENING":
            return "listening" if self._hearing else "idle"
        if s == "SPEAKING":
            return "speaking"
        if s in ("THINKING", "PROCESSING", "WORKING"):
            return "thinking"
        return "idle"

    @staticmethod
    def _pill_data(s) -> dict:
        """What the resting island says about the microphone."""
        s = (s or "").upper()
        if s == "MUTED":
            return {"mic": "muted"}
        if s in ("LISTENING", "SPEAKING", "THINKING", "PROCESSING", "WORKING"):
            return {"mic": "live"}
        return {"mic": "offline"}

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
        """Google refused the key: say so on the island and open the key screen."""
        self.pill_win.set_pill("notice", {"text": "Gemini key refused · tap to fix",
                                          "action": "key"})
        win = self._reconfig_win
        if win is not None and win.isVisible():
            win.raise_(); win.activateWindow()
            return

        def _done():
            self._win._ready = True
            self._reconfig_win = None
            self.pill_win.set_pill(self._pill_state(self._last_state),
                                   self._pill_data(self._last_state))

        self._reconfig_win = OnboardingWindow(
            on_done=_done, mode="key", on_cancel=lambda: None,
            reason="Google turned your Gemini key down. Get a new one, "
                   "free, to keep going.")
        self._reconfig_win.showMaximized()
        self._reconfig_win.raise_()

    # The interface AethelarkLive expects (thread-safe).
    def set_state(self, state):
        # The playback loop re-asserts SPEAKING on every audio frame: act only on real
        # transitions.
        if state == self._last_state:
            return
        self._last_state = state
        self._state_sig.emit(state)
    def write_log(self, text):
        if not str(text).startswith(("SYS:", "ERR:")):
            print(f"[chat] {text}")
        self._log_sig.emit(text)
    def request_chrome_setup(self) -> None:
        """chrome_setup from any thread: it touches the clipboard."""
        self._chrome_setup_sig.emit()

    def _on_escalation_ui(self, esc) -> None:
        if getattr(esc, "kind", "") == "resolved" or not getattr(esc, "informational", False):
            self._ask_sig.emit()

    def _sync_ask(self) -> None:
        """GUI thread. The island shows the oldest held request, or lets go of
        the one it showed once that is answered."""
        from core import escalations
        esc = escalations.oldest()
        if esc is not None:
            if esc.id == self._asking:
                return
            self._asking = esc.id
            lines = [ln.strip() for ln in esc.excerpt.split(" / ") if ln.strip()]
            self.pill_win.set_pill("ask", {
                "id": esc.id, "agent": esc.agent,
                "what": lines[0] if lines else esc.reason,
                "command": lines[-1] if len(lines) > 1 else "",
                "why": esc.reason})
            return
        if self._asking:
            done, self._asking = self._asking, ""
            self.pill_win.set_pill("ask_done", {"id": done})

    def answer_request(self, esc_id: str, verdict: str) -> None:
        """A tap on Allow or Don't allow: the user's own answer, delivered the
        same way a spoken one is."""
        if verdict not in ("allow", "deny"):
            return
        from core import escalations
        esc = escalations.find(esc_id)
        if esc is None:
            self._ask_sig.emit()
            return

        def _work():
            from actions.swarm_orchestrator import deliver_verdict
            print(f"[island] {deliver_verdict(esc, verdict, by='tap')}")
        threading.Thread(target=_work, daemon=True, name="island-answer").start()

    def show_notice(self, text: str, action: str = "") -> None:
        """One line on the island until the next state change replaces it.
        `action="key"` makes a tap open the key screen; anything else, nothing."""
        self._notice_sig.emit(str(text), str(action or ""))
    def set_hearing(self, on: bool): self._hearing_sig.emit(bool(on))

    def request_stop(self):
        if self.on_stop:
            threading.Thread(target=self.on_stop, daemon=True).start()
    def set_audio_level(self, level):
        now = time.monotonic()
        if now - self._level_at < 0.045:
            return
        self._level_at = now
        self.pill_win.run_js("window.island && window.island.level(%.3f)" % float(level))
    def show_content(self, title, text): self._content_sig.emit(str(title)[:48], str(text)[:4000])
    def prompt_reconfig(self): self._win._ready = False; self._reconfig_sig.emit()

    def reconfig_complete(self) -> bool:
        """Has the user finished re-entering their credentials?"""
        return bool(self._win._ready)

    def request_shutdown(self) -> None:
        """Ask the UI to close, and take the module listeners with it."""
        self.stop_ambient_events()
        self.root.quit()

    def notify_phone_connected(self): pass

    def deck_is_open(self) -> bool:
        return bool(getattr(self, "_deck_open", False))

    # Hard cap on the island line: it is spent from the conversation's context budget.
    _ISLAND_LINE_MAX = 120

    def note_island_subject(self, line: str) -> None:
        """Tell the model what the user is looking at. Deduplicated here as well as
        in the page, because this end spends context.
        """
        line = (line or "").strip()[:self._ISLAND_LINE_MAX]
        if not line or line == getattr(self, "_island_subject", None):
            return
        self._island_subject = line
        handler = getattr(self, "on_island_subject", None)
        if handler is None:
            return
        # Off the GUI thread: this reaches the live session.
        threading.Thread(target=handler, args=(line,), daemon=True).start()

    def island_fleet(self) -> str:
        """The printer keys the last browse reported, as JSON."""
        return getattr(self, "_deck_fleet", "[]")

    def island_selection(self) -> str:
        """The user's picks as JSON: [{"model_id": ..., "printer": ...}]."""
        return getattr(self, "_deck_picks", "[]")

    def island_deck_move(self, delta: int) -> None:
        self.pill_win.run_js(
            "window.island && window.island.send('deck_move', {delta: %d})"
            % int(delta))

    def island_view(self, stage: str, target: str = ""):
        """Ask the page to show `stage`; True when there is something to show,
        otherwise the names of what is running (possibly none)."""
        running = self._activity_board.visible()
        names = [str(a.get("leading") or "") for a in running]
        if stage == "idle":
            self.pill_win.run_js("window.island && window.island.send('view', {stage: 'idle'})")
            return True
        wanted = target.lower().replace("_", " ")

        def known(a):
            card = a.get("card") or {}
            said = [a.get("leading"), card.get("printer"), card.get("printer_key"),
                    card.get("printer_name"), card.get("ticker"), card.get("title")]
            said = [str(x).lower().replace("_", " ") for x in said if x]
            return any(wanted in x or x in wanted for x in said)

        on_screen = self._active_pill_context is not None
        if wanted and not any(known(a) for a in running):
            return names
        if not running and not on_screen:
            return names
        self.pill_win.run_js("window.island && window.island.send('view', %s)"
                             % json.dumps({"stage": stage, "subject": target}))
        return True

    def island_deck_show(self, index: int) -> bool:
        """Jump to a 0-based position in the deck. Returns whether it exists; the
        size comes from the page.
        """
        index = int(index)
        if not (0 <= index < int(getattr(self, "_deck_len", 0))):
            return False
        self.pill_win.run_js(
            "window.island && window.island.goTo(%d)" % index)
        return True

    def island_set_printer(self, name: str) -> None:
        self.pill_win.run_js(
            "window.island && window.island.setPrinter(%s)" % json.dumps(name))

    def island_refine(self, field: str, value, data: dict) -> None:
        """Merge a refined answer into the deck card whose `field` is `value`."""
        self.pill_win.run_js(
            "window.island && window.island.refineCandidate && "
            "window.island.refineCandidate(%s, %s, %s)"
            % (json.dumps(field), json.dumps(value), json.dumps(data, default=str)))

    def set_pill_context(self, module: str, data: dict, ttl_s: float = 20.0,
                         *, ambient: bool = False):
        """Put a module's card on the island. ambient=True marks a card the module
        raised itself: it does not count as the user bringing the module up.
        """
        if not ambient:
            self._ambient_engaged.add(module)
        self._pill_context_sig.emit(
            module, data if isinstance(data, dict) else {}, ttl_s, bool(ambient))


    def fetch_card_depth(self, module: str, ticker: str, *, quiet: bool = False) -> None:
        """Run the deep tool for the card on screen and merge what comes back."""
        from core.card_assembly import needs_depth
        from main import CARD_STORE, MODULE_BUS, TOOL_SPECS, ToolSpec

        # The deeper card is filled by the module's `prefetch` tools; none means nothing
        # to fetch.
        tools = MODULE_BUS.depth_tools(module)
        if not tools or not ticker:
            return

        # Reuse what a prefetch already stored, within the module's `[island].fresh_for`
        # (0 means no bound).
        face = MODULE_BUS.island_face(tools[0])
        fresh_for = float(getattr(face, "fresh_for", 0.0) or 0.0)
        stored = CARD_STORE.card_for(ticker, fresh_for=fresh_for)
        if stored is not None and not needs_depth(stored):
            if not quiet:
                self.set_pill_context(module, stored, ttl_s=30.0)
            return

        # The card may already hold the deep answer.
        current = (self._active_pill_context or ("", {}))[1]
        if not needs_depth(current):
            return
        if getattr(self, "_depth_inflight", None) == (module, ticker):
            # Already fetching this: record that someone is now looking, so the running
            # fetch repaints when it lands.
            if not quiet:
                self._depth_wants_paint = True
            return
        self._depth_inflight = (module, ticker)
        # Someone is looking at the card this fetch is for.
        self._depth_wants_paint = not quiet

        def _run():
            try:
                for tool in tools:
                    budget = TOOL_SPECS.get(tool, ToolSpec()).timeout_s
                    result = MODULE_BUS.invoke(tool, {"ticker": ticker},
                                               timeout_s=budget)
                    payload = (result.data.get("result")
                               if isinstance(result.data, dict) else None)
                    if not isinstance(payload, dict):
                        # One tool with nothing to say does not abandon the rest of the
                        # card.
                        print(f"[aethelark_web] {tool} gave the card nothing "
                              f"for {ticker}: {result.message[:90]}")
                        continue
                    card = CARD_STORE.absorb(tool, payload, {"ticker": ticker})
                    # Whether anyone is looking now, not why the fetch began.
                    if getattr(self, "_depth_wants_paint", False):
                        # Repaint after each tool, so layers appear without waiting on
                        # the rest.
                        self.set_pill_context(module, card, ttl_s=30.0)
            except Exception as e:
                print(f"[aethelark_web] depth fetch failed for {ticker}: {e}")
            finally:
                self._depth_inflight = None
                self._depth_wants_paint = False

        threading.Thread(target=_run, daemon=True).start()

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
        reported = False
        while True:
            try:
                d = json.loads(API_KEYS.read_text(encoding="utf-8"))
                if d.get("gemini_api_key"):
                    self._win._ready = True
                    return
            except FileNotFoundError:
                pass
            except Exception as _e:
                if not reported:
                    print(f"[aethelark_web] config not readable yet: {_e}")
                    reported = True
            time.sleep(0.3)


ONBOARDING_HTML = BASE / "web" / "onboarding.html"


def _config() -> dict:
    try:
        return json.loads(API_KEYS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _is_onboarded() -> bool:
    """Set up means the flow finished and a Gemini key is saved."""
    cfg = _config()
    return bool(cfg.get("onboarded")) and bool(cfg.get("gemini_api_key"))


class OnboardBridge(QObject):
    """window.pybridge on the onboarding page."""
    def __init__(self, win): super().__init__(); self._win = win

    @pyqtSlot()
    def onboard_ready(self): self._win.on_ready()
    @pyqtSlot(str)
    def validate_key(self, key): self._win.validate_key(key)
    @pyqtSlot()
    def get_key(self): self._win.get_key()
    @pyqtSlot()
    def use_found_key(self): self._win.use_found_key()
    @pyqtSlot(str)
    def open_url(self, url): self._win.open_url(url)
    @pyqtSlot(str)
    def start_modules(self, names_json): self._win.start_modules(names_json)
    @pyqtSlot()
    def start_mic_check(self): self._win.start_mic_check()
    @pyqtSlot()
    def stop_mic_check(self): self._win.stop_mic_check()
    @pyqtSlot()
    def enable_echo_cancel(self): self._win.enable_echo_cancel()
    @pyqtSlot()
    def chrome_setup(self): self._win.chrome_setup()
    @pyqtSlot(str)
    def complete(self, payload): self._win.complete(payload)
    @pyqtSlot()
    def quit(self): self._win.cancel()

    @pyqtSlot(int, int)
    def begin_drag(self, sx, sy):
        w = self._win; w._drag_origin = (sx, sy, w.x(), w.y())

    @pyqtSlot(int, int)
    def drag_to(self, sx, sy):
        w = self._win; o = getattr(w, "_drag_origin", None)
        if o:
            w.move(o[2] + (sx - o[0]), o[3] + (sy - o[1]))


class OnboardingWindow(QMainWindow):
    """First-run setup, and the place a refused key is fixed. mode="key" shows only
    the key screen.
    """
    _push_sig = pyqtSignal(str, str)   # (fn, json-payload) — marshals to GUI thread
    _front_sig = pyqtSignal()

    def __init__(self, on_done, mode: str = "setup", reason: str = "",
                 on_cancel=None):
        super().__init__()
        self._on_done = on_done
        self._on_cancel = on_cancel
        self._mode = mode
        self._reason = reason
        self._validated_key = ""
        self._found_key = ""
        self._last_clip = ""
        self._mic_stop = threading.Event()
        self._mic_thread = None
        self.setWindowTitle("Aethelark — Setup")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view = _local_view()
        self.view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view.page().setBackgroundColor(Qt.GlobalColor.transparent)
        self.setCentralWidget(self.view)
        self.channel = QWebChannel()
        self.bridge = OnboardBridge(self)
        self.channel.registerObject("pybridge", self.bridge)
        self.view.page().setWebChannel(self.channel)
        self._push_sig.connect(self._do_push)
        self._front_sig.connect(self._come_back)
        self.view.load(QUrl.fromLocalFile(str(ONBOARDING_HTML)))

    def _do_push(self, fn, payload):
        self.view.page().runJavaScript(
            "window.onboarding && window.onboarding.%s && window.onboarding.%s(%s)"
            % (fn, fn, payload))

    def push(self, fn, payload):
        self._push_sig.emit(fn, json.dumps(payload))

    def on_ready(self):
        cfg = _config()
        from actions.grounding.web import chrome_link
        chrome = chrome_link.status()
        start = {"mode": self._mode,
                 "user_name": cfg.get("user_name") or chrome_link.account_name(),
                 "has_key": bool(cfg.get("gemini_api_key")),
                 "aec_offer": self._offer_echo_cancel(),
                 "chrome": chrome["browser"] if chrome["state"] != "none" else "",
                 "examples": WebShellUI._BASE_SUGGESTIONS}
        if self._mode == "key":
            start.update(step="key", reason=self._reason)
        else:
            try:
                from core.module_bus import installer
                start["modules"] = [
                    {"name": m["name"], "title": m["title"], "about": m["about"],
                     "installed": m["installed"], "checked": m["installed"] or m["default"]}
                    for m in installer.shop()]
            except Exception as e:
                print(f"[onboarding] could not list the modules: {e}")
        self.push("startAt", start)
        self._clip_timer = QTimer(self)
        self._clip_timer.timeout.connect(self._watch_clipboard)
        self._clip_timer.start(800)
        if not start["has_key"] or self._mode == "key":
            threading.Thread(target=self._look_for_key, daemon=True).start()

        # The full machine scan, saved so later launches load it at once. Off-thread:
        # nvidia-smi and lspci can block.
        def _probe():
            try:
                from core.capability.profile import save, scan
                save(scan(full=True))
            except Exception as e:
                print(f"[onboarding] capability scan failed: {e}")
        threading.Thread(target=_probe, daemon=True).start()

    @staticmethod
    def _offer_echo_cancel() -> bool:
        """Whether to offer echo cancellation: it can be turned on and is not.
        Offered, never applied: it changes the default microphone and speakers for
        every app.
        """
        try:
            from core import echo_cancel
            st = echo_cancel.state()
            return bool(st.supported and not st.active)
        except Exception:
            return False

    def enable_echo_cancel(self):
        def _work():
            try:
                from core import echo_cancel
                ok, detail = echo_cancel.install()
            except Exception as e:
                ok, detail = False, str(e)
            self.push("setAec", {"ok": ok, "message": (
                "Echo cancellation is on. Talk over me any time." if ok
                else f"It could not be turned on: {detail[:120]}")})
        threading.Thread(target=_work, daemon=True).start()

    def chrome_setup(self):
        from actions.grounding.web import chrome_link
        st = chrome_link.begin_setup()
        if st["state"] != "off":
            self.push("setChrome", {"on": True})
            return
        QApplication.clipboard().setText(st["setup_url"])
        self.push("setChrome", {"on": False})

        def _wait():
            self.push("setChrome", {"on": chrome_link.wait_until_on(timeout=600)})
        threading.Thread(target=_wait, daemon=True).start()

    def open_url(self, url: str):
        from PyQt6.QtGui import QDesktopServices
        if str(url).startswith("https://"):
            QDesktopServices.openUrl(QUrl(str(url)))

    def start_modules(self, names_json: str):
        """Install the ticked modules now, while the user finishes setup; the last
        screen shows where each got to.
        """
        from core import setup_modules
        from core.module_bus import installer
        try:
            chosen = [str(n) for n in json.loads(names_json or "[]")]
            shop = installer.shop()
        except Exception as e:
            print(f"[onboarding] could not start the modules: {e}")
            return
        titles = {m["name"]: m["title"] for m in shop}
        installed = {m["name"] for m in shop if m["installed"]}

        def report(name, state, step):
            self.push("setModuleProgress", {"name": name, "title": titles.get(name, name),
                                            "state": state, "step": step})

        def install(name, progress):
            installer.install(name, interactive=False,
                              progress=lambda m: progress(m.rstrip("…").strip() + "…"))

        threading.Thread(
            target=setup_modules.install_chosen, daemon=True, name="setup-modules",
            kwargs=dict(names=[n for n in chosen if n in titles], installed=installed,
                        install=install, report=report)).start()

    def _watch_clipboard(self):
        """Pick a copied key off the clipboard. Only a string shaped exactly like a
        Gemini key goes to the page.
        """
        from core import gemini_key
        text = (QApplication.clipboard().text() or "").strip()
        if text != self._last_clip and gemini_key.shaped(text):
            self._last_clip = text
            self.push("clipboardKey", text)

    def _look_for_key(self):
        from core import gemini_key
        for key in gemini_key.on_this_computer():
            if gemini_key.check(key)[0]:
                self._found_key = key
                self.push("foundKey", {})
                return

    def use_found_key(self):
        if self._found_key:
            self._validated_key = self._found_key
            self.push("setKeyResult", {"ok": True, "message": ""})

    def get_key(self):
        from actions.grounding.web import chrome_link
        url = "https://aistudio.google.com/apikey"
        g = chrome_link.open_signed_in(url)
        if g is None:
            self.open_url(url)
        self.push("keyPageOpened", {"browser": g.browser.name if g else "",
                                    "account": g.email if g else ""})

    def _come_back(self):
        if self.isActiveWindow():
            return
        self.showNormal()
        self.raise_()
        self.activateWindow()
        QApplication.alert(self)

    def validate_key(self, key: str):
        def _work():
            from core import gemini_key
            ok, message = gemini_key.check(key)
            if ok:
                self._validated_key = (key or "").strip()
                self._front_sig.emit()
            self.push("setKeyResult", {"ok": ok, "message": message})
        threading.Thread(target=_work, daemon=True).start()

    def start_mic_check(self):
        self.stop_mic_check()
        self._mic_stop = threading.Event()
        stop = self._mic_stop
        self._mic_thread = threading.Thread(
            target=self._mic_probe, args=(stop,), daemon=True)
        self._mic_thread.start()

    def stop_mic_check(self):
        self._mic_stop.set()

    def _mic_probe(self, stop: threading.Event):
        """Open the default input with the voice loop's own library, report its
        level, and say when a voice is heard.
        """
        try:
            import numpy as np
            import sounddevice as sd
        except Exception:
            self.push("setMic", {"state": "error", "message":
                      "Audio isn't set up on this computer. Reinstall "
                      "Aethelark, or run 'eagle --doctor' to see what is missing."})
            return
        try:
            device = sd.query_devices(kind="input")
            name = str(device.get("name") or "")
        except Exception:
            self.push("setMic", {"state": "error",
                                 "message": "No microphone was found."})
            return
        self.push("setMic", {"device": name})
        rate, block = 16000, 800            # 50 ms per block
        heard_run, floor, sent = 0, None, 0
        started = time.monotonic()
        try:
            with sd.InputStream(samplerate=rate, channels=1, dtype="int16",
                                blocksize=block) as stream:
                while not stop.is_set():
                    data, _ = stream.read(block)
                    rms = float(np.sqrt(np.mean(data.astype(np.float32) ** 2)))
                    floor = rms if floor is None else min(rms, floor * 0.98 + rms * 0.02)
                    level = min(1.0, rms / 2500.0)
                    sent += 1
                    if sent % 2 == 0:
                        self.push("setMic", {"level": round(level, 3)})
                    loud = rms > max(450.0, (floor or 0) * 4)
                    heard_run = heard_run + 1 if loud else max(0, heard_run - 1)
                    if heard_run >= 6:            # ~300 ms of voice
                        self.push("setMic", {"state": "heard", "level": round(level, 3)})
                        break
                    if time.monotonic() - started > 15:
                        self.push("setMic", {"state": "quiet", "level": 0})
                        break
        except Exception as e:
            self.push("setMic", {"state": "error", "message":
                      f"The microphone could not be opened ({str(e)[:80]})."})

    def complete(self, payload):
        self.stop_mic_check()
        if getattr(self, "_clip_timer", None):
            self._clip_timer.stop()
        try:
            data = json.loads(payload or "{}")
        except Exception:
            data = {}
        cfg = _config()
        key = self._validated_key or ""
        if key:
            cfg["gemini_api_key"] = key
            cfg["brain_provider"] = "google"
            cfg["brain_mode"] = "api"
        name = (data.get("user_name") or "").strip()
        if name:
            cfg["user_name"] = name
        cfg.setdefault("user_name", "")
        cfg.setdefault("auth_provider", "guest")
        if cfg.get("gemini_api_key"):
            cfg["onboarded"] = True
        try:
            user_paths.write_private(API_KEYS, json.dumps(cfg, indent=4))
        except Exception as e:
            print(f"[onboarding] could not write config: {e}")
        from config import reset_client_cache
        reset_client_cache()
        self.close()
        self._on_done()

    def cancel(self):
        """The ✕. First run: quit. Fixing a key: just close the window."""
        self.stop_mic_check()
        if getattr(self, "_clip_timer", None):
            self._clip_timer.stop()
        if self._on_cancel is not None:
            self.close()
            self._on_cancel()
        else:
            QApplication.instance().quit()


VOICE_DID_NOT_START = "Voice didn't start. Run: eagle --doctor --fix"


def _launch_main_app():
    # The app lives in the pill, a Tool window Qt does not count. Left at the default,
    # closing any other window while the full window was hidden (the key screen when
    # Google refused a key, done or dismissed) quit the whole eagle. Quit is explicit.
    QApplication.instance().setQuitOnLastWindowClosed(False)
    ui = WebShellUI("face.png")

    def _say_if_update_is_ready():
        from core.update_check import newer_version
        if newer_version(BASE):
            ui.show_notice("New version ready. Run: eagle update")

    QTimer.singleShot(20000, lambda: threading.Thread(
        target=_say_if_update_is_ready, daemon=True, name="update-check").start())

    def runner():
        ui.wait_for_api_key()
        try:
            from main import AethelarkLive, _run_core
        except Exception:
            # This thread dying used to leave the pill looking idle and never
            # listening, with nothing on screen to say so.
            import traceback
            traceback.print_exc()
            ui.show_notice(VOICE_DID_NOT_START)
            return
        import asyncio

        # Supervised by _run_core, which builds a new event loop on a restart.
        def start():
            live = AethelarkLive(ui)
            asyncio.run(live.run())

        _run_core(ui, start)

    threading.Thread(target=runner, daemon=True).start()
    # Keep a reference so the shell is not garbage-collected.
    QApplication.instance()._aethelark_ui = ui


def _name_the_app(app):
    """The desktop matches a pinned launcher to its windows by this name."""
    app.setApplicationName("aethelark")
    app.setDesktopFileName("aethelark")


def _quiet_once_started() -> None:
    """Started detached by the launcher: stop writing into its start-up capture once
    the app is up.
    """
    if not os.environ.pop("EAGLE_DETACHED", ""):
        return

    def to_null() -> None:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        null = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null, 1)
        os.dup2(null, 2)
        os.close(null)

    QTimer.singleShot(8000, to_null)


def _windows_through_x11(environ=None) -> bool:
    """On Wayland, Qt's own windows can neither stay on top nor place themselves:
    measured on Ubuntu 26.04, the pill sat mid-screen and sank behind the next
    window opened. Through Xwayland the desktop honours both. Linux only, only
    where Xwayland runs, and never over a platform the user chose."""
    env = os.environ if environ is None else environ
    if (sys.platform.startswith("linux") and env.get("WAYLAND_DISPLAY")
            and env.get("DISPLAY") and not env.get("QT_QPA_PLATFORM")):
        env["QT_QPA_PLATFORM"] = "xcb"
        return True
    return False


def main():
    _windows_through_x11()
    app = QApplication.instance() or QApplication(sys.argv)
    _name_the_app(app)
    load_app_fonts()
    app.setStyle("Fusion")
    _quiet_once_started()

    if _is_onboarded():
        _launch_main_app()
    else:
        onboard = OnboardingWindow(on_done=_launch_main_app)
        app._aethelark_onboard = onboard   # keep alive
        onboard.showMaximized()

    sys.exit(app.exec() or 0)


def _carries_no_signature(path) -> bool:
    """Whether a bundle has no signature entry at all, which differs from one that
    does not verify.
    """
    import zipfile
    try:
        with zipfile.ZipFile(path) as archive:
            return "signature.bin" not in archive.namelist()
    except Exception:
        return False


def _maybe_modules() -> bool:
    """`eagle --install-module <file>`, `eagle --modules`. Handled before anything
    starts: installing a module needs no key, window or brain.
    """
    import sys
    argv = sys.argv[1:]

    if "--modules" in argv:
        # One listing, the same reader as `eagle modules`.
        from core.module_bus.installer import main as _modules
        raise SystemExit(_modules(["modules"]))

    if "--install-module" not in argv:
        return False

    i = argv.index("--install-module")
    if i + 1 >= len(argv) or argv[i + 1].startswith("-"):
        print("--install-module needs the module file you were given, "
              "for example:", file=sys.stderr)
        print("  eagle --install-module ~/Downloads/aethelark-trade.aem",
              file=sys.stderr)
        raise SystemExit(2)

    from core.module_bus import BundleError, ModuleManager
    path = argv[i + 1]
    # No signature at all is a developer's own build. A signature that does not verify
    # is always refused.
    allow_unsigned = "--allow-unsigned" in argv

    try:
        manifest = ModuleManager().install_bundle(path,
                                                  allow_unsigned=allow_unsigned)
    except BundleError as e:
        print(f"That module was not installed.\n\n{e}", file=sys.stderr)
        # Only for an unsigned bundle: a signature that does not verify gets no flag to
        # try next.
        if not allow_unsigned and _carries_no_signature(path):
            print(f"\nIf you built this module yourself, install it with:\n"
                  f"  eagle --install-module {path} --allow-unsigned",
                  file=sys.stderr)
        raise SystemExit(1)
    except OSError as e:
        print(f"That module was not installed.\n\n{path} could not be read: "
              f"{e}", file=sys.stderr)
        raise SystemExit(1)

    # A bundle carries source, not dependencies: provisioning builds its environment.
    manager = ModuleManager()
    if not manager.is_ready(manifest.key):
        print(f"Setting up {manifest.key} (downloading what it needs; this can "
              f"take a few minutes)…")
        result = manager.provision(manifest.key)
        if not result.ok:
            print(f"{manifest.key} is installed but could not be set up:\n"
                  f"{str(result.detail)[-800:]}\n\nRun the same "
                  f"--install-module command again to retry.", file=sys.stderr)
            raise SystemExit(1)

    actions = len(manifest.tools)
    print(f"Installed {manifest.key} — {manifest.description}")
    print(f"It adds {actions} action{'' if actions == 1 else 's'}. "
          f"Start the eagle and ask it for one.")
    raise SystemExit(0)


def _maybe_doctor() -> bool:
    """`eagle --doctor` / `--fix`. Handled before anything else starts, so a
    machine that cannot run the app can still be told why."""
    import sys
    if "--doctor" in sys.argv or "--fix" in sys.argv:
        from core.doctor import main as _doctor
        raise SystemExit(_doctor([a for a in sys.argv[1:]]))
    return False


_maybe_modules()
_maybe_doctor()

if __name__ == "__main__":
    main()
