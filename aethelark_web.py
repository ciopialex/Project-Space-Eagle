"""Aethelark — integrated web-rendered app.

Runs the REAL voice/swarm backend (main.AethelarkLive) behind a native pill +
QWebEngine web dashboard (the exact artifact). `WebShellUI` is a drop-in
adapter exposing the interface AethelarkLive expects (write_log / set_state /
muted / set_audio_level / …), translating it into native-pill updates +
dashboard.push(...) per the message contract in Aethelark_Web_Pivot_Plan.md.

This is the ONLY entry point. The QPainter cockpit that main.py used to launch
was deleted on 2026-08-28: main.py is a library exposing AethelarkLive, and
ui.py is down to the fonts, metrics and spring curve this file imports.
The installed `eagle` command execs this file.

Run:  .venv/bin/python aethelark_web.py
"""
import json
import sys

# Line-buffer stdout before anything prints. Redirected output is
# block-buffered by default, so `eagle > log.txt` loses everything
# still in the buffer when the process dies - which is exactly the
# moment the log matters most.
from core import logsetup  # noqa: E402,F401
import os
# Qt importing Chromium's GPU frames as GLX native pixmaps aborts the process
# at random ("Failed to restore OpenGL context after clean-up"). Software
# compositing hands frames over in shared memory and never takes that path;
# WebGL and raster stay on the GPU.
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
from core import setup_wishes, user_paths

BASE = pathlib.Path(__file__).resolve().parent
DASHBOARD_HTML = BASE / "web" / "dashboard.html"
PILL_HTML = BASE / "web" / "pill.html"
API_KEYS = user_paths.api_keys_path()
# The island window is sized ONCE, for the largest state FSM-1 can reach
# (the 440×364 Expanded card), and never resized again: resizing a window with
# a QWebEngineView in it relayouts Chromium every frame, which is the stutter
# the CSS-transform approach exists to remove. Everything the user sees morph
# is a transition inside the page.
#
# ISLAND_TOP_PAD matches `.stage{padding-top}` in pill.html — the page measures
# its hit regions from there, so Qt has to agree or the mask sits off the card.
# ISLAND_MARGIN is dead transparent space for the drop-shadow (34px blur) and
# the spring's overshoot; the mask below is what stops it eating clicks.
ISLAND_TOP_PAD = 20
ISLAND_MARGIN = 44

#: What the cards shipped today need. Kept as a FLOOR rather than replaced,
#: because it is the only number here with a running app behind it — the
#: geometry below is derived from declarations that have existed for one day.
_MIN_PILL_W, _MIN_PILL_H = 560, 480


def _island_window_size() -> tuple[int, int]:
    """Big enough for the largest card any installed module declares.

    This was two hand-typed numbers with a comment naming the card they had to
    clear — which works exactly until someone installs a module with a taller
    card and nobody remembers the comment. The window is sized ONCE at startup
    (resizing a QWebEngineView relayouts Chromium every frame), so being too
    small is not something the page can recover from at runtime: the card is
    simply drawn clipped.

    Never returns less than what ships today, so a module declaring nothing
    cannot shrink the window out from under a card that already fits. A broken
    manifest falls back to the floor for the same reason every other failure
    here does: a module going missing must not stop the eagle booting.
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
    """The card template and stylesheet the host should register for a module.

    Split out of `_setup_module_watcher._sync` so there is something to test.
    The bug that forced it: `[island] css = "style.css"` NAMES a file, and the
    loader used the declared value as the stylesheet's CONTENT. Every module
    that declares the key got a <style> tag holding the literal filename --
    zero rules -- and its card rendered with no stylesheet at all: every stage
    visible at once, no layout, the logo at its natural size. The whole suite
    passed while that shipped, because nothing anywhere called this code.

    Looked up beside the module's own manifest (see island_dirs).
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
    """Company marks a module ships in `island/logos.json`, keyed by symbol.

    The page used to carry its own two dozen logos while the trade module
    shipped hundreds that nothing read. The module owns its marks; the host
    only hands them to the page.
    """
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

#: No voice state interrupts a card any more, so the set and its predicate
#: are gone rather than emptied.
#:
#: The rule was: SPEAKING must not interrupt or no card survives long enough
#: to read; LISTENING must, "because by then the card is answering a question
#: they have moved on from". The first half was right. The second half was an
#: assumption, and the common case is its opposite -- the user talks over the
#: card BECAUSE of what is on it, and the card was destroyed for it.
#:
#: It was never really a ranking decision. The waveform and the card body both
#: wrote #pbody, so one of them had to lose the element; ranking was how that
#: got expressed. The island now carries a liveness dot in host chrome
#: (`#plive` in web/pill.html), the two stop sharing a node, and nothing has
#: to outrank anything.


import ctypes
import ctypes.util

_X11_LIB = None
_XEXT_LIB = None
_X11_LOADED = False

#: The installed X error handler, kept alive deliberately.
#:
#: ctypes callbacks are garbage collected like any other object, and Xlib keeps
#: only the raw pointer. Letting this fall out of scope leaves the server
#: calling into freed memory the next time anything goes wrong, which is a
#: worse crash than the one it exists to prevent.
_X_ERROR_HANDLER = None


def _install_x_error_handler() -> None:
    """Stop an X protocol error from killing the process.

    `_apply_x11_input_shape` is wrapped in `try/except Exception` and that
    catches NOTHING here. X errors are asynchronous: the server reports them
    later, Xlib's default handler prints the request and calls exit(), and no
    Python frame is unwound. The except clause is a false guarantee.

    Measured by constructing the shell headlessly: the pill and dashboard came
    up, XShapeCombineRectangles was handed a window id the platform had not
    realised, and the process died with

        X Error of failed request:  BadWindow (invalid Window parameter)
        Major opcode of failed request:  129 (SHAPE)

    having printed no traceback. A window that is not mapped yet, a compositor
    with no SHAPE extension, or an id that changed under a reparent all reach
    the same place — and the island is always-on-top chrome, so losing the hit
    region is a cosmetic fault that should never take the eagle with it.

    Swallowing is right rather than logging: these are advisory failures about
    one call, the caller already returns False, and an error handler that
    printed would spam a frame loop.
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
    """Load libX11/libXext once, with the prototypes ctypes will not guess.

    `restype` matters more than it looks. ctypes defaults it to `c_int`, so a
    64-bit `Display *` comes back truncated to 32 bits. On this machine it
    happened to work because malloc handed out a low address; inside a loaded
    Qt process the same call returns 0x7f..., and the truncated value is then
    passed back in as a live display pointer.
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
        # Before the first request goes out, not after the first one fails:
        # the default handler exits the process, so there is no second chance.
        _install_x_error_handler()
        return True
    except Exception:
        _X11_LIB = _XEXT_LIB = None
        return False


def _rounded_spans(x: int, y: int, w: int, h: int, radius: int):
    """A rounded rectangle as horizontal strips.

    One strip per row through the two corner bands, plus a single rectangle for
    everything between them. Strips rather than a filled path because both
    consumers below want rectangles: Qt's region and X11's shape extension
    take the same list, so the mask and the input shape cannot disagree about
    where the card is.
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
    """Set the window's INPUT shape, leaving its pixels unclipped.

    Shaping input rather than masking the widget is what keeps the drop shadow
    a smooth gradient: a Qt mask clips what is drawn as well as what is
    clicked, and the shadow then ends on a hard stepped edge.
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
    """Constrain `widget` so only the visible card accepts the mouse.

    Returns which mechanism ended up doing it — "x11" or "mask" — and never
    returns without one of them in place. That is the whole contract: a
    frameless, always-on-top, translucent window is deliberately much larger
    than the card so the shadow has room, and every pixel of that surplus is a
    hole punched over whatever the user was doing. Clearing the mask first and
    then discovering X11 is unavailable leaves exactly that hole, on Wayland,
    on Windows, on macOS, and anywhere libXext is missing.

    The X11 input shape is preferred because it leaves the drawn pixels alone;
    the Qt mask is the fallback, and it clips the shadow, which is a cosmetic
    price worth paying over an unclickable desktop.
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
    """A page that can only ever show the app's own files.

    Every page here carries the bridge, and the bridge installs modules and
    runs tools. A link followed inside the view would hand both to whatever
    site it pointed at, so the view never leaves; a clicked link opens in the
    user's browser instead.
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
    def connect_youtube(self): self._ui.connect_youtube()
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
    declares an account, read from disk so a module installed a moment ago
    is included."""
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
    """Hand a just-installed module any session the browser already holds.

    Returns the sites it still needs a sign-in for, so the island can say
    where to go. A user who signed in to the site before clicking Get never
    sees the module fail for want of it.
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

    #: Same reasoning as PillWebWindow._run_js_sig: the dispatcher thread can
    #: reach _push, and QWebEnginePage is GUI-thread-only. Marshalled here so
    #: it is a property of the window rather than of the caller.
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
        """The page drilled into a view the card on screen cannot fill.

        The island is drawn from whichever tools have already run. Drilling
        into the seven-layer view when only a quote has been fetched shows a
        card of em dashes, and until now the page had no way to ask for more.
        """
        self._ui.fetch_card_depth(module or "", ticker or "")

    @pyqtSlot(str, str)
    def prefetch_depth(self, module, ticker):
        self._ui.fetch_card_depth(module or "", ticker or "", quiet=True)

    @pyqtSlot(int, int, int, int)
    def hit_region(self, x, y, w, h):
        """The page telling Qt which pixels are actually the island.

        Called on every geometry change in the FSM. Everything outside stays
        transparent to the mouse, so the desktop under the window's dead
        margin keeps working.
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
        """The page telling Python whether a row of candidates is on screen.

        Pushed rather than polled: this is read on the voice turn path, and a
        round trip into QWebEngine there would block the loop that is trying
        to answer the user.
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
        """The page's decay timer reached the end and the card is gone.

        Python suppresses state repaints while a card is up; without this it
        would go on suppressing them after the card had already left.
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
            # delta-based so it's correct regardless of screenX origin
            w.move(o[2] + (sx - o[0]), o[3] + (sy - o[1]))


class PillWebWindow(QMainWindow):
    """Transparent, frameless, always-on-top window rendering web/pill.html —
    the Dynamic Island, pixel-identical to the artifact."""

    #: Every write to the page travels on this, from whatever thread asked.
    #:
    #: The tool dispatcher does NOT run on the GUI thread — `_launch_main_app`
    #: puts the asyncio loop on a daemon thread while Qt keeps the main one —
    #: and three tools reach the page without going through a slot:
    #: island_deck_move, island_deck_show and island_set_printer are called
    #: straight out of `_execute_tool`. QWebEnginePage.runJavaScript is not
    #: thread-safe, so those calls entered QtWebEngine from the wrong thread.
    #:
    #: Measured 2026-09-08 from a real session log. The last line written was
    #:     [Tool] ▶ island_deck_move (epoch=7) {direction=next}
    #: with no matching ✓ or ✗ and nothing after it: saying "next" over a deck
    #: of five models took the whole eagle down inside the call.
    #:
    #: Everything else was already safe by accident rather than by design —
    #: set_pill and set_island_ttl are reached from pyqtSignal slots, which Qt
    #: marshals for you. This makes the marshalling the property of the seam
    #: instead of a property of who happens to call it.
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
        # Persist the latest state so it survives (a) a set before the page has
        # loaded and (b) Chromium throttling the view while it's hidden.
        self._pending = (state, data or {})
        self._apply()

    def set_island_ttl(self, seconds: float):
        """One decay clock, owned by the page because only it knows about
        hover. Python supplies the number; the page runs it."""
        self.run_js(
            "window.island && (window.island.ttlMs = %d);"
            % max(0, int(seconds * 1000)))

    def register_module(self, key: str, template: str = "", style: str = "",
                        logos: dict | None = None, meta: dict | None = None):
        # Same trap as set() above: window.pill is the <div id="pill"> until
        # the page's script replaces it, so the object test alone passes.
        js = ("window.pill && typeof window.pill.registerModule === 'function' "
              "&& window.pill.registerModule(%s, %s, %s, %s, %s);") % (
            json.dumps(key), json.dumps(template), json.dumps(style),
            json.dumps(logos or {}), json.dumps(meta or {})
        )
        self.run_js(js)

    def run_js(self, js: str) -> None:
        """Fire-and-forget JavaScript at the pill page, from any thread.

        The window owns the view, so the one place that touches the page from
        Python is here rather than scattered through the shell — and because
        it is the one place, making it thread-safe makes every caller
        thread-safe. Emitting rather than calling: Qt gives a cross-thread
        signal a queued connection and runs the slot on the GUI thread, while
        a call already on the GUI thread stays direct and synchronous.
        """
        self._run_js_sig.emit(js)

    def _run_js_on_gui_thread(self, js: str) -> None:
        self.view.page().runJavaScript(js)

    def _apply(self):
        st, data = self._pending
        # Through run_js like everything else, so no path to the page bypasses
        # the marshal. These callers are already on the GUI thread; routing
        # them here costs a direct connection and removes a second doorway.
        # `window.pill &&` is NOT enough of a guard, and this was throwing on
        # every launch:
        #
        #     js: Uncaught TypeError: window.pill.set is not a function
        #
        # The page has <div class="pill" id="pill">, and an element with an id
        # becomes a property of window. So until the page's own script runs and
        # reassigns it, `window.pill` IS that div — truthy, so the guard passes,
        # and `.set` is undefined on a DIV. Measured: with only the markup
        # loaded, `window.pill === document.getElementById('pill')` is true.
        #
        # Guarding on the FUNCTION rather than the object. Skipping is safe:
        # loadFinished re-applies the pending state once the script has run,
        # which is what `self._pending` is for.
        self.run_js(
            "window.pill && typeof window.pill.set === 'function' "
            "&& window.pill.set(%s, %s)" % (json.dumps(st), json.dumps(data)))

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
        # The size, never the content. The clipboard carries passwords and
        # one-time codes, and this panel sits on top of every window.
        words = len(text.split())
        self._preview.setText(f"{words} word{'' if words == 1 else 's'} copied")
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
    consumes_audio_level = True

    # Declared at class level so core.ui_contract can verify conformance
    # statically. The backend assigns these at startup (main.py:850-852).
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
    _shop_sig         = pyqtSignal()           # an install moved on -> repaint the shop
    _module_ready_sig = pyqtSignal(str)        # (title) an install finished
    _module_gone_sig = pyqtSignal()            # a module was removed

    def __init__(self, face_path="face.png"):
        super().__init__()
        self._app = QApplication.instance() or QApplication(sys.argv)
        _name_the_app(self._app)
        load_app_fonts()
        self._app.setStyle("Fusion")

        # Set crisp high-res Aethelark Eagle icon for OS dock, taskbar, and Alt-Tab switcher
        icon_file = BASE / "assets" / "images" / "aethelark.png"
        if not icon_file.is_file():
            icon_file = BASE / "web" / "public" / "favicon-512x512.png"
        if icon_file.is_file():
            from PyQt6.QtGui import QIcon
            self._app.setWindowIcon(QIcon(str(icon_file)))

        self.pinned = False

        #: Modules the user has actually brought up in this session. Until a
        #: module is in here it may only reach the island by having live work
        #: on a machine — see `_ambient_may_speak`.
        self._ambient_engaged: set[str] = set()
        #: "<module>:<printer>" for every machine seen with a job on it this
        #: session, so the end of that job is allowed to speak and the end of
        #: a job nobody watched is not.
        self._ambient_live_jobs: set[str] = set()
        from core.activities import ActivityBoard
        self._activity_board = ActivityBoard()
        self._activity_sig: tuple = ()
        self._agent_busy: dict[str, tuple[bool, float]] = {}
        self._level_at = 0.0
        self._muted = False
        self._last_state = None      # dedupe: backend re-asserts SPEAKING per audio frame
        self._active_pill_context = None
        #: Whether what is on screen arrived unprompted. Declared here so
        #: `_may_take_the_screen` reads a real attribute rather than a default.
        self._active_pill_is_ambient = False
        self._log_lines = []
        self._assistant_name = "Aethelark"
        self._ui_ready = False
        self._routing = None      # cached capability routing; drives lane labels

        self.on_text_command = None
        #: Set by AethelarkLive. Called with one short line whenever the
        #: island starts showing something different.
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
        #: Whether the user is talking right now, from the voice loop's own
        #: speech detector. Drives the island between resting and hearing.
        self._hearing = False
        self._reconfig_win = None

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
        self._pill_context_sig.connect(self._on_pill_context)
        self._hearing_sig.connect(self._on_hearing)
        self._modules_sig.connect(self._on_modules_changed)
        self._ambient_refresh_sig.connect(self._refresh_ambient)
        self._notice_sig.connect(
            lambda text, action: self.pill_win.set_pill(
                "notice", {"text": text, "action": action}))
        #: Installs started from Settings: name -> {"state", "step"}. Kept
        #: here, not in the page, so closing Settings changes nothing about
        #: an install in flight and reopening it shows where it got to.
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

        # Clipboard assist — copy text, get quick actions. Opt-in: a panel on
        # every copy is noise for most people, and the clipboard is where
        # passwords and one-time codes pass through.
        self._last_clip = ""
        self._clip_panel = WebClipboardPanel()
        self._clip_panel.action_requested.connect(self._dispatch_command)
        try:
            self._app.clipboard().dataChanged.connect(self._on_clipboard_changed)
        except Exception as e:
            print(f"[aethelark_web] clipboard watch unavailable: {e}")

        # Module Dynamic Island Auto-Sync & OS File System Watcher
        self._setup_module_watcher()
        self._setup_ambient_events()
        self._prune_undo_journal()

        self.show_pill()

    def _prune_undo_journal(self):
        """Drop staged blobs nothing refers to any more.

        `core/journal.py` stages a full copy of every file the file tool
        overwrites or deletes, so that the change can be reversed. `prune()`
        exists to bound that and had no caller anywhere, so the copies
        accumulated for the life of the install — every overwrite paying disk
        for a rollback nothing was ever going to perform.

        file_controller's `undo` action is what spends them; this keeps the
        ones nothing can spend any more from piling up.

        Best-effort and never fatal: housekeeping that prevents a launch would
        be worse than the growth it prevents.
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
        """Zero-poll hot module loader: registers module templates into PillWebWindow
        and watches ~/.aethelark/modules/ for hot-mounts."""
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
        # A module installed or removed while the eagle runs. The directory
        # changes when `<module> register` creates or deletes its folder; the
        # settle timer turns a burst of writes into one refresh.
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
        """Grow the island's window when an installed module brings a bigger card.

        The window is sized once, at startup, for the cards installed then --
        resizing a QWebEngineView relayouts Chromium, which is why it is not
        resized per state. But a module installed while the eagle ran had its
        card drawn clipped until a restart. Growing once per install is one
        relayout. Never shrinks: a card already on screen keeps its room.
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

        #: Two seconds. The events these carry are minutes apart — a first
        #: layer, a spool running low — so polling faster buys nothing, and a
        #: stat() per module is cheap enough that it never needs to be clever.
        self._ambient_timer = QTimer(self)
        self._ambient_timer.timeout.connect(self._drain_ambient_events)
        self._ambient_timer.start(2000)

        # Before starting ours, clear any left running by an eagle that died
        # without getting to stop_all. They are not harmless: enough of them
        # exhaust a printer's websocket and every telemetry read then fails.
        self._ambient_listeners.sweep_orphans()

        started = self._ambient_listeners.ensure_running()
        print(f"[ambient] watching {len(manifests)} module(s); "
              f"started {started or 'no'} listener(s)")

    #: A machine with a job on it right now. Set by the operator, 2026-09-05:
    #: "If the printer is currently PRINTING, yes, bring it up without me
    #: saying anything, if it's NOT, don't bring it up unless I mention it."
    _LIVE_JOB_STATES = frozenset({
        "PRINTING", "PAUSED", "PREPARING", "HEATING", "LEVELING", "UPLOADING"})

    #: How a job ends. These speak only about a job this session watched start
    #: — the tail of a sentence already on screen. An ERROR sitting on an idle
    #: machine since before launch is not news, it is a status.
    _JOB_ENDED_STATES = frozenset({"COMPLETED", "STOPPED", "ERROR"})
    _STATUS_ONLY_STATES = frozenset({"IDLE", "STANDBY", "DISCONNECTED", "OFFLINE",
                                     "UNREACHABLE", "UNKNOWN", "UPLOADING"})

    def _ambient_may_speak(self, event) -> bool:
        """Whether an unasked-for event has earned the island.

        The rule this enforces is already written in `start_ambient_events`:
        modules do not speak first. What was missing was any test of it, so a
        printer that had been sitting idle since boot announced itself as the
        first thing on screen every single launch — and announced itself as
        UNREACHABLE, on the strength of one dropped UDP packet, while
        answering a broadcast from the same laptop in 0.19s.

        Two ways through:

          - The user brought this module up in this session. From then on it
            is a conversation and the module may add to it.
          - There is a job on the machine right now, or a job this session
            saw running has just ended. That is the second half of a sentence
            the user started by pressing print.

        Everything else — idle, offline, unreachable, a spool that is low on a
        machine nobody is using — is a status, not news. It is still there to
        be asked about; it just does not interrupt.
        """
        payload = event.payload if isinstance(event.payload, dict) else {}
        telemetry = payload.get("telemetry")
        state = ""
        if isinstance(telemetry, dict):
            state = str(telemetry.get("state") or "").upper()
        # The state can arrive as "PrinterState.PRINTING" through asdict on a
        # str-Enum, so match on the tail rather than the whole token.
        state = state.rsplit(".", 1)[-1]

        # A machine sitting there is status, not news, even in a session that
        # used the module: an idle Centauri Carbon 2 took the island over the
        # camera card of a print that had just started on the other printer.
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
            # Deliberately quiet, and deliberately not counted as a failure:
            # this is the normal state of a fleet nobody is using.
            return

        # The island shows one thing. poll() has already ordered by the
        # module's own priority, so the head is what belongs on screen; the
        # rest happened and are not worth interrupting for.
        top = allowed[0]
        if not self._may_take_the_screen(top):
            return
        if len(allowed) > 1:
            print(f"[ambient] {len(allowed)} events; showing {top.event} "
                  f"(priority {top.priority})")

        # duration_ms is the module saying how long its own event is worth
        # looking at — a first layer is worth six seconds, an error twelve.
        ttl = float(top.payload.get("duration_ms") or 0) / 1000.0
        self.set_pill_context(top.module, dict(top.payload),
                              ttl_s=ttl if ttl > 0 else 8.0, ambient=True)

    #: What an unprompted event has to score to take the screen away from a
    #: card the user asked for.
    #:
    #: The module ranks its own events against each other and the host ranks
    #: them against a person, because only the host knows there is a person
    #: reading. a3d's scale makes the line legible: printer_error 100,
    #: printer_paused 95 and spool_low_alert 80 are all things you would want
    #: to be told mid-sentence; first_layer_passed 35, print_complete 30,
    #: print_progress 25 and printer_standby 5 are things that can wait for the
    #: card in front of you to finish.
    #:
    #: Nothing is dropped either way -- an event that does not clear this is
    #: still on the bus and still answerable when asked. It just does not take
    #: the screen out from under a question the user asked thirty seconds ago.
    AMBIENT_INTERRUPTS_A_CARD = 80

    def _may_take_the_screen(self, event) -> bool:
        """Whether an unprompted event may replace what is already up.

        `_ambient_may_speak` answers a different question -- whether this
        module has earned the right to say anything at all -- and it was the
        only gate there was. So a printer_standby at priority 5 could clear a
        card the user had just asked for, two seconds after it arrived, purely
        because the drain runs on a 2s timer and repainted unconditionally.

        Three cases, and only the middle one is new:
          - nothing on screen: anything may take it.
          - a card the USER asked for: only an interrupt-grade event may.
          - a card an earlier ambient event put up: replaceable, because
            poll() has already ordered these by the module's own priority and
            the newer head is by definition the more important news.
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

    # ---- window transitions (main thread) ----
    def show_pill(self):
        self.pill_win.setGeometry(self._pill_geo)
        self.pill_win.show(); self.pill_win.raise_()

    # Expand/collapse is animated INSIDE the page (a GPU-composited transform on
    # the card), not by resizing the Chromium window. Resizing forced a full page
    # relayout every frame — the stutter you saw. The window is simply shown at
    # its final size and the card springs in via CSS.
    _MORPH_OUT_MS = 260   # must cover the aeOut keyframe duration in web/dashboard.html

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

    def halt_swarm(self):
        """INTERJECT · HALT SWARM: the eagle stops talking AND every agent stops.

        This button used to do the first half only -- it was wired to the same
        interrupt as Esc -- so agents kept building under a button that said
        HALT.
        """
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

    # ---- Settings panel (title-bar gear) ----
    # ---- Settings > Modules: the shop ----
    def install_module(self, name: str) -> None:
        """"Get" in the shop. Runs in the background, like an editor installing
        an extension: the user can close Settings and carry on; the island says
        when it is ready, and the running session picks it up with no restart.
        """
        name = (name or "").strip()
        if not name:
            return
        if (self._installs.get(name) or {}).get("state") in ("queued", "installing"):
            return
        self._installs[name] = {"state": "queued", "step": "Waiting to start…"}
        self._shop_sig.emit()
        threading.Thread(target=self._install_job, args=(name,), daemon=True,
                         name=f"install-{name}").start()

    def remove_module(self, name: str) -> None:
        """"Remove" in the shop. Its actions leave the eagle's tool list, which
        is the point: a module the user does not use costs the model attention
        on every turn. Adding it back is one Get."""
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
        # One at a time: two installs share the download tool and the
        # modules folder, and a queue is easier to read than a race.
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
        """GUI thread. Load the module now rather than waiting on the folder
        watcher, then tell the user on the island."""
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
        """Send a fresh settings snapshot to the panel. Computed off the GUI
        thread — the first snapshot probes installed browsers (shells out to
        xdg-settings), which must never block the UI / steal the audio GIL."""
        def _work():
            try:
                from actions.app_settings import snapshot
                self._push_settings_async(snapshot())
            except Exception as e:
                print(f"[aethelark_web] settings snapshot failed: {e}")
            # After the panel is drawn, so the shop has somewhere to go.
            self._shop_sig.emit()
        threading.Thread(target=_work, daemon=True).start()

    def _push_settings_async(self, snap):
        # From a worker thread we can't touch the web view directly.
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
            snap = save(data)
            if key:
                snap = set_brain_key(data.get("brain_provider")
                                     or snap["brain"]["provider"], key)
            self._push("setSettings", snap)
            self.sync_island_prefs()
            if prefs.enabled("labs_tools_enabled") != before and self.on_session_refresh:
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
        """Check a new key with Google, and only keep it if Google says yes.

        Off the GUI thread: the check is a network call. A key that was
        refused mid-session is replaced here too, so the voice loop waiting
        on `reconfig_complete` carries on without a relaunch.
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

    def connect_youtube(self):
        """One button, whichever wall the user is actually at.

        Connecting YouTube used to take four separate discoveries - sign in,
        find the token predates the YouTube scope, reconnect, then find the
        Data API switched off on the Cloud project. Each has a different fix,
        and the eagle spent two rounds blaming the user's account for the last
        one. This does the right one and says what it did.
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
                self._shop_sig.emit()
                # `_push` touches the web view, which from a worker thread
                # takes the whole app down - the crash the user hit. The
                # settings signal marshals it back to the GUI thread, which is
                # why `_push_settings_async` exists at all.
                self._push_settings_async(snapshot())
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
        self._reonboard = OnboardingWindow(on_done=_done, on_cancel=lambda: None)
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
                "trailing": "working" if busy else "done",
                "relevance": 70 if busy else 40, "stale_after_s": 86400}})
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
        # Metrics are cheap (a psutil snapshot); memory is only re-read + pushed
        # when the file actually changed, not every 2 seconds.
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

    # ---- signal slots (main thread) ----
    def _on_hearing(self, on: bool):
        self._hearing = bool(on)
        if (self._last_state or "").upper() == "LISTENING":
            self.pill_win.set_pill(self._pill_state("LISTENING"),
                                   self._pill_data("LISTENING"))

    def _on_modules_changed(self):
        """Installed modules changed on disk: refresh the session, then the
        listeners that watch for their events. Off the GUI thread, because the
        session refresh waits for any reply in flight to finish."""
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
        # Forwarded unconditionally: voice state and card state are orthogonal
        # and the page is the only thing that knows which is on screen, so it
        # is the only thing that can place a voice state. A card up routes it
        # to the liveness dot; no card routes it to the capsule body.
        self.pill_win.set_pill(self._pill_state(state), self._pill_data(state))
        self._push("setState", state)

    def _on_pill_context(self, module: str, data: dict, ttl_s: float,
                         ambient: bool = False):
        self._active_pill_context = (module, data)
        #: Whether what is on screen is something the user asked for. Read by
        #: `_may_take_the_screen` to decide whether an unprompted event is
        #: allowed to replace it. Travels on the signal rather than being set
        #: beside the emit, so it can never disagree with the context it
        #: describes — both are written here, on the GUI thread, together.
        self._active_pill_is_ambient = bool(ambient)
        self.pill_win.set_island_ttl(ttl_s)
        self.pill_win.set_pill(module, data)
        # The page owns decay, because only the page knows whether the cursor
        # is resting on the card. This timer is the backstop for the case where
        # the page never reports back — a stuck context would otherwise
        # suppress every state repaint for the rest of the session.
        self._pill_context_timer.stop()
        self._pill_context_timer.start(
            int(ttl_s * 1000) + self._BACKSTOP_GRACE_MS)

    def note_island_opened(self, module: str):
        """A card the page opened itself holds the screen like one the user
        asked Python for: background events wait behind it."""
        self._active_pill_context = (module, {})
        self._active_pill_is_ambient = False

    def note_island_deadline(self, left_ms: int):
        """The backstop follows the page's own clock instead of guessing it."""
        if self._active_pill_context is None:
            return
        self._pill_context_timer.stop()
        self._pill_context_timer.start(max(0, left_ms) + self._BACKSTOP_GRACE_MS)

    def live_media(self, module: str, subject: str, on: bool):
        """Switch a module's live media (a printer camera) on or off.

        Requests for one subject run in order on one thread and the last one
        wins, so a card closed while the camera was still starting ends with
        the camera off, not on.
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

    #: How long the backstop waits past the context's own decay before
    #: deciding the page is never going to report back.
    _BACKSTOP_GRACE_MS = 10_000

    def _on_pill_context_expired(self):
        if self._active_pill_context is None:
            return                      # the page already collapsed it
        if self.deck_is_open():
            # A deck is a decision in progress, and the page suspends its own
            # decay for exactly that reason. This timer did not know about
            # decks, so at ttl_s + grace it reset the pill and took five
            # downloaded models off the screen while the user was choosing
            # between them. It re-arms rather than standing down, so a context
            # that really is stuck still clears once the deck goes away.
            self._pill_context_timer.start(self._BACKSTOP_GRACE_MS)
            return
        if self._pill_state(self._last_state) != "idle":
            # A turn is in flight. The page holds a card through a voice turn
            # on purpose now -- that is what the liveness dot is for, and the
            # dwell clock is frozen for the duration -- so this firing during
            # a conversation is a false positive, not a stuck context. It
            # re-arms rather than standing down, exactly as the deck case
            # above does, so a genuinely stuck context still clears once the
            # talking stops.
            self._pill_context_timer.start(self._BACKSTOP_GRACE_MS)
            return
        self._active_pill_context = None
        self.pill_win.set_pill(self._pill_state(self._last_state),
                               self._pill_data(self._last_state))

    def _pill_state(self, s):
        """Which face the island shows for a voice-loop state.

        LISTENING is the state the loop sits in whenever the mic is open,
        which is almost always. Drawing it as a moving waveform meant the
        island animated forever, identically in a silent room. It now rests,
        and shows the waveform only while the user is actually talking.
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
        """Google refused the key: say so on the island and open the key screen.

        This used to print one line to a terminal and wait for a flag nothing
        ever set, so the only fix was quitting and relaunching.
        """
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
            reason="Google turned your Gemini key down. Paste a new one to "
                   "keep going.")
        self._reconfig_win.showMaximized()
        self._reconfig_win.raise_()

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
    def write_log(self, text):
        if not str(text).startswith(("SYS:", "ERR:")):
            print(f"[chat] {text}")
        self._log_sig.emit(text)
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
    # ---- the deck on the island, as the voice tools see it ----------------

    def deck_is_open(self) -> bool:
        return bool(getattr(self, "_deck_open", False))

    #: Hard cap on the island line. The page already trims to 96; this is the
    #: backstop, because this text is spent from the same context budget as the
    #: conversation and nothing else guards it on this side.
    _ISLAND_LINE_MAX = 120

    def note_island_subject(self, line: str) -> None:
        """Tell the model what the user is looking at.

        Deduplicated here as well as in the page: the page is one window into
        one process and this is the thing that actually spends context, so the
        guard belongs on both ends of the wire rather than only the far one.
        """
        line = (line or "").strip()[:self._ISLAND_LINE_MAX]
        if not line or line == getattr(self, "_island_subject", None):
            return
        self._island_subject = line
        handler = getattr(self, "on_island_subject", None)
        if handler is None:
            return
        # Off the GUI thread for the same reason _dispatch_command is: this
        # reaches the live session, and the session is not ours to block on.
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
        """Jump to a 0-based position in the deck.

        Returns whether that position exists, so "show me the eighth one" over
        a deck of five is answered rather than silently ignored. The size comes
        from the page, which is the only thing that knows it.
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
        """Put a module's card on the island.

        `ambient=True` marks a card the module raised on its own. It paints
        identically; the difference is that it does not count as the user
        having brought that module up. Without the distinction the ambient
        gate below would open itself: one unprompted card would register as
        engagement and every later one would be allowed through.
        """
        if not ambient:
            self._ambient_engaged.add(module)
        self._pill_context_sig.emit(
            module, data if isinstance(data, dict) else {}, ttl_s, bool(ambient))


    def fetch_card_depth(self, module: str, ticker: str, *, quiet: bool = False) -> None:
        """Run the deep tool for the card on screen and merge what comes back."""
        from core.card_assembly import needs_depth
        from main import CARD_STORE, MODULE_BUS, TOOL_SPECS, ToolSpec

        # What fills the deeper card is the module's to say: `prefetch` on its
        # cards. A module that declares none has no deeper view to fetch.
        tools = MODULE_BUS.depth_tools(module)
        if not tools or not ticker:
            return

        # A prefetch may already have filled this in while the capsule sat on
        # screen. Cashing that in here — a dict copy and a signal emit — is
        # what makes the click instant instead of paying the ~35s fetch again;
        # that is the entire point of prefetching. A quiet caller (the
        # prefetch itself) has nothing to repaint even when this fires.
        #
        # How long that stored answer stays worth reusing is the module's call,
        # not this file's: `[island].fresh_for` in its manifest. The store has
        # no expiry of its own and outlives the turn that filled it, so without
        # a bound a price fetched minutes ago was handed back and painted as
        # current. A module that declares nothing gets 0, which means no bound
        # — the old behaviour, and the right one for answers that do not rot.
        face = MODULE_BUS.island_face(tools[0])
        fresh_for = float(getattr(face, "fresh_for", 0.0) or 0.0)
        stored = CARD_STORE.card_for(ticker, fresh_for=fresh_for)
        if stored is not None and not needs_depth(stored):
            if not quiet:
                self.set_pill_context(module, stored, ttl_s=30.0)
            return

        # The card may already hold the deep answer — the model runs analyze
        # itself for "is Nvidia a good buy". Paying 35s again for figures
        # already on screen is the wrong kind of thorough.
        current = (self._active_pill_context or ("", {}))[1]
        if not needs_depth(current):
            return
        if getattr(self, "_depth_inflight", None) == (module, ticker):
            # Already fetching exactly this — but WHY it is being fetched can
            # change while it runs, and dropping the second caller loses that.
            #
            # Measured 2026-09-07 by instrumenting both calls. The capsule
            # lands and fires prefetch_depth(quiet=True) at t=7ms; the user
            # clicks into the layers 600ms later and fires
            # request_depth(quiet=False), which lands here and returns. The
            # quiet fetch then completes, absorbs analyze AND governance into
            # CARD_STORE — and repaints nothing, because a prefetch must never
            # repaint what someone is reading.
            #
            # So the expanded card sat empty over data that had already
            # arrived, and only filled if the user clicked a SECOND time after
            # the fetch finished. The optimisation meant to make the click
            # instant was the reason the click showed nothing.
            if not quiet:
                self._depth_wants_paint = True
            return
        self._depth_inflight = (module, ticker)
        #: Somebody is looking at the card this fetch is for. Set by the loud
        #: caller, whether it started the fetch or arrived during one.
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
                        # One tool having nothing to say is not a reason to
                        # abandon the rest of the card.
                        print(f"[aethelark_web] {tool} gave the card nothing "
                              f"for {ticker}: {result.message[:90]}")
                        continue
                    card = CARD_STORE.absorb(tool, payload, {"ticker": ticker})
                    # `_depth_wants_paint`, not `quiet`: this fetch may have
                    # STARTED quiet as a prefetch and been claimed by a click
                    # while it ran. What matters is whether anyone is looking
                    # now, not why it began.
                    if getattr(self, "_depth_wants_paint", False):
                        # Repaint after each, so the layers appear without
                        # waiting on governance behind them.
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


def _config() -> dict:
    try:
        return json.loads(API_KEYS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _is_onboarded() -> bool:
    """Set up means: finished the flow AND holding a key to think with.

    `onboarded` alone was not enough. Skipping through the flow, or choosing
    a provider the voice loop does not read, marked the install as set up with
    no Gemini key -- and the app then waited for one forever behind an idle
    island, with nothing on screen saying why.
    """
    cfg = _config()
    return bool(cfg.get("onboarded")) and bool(cfg.get("gemini_api_key"))


class OnboardBridge(QObject):
    """window.pybridge on the onboarding page."""
    def __init__(self, win): super().__init__(); self._win = win

    @pyqtSlot()
    def onboard_ready(self): self._win.on_ready()
    @pyqtSlot(str)
    def validate_key(self, key): self._win.validate_key(key)
    @pyqtSlot(str)
    def open_url(self, url): self._win.open_url(url)
    @pyqtSlot()
    def start_mic_check(self): self._win.start_mic_check()
    @pyqtSlot()
    def stop_mic_check(self): self._win.stop_mic_check()
    @pyqtSlot()
    def enable_echo_cancel(self): self._win.enable_echo_cancel()
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
    """First-run setup, and the place a refused key is fixed.

    `mode="setup"` walks welcome -> key -> name -> microphone -> done.
    `mode="key"` shows only the key screen: the running eagle's key was turned
    down, and the one thing to do is paste a new one.
    """
    _push_sig = pyqtSignal(str, str)   # (fn, json-payload) — marshals to GUI thread

    def __init__(self, on_done, mode: str = "setup", reason: str = "",
                 on_cancel=None):
        super().__init__()
        self._on_done = on_done
        self._on_cancel = on_cancel
        self._mode = mode
        self._reason = reason
        self._validated_key = ""
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
        self.view.load(QUrl.fromLocalFile(str(ONBOARDING_HTML)))

    def _do_push(self, fn, payload):
        self.view.page().runJavaScript(
            "window.onboarding && window.onboarding.%s && window.onboarding.%s(%s)"
            % (fn, fn, payload))

    def push(self, fn, payload):
        self._push_sig.emit(fn, json.dumps(payload))

    def on_ready(self):
        cfg = _config()
        start = {"mode": self._mode, "user_name": cfg.get("user_name") or "",
                 "has_key": bool(cfg.get("gemini_api_key")),
                 "aec_offer": self._offer_echo_cancel()}
        if self._mode == "key":
            start.update(step="key", reason=self._reason)
        self.push("startAt", start)

        # The one expensive full scan of the machine, persisted so later
        # launches load it in a millisecond. Off-thread: nvidia-smi and lspci
        # can block briefly.
        def _probe():
            try:
                from core.capability.profile import save, scan
                save(scan(full=True))
            except Exception as e:
                print(f"[onboarding] capability scan failed: {e}")
        threading.Thread(target=_probe, daemon=True).start()

    @staticmethod
    def _offer_echo_cancel() -> bool:
        """Whether to offer echo cancellation: it can be turned on here, and is not.

        Offered, never applied: it changes the default microphone and speakers
        for every app on the computer, which is the user's call. Talking over
        the eagle at a normal voice depends on it (core/barge_in.py).
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

    def open_url(self, url: str):
        from PyQt6.QtGui import QDesktopServices
        if str(url).startswith("https://"):
            QDesktopServices.openUrl(QUrl(str(url)))

    def validate_key(self, key: str):
        def _work():
            from core import gemini_key
            ok, message = gemini_key.check(key)
            if ok:
                self._validated_key = (key or "").strip()
            self.push("setKeyResult", {"ok": ok, "message": message})
        threading.Thread(target=_work, daemon=True).start()

    # ── microphone check ───────────────────────────────────────────────
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
        """Open the default input, report its level, and say when a voice is heard.

        Runs the same library the voice loop uses, so a microphone that works
        here works there -- and one that fails here fails with a sentence the
        user can act on, instead of a silent island later.
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

    # ── finishing ──────────────────────────────────────────────────────
    def complete(self, payload):
        self.stop_mic_check()
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
        if data.get("has_printer"):
            setup_wishes.add_wish(cfg, "install:3d")
        cfg.setdefault("auth_provider", "guest")
        if cfg.get("gemini_api_key"):
            cfg["onboarded"] = True
        try:
            user_paths.write_private(API_KEYS, json.dumps(cfg, indent=4))
        except Exception as e:
            print(f"[onboarding] could not write config: {e}")
        self.close()
        self._on_done()

    def cancel(self):
        """The ✕. First run: quit. Fixing a key: just close the window."""
        self.stop_mic_check()
        if self._on_cancel is not None:
            self.close()
            self._on_cancel()
        else:
            QApplication.instance().quit()


def _launch_main_app():
    ui = WebShellUI("face.png")

    # An answer given in setup ("I have a 3D printer") is acted on once, here,
    # through the same path as the Get button in Settings.
    def _say_if_update_is_ready():
        from core.update_check import newer_version
        if newer_version(BASE):
            ui.show_notice("A new version is ready. Run eagle update in a terminal.")

    QTimer.singleShot(20000, lambda: threading.Thread(
        target=_say_if_update_is_ready, daemon=True, name="update-check").start())

    cfg = _config()
    if setup_wishes.take_wish(cfg, "install:3d"):
        try:
            user_paths.write_private(API_KEYS, json.dumps(cfg, indent=4))
        except Exception as e:
            print(f"[setup] could not record the printer answer: {e}")
        QTimer.singleShot(2500, lambda: ui.install_module("3d"))

    def runner():
        ui.wait_for_api_key()
        from main import AethelarkLive, _run_core
        import asyncio

        # `_run_core` exists precisely for this and was never called. Its own
        # docstring names the failure it prevents — "a beautiful dead window is
        # the worst failure mode available, because it looks fine" — and that
        # is exactly what shipped: `asyncio.run` was invoked bare on a daemon
        # thread, so anything escaping run() killed the thread in silence,
        # leaving the window open and the pill animating over nothing.
        #
        # Measured 2026-09-07: the loop's default executor was shut down
        # mid-session, which broke getaddrinfo, which broke every reconnect.
        # The eagle spent the rest of the session retrying DNS every three
        # seconds, forever, with no supervisor to notice and nothing on screen
        # to say so.
        #
        # A restart builds a NEW event loop and therefore a new default
        # executor, so the class of failure that killed that session is
        # survivable now rather than terminal.
        def start():
            live = AethelarkLive(ui)
            asyncio.run(live.run())

        _run_core(ui, start)

    threading.Thread(target=runner, daemon=True).start()
    # keep a reference so the shell isn't garbage-collected
    QApplication.instance()._aethelark_ui = ui


def _name_the_app(app):
    """The window's identity for the desktop: a pinned launcher is matched to
    its windows by this name, and without it the taskbar shows two icons."""
    app.setApplicationName("aethelark")
    app.setDesktopFileName("aethelark")


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    _name_the_app(app)
    load_app_fonts()
    app.setStyle("Fusion")

    if _is_onboarded():
        _launch_main_app()
    else:
        onboard = OnboardingWindow(on_done=_launch_main_app)
        app._aethelark_onboard = onboard   # keep alive
        onboard.showMaximized()

    sys.exit(app.exec() or 0)


def _carries_no_signature(path) -> bool:
    """Whether a bundle carries no signature entry at all — which is a
    different thing from carrying one that does not verify. Reading a zip
    entry is not cryptography, so this needs no crypto library present."""
    import zipfile
    try:
        with zipfile.ZipFile(path) as archive:
            return "signature.bin" not in archive.namelist()
    except Exception:
        return False


def _maybe_modules() -> bool:
    """`eagle --install-module <file>`, `eagle --modules`.

    Handled before anything else starts, for the same reason `--doctor` is: a
    person who has just bought a module has not necessarily got the eagle
    running, and installing one must not require an API key, a window, or a
    brain that answers. The checking, the refusal of a tampered or
    path-escaping archive and the clean replace on upgrade all already existed
    in core.module_bus; until now the only thing that called them was a test.
    """
    import sys
    argv = sys.argv[1:]

    if "--modules" in argv:
        # One listing, whichever way a module arrived. This printed its own,
        # from a different reader than `eagle modules`, and the two could
        # disagree about the same directory.
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
    # A bundle carrying no signature at all is the one a developer builds for
    # themselves. One carrying a signature that does not verify is refused
    # whatever flags are passed — see install_bundle.
    allow_unsigned = "--allow-unsigned" in argv

    try:
        manifest = ModuleManager().install_bundle(path,
                                                  allow_unsigned=allow_unsigned)
    except BundleError as e:
        print(f"That module was not installed.\n\n{e}", file=sys.stderr)
        # Only for a bundle carrying no signature at all. A bundle whose
        # signature does not verify must never be answered with a flag to
        # try next: no flag launders that, and suggesting one reads as if
        # something might.
        if not allow_unsigned and _carries_no_signature(path):
            print(f"\nIf you built this module yourself, install it with:\n"
                  f"  eagle --install-module {path} --allow-unsigned",
                  file=sys.stderr)
        raise SystemExit(1)
    except OSError as e:
        print(f"That module was not installed.\n\n{path} could not be read: "
              f"{e}", file=sys.stderr)
        raise SystemExit(1)

    # A bundle carries source, not its dependencies: provisioning builds them
    # their own environment. It had no caller, so a bundle whose command lives
    # in that environment installed and could never run.
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
