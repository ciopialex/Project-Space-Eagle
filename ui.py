from __future__ import annotations

import json
import math
import os
import platform
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}

from PyQt6.QtCore import (
    QEasingCurve, QMimeData, QObject, QPointF, QRectF, QSize, Qt,
    QTimer, QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QDragEnterEvent, QDropEvent, QFont,
    QFontDatabase, QImage, QKeySequence, QLinearGradient, QPainter, QPainterPath,
    QPen, QPixmap, QRadialGradient, QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget, QProgressBar,
)

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"


def load_app_fonts() -> list:
    """Register Aethelark's bundled display + body fonts (Doto, Manrope) as Qt
    application fonts so the UI renders identically on every machine. Neither is
    a common system font, so without this the display text (wordmark, clock,
    agent glyphs, metric values) silently falls back and stops matching the
    design. Fonts ship in assets/fonts; Manrope is downloaded once only if
    missing."""
    from PyQt6.QtGui import QFontDatabase

    font_dir = BASE_DIR / "assets" / "fonts"
    font_dir.mkdir(parents=True, exist_ok=True)

    manrope = font_dir / "Manrope-Variable.ttf"
    if not manrope.exists() or manrope.stat().st_size == 0:
        try:
            import urllib.request
            url = "https://github.com/google/fonts/raw/main/ofl/manrope/Manrope%5Bwght%5D.ttf"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as response:
                manrope.write_bytes(response.read())
        except Exception as e:
            print(f"[Fonts] Manrope download failed: {e}")

    loaded = []
    for name in ("Doto.ttf", "Manrope-Variable.ttf"):
        p = font_dir / name
        if p.exists() and p.stat().st_size > 0:
            fid = QFontDatabase.addApplicationFont(str(p))
            if fid != -1:
                fams = QFontDatabase.applicationFontFamilies(fid)
                if fams:
                    loaded.append(fams[0])
    for want in ("Doto", "Manrope"):
        if want not in loaded:
            print(f"[Fonts] ⚠️  {want} not registered — text will fall back to a system font.")
    return loaded


# Backwards-compatible alias
load_manrope_font = load_app_fonts


def _make_gaussian_shadow_image(w: int, h: int, render_rect: QRectF, offset_y: float = 2.0, blur_radius: float = 4.5, alpha: int = 135) -> QImage:
    """Generates a true Gaussian-blurred drop shadow QImage matching the capsule curvature 1:1."""
    try:
        from PIL import Image, ImageFilter
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(0, 0, 0, alpha))
        p.setPen(Qt.PenStyle.NoPen)
        
        shadow_rect = render_rect.translated(0, offset_y)
        p.drawRoundedRect(shadow_rect, shadow_rect.height() / 2.0, shadow_rect.height() / 2.0)
        p.end()
        
        ptr = img.bits()
        ptr.setsize(img.height() * img.bytesPerLine())
        pil_img = Image.frombuffer('RGBA', (img.width(), img.height()), ptr, 'raw', 'BGRA', img.bytesPerLine(), 1)
        blurred = pil_img.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        
        b_bytes = blurred.tobytes('raw', 'BGRA')
        res_img = QImage(b_bytes, blurred.width, blurred.height, img.bytesPerLine(), QImage.Format.Format_ARGB32_Premultiplied)
        return res_img.copy()
    except Exception as e:
        print(f"Shadow blur generator fallback: {e}")
        return QImage()


def _make_blurred_logo_image(img: QImage, blur_radius: float = 1.1) -> QImage:
    """Applies a subtle 1.1px PIL Gaussian Blur to the AE logo image for liquid-smooth anti-aliased edges."""
    try:
        from PIL import Image, ImageFilter
        ptr = img.bits()
        ptr.setsize(img.height() * img.bytesPerLine())
        pil_img = Image.frombuffer('RGBA', (img.width(), img.height()), ptr, 'raw', 'BGRA', img.bytesPerLine(), 1)
        blurred = pil_img.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        b_bytes = blurred.tobytes('raw', 'BGRA')
        return QImage(b_bytes, blurred.width, blurred.height, img.bytesPerLine(), QImage.Format.Format_ARGB32_Premultiplied).copy()
    except Exception:
        return img


def _read_full_config() -> dict:
    """Read api_keys.json config dict. Returns {} on any error."""
    try:
        return json.loads(API_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


_DEFAULT_W, _DEFAULT_H = 980, 700
_MIN_W,     _MIN_H     = 820, 580
_LEFT_W  = 202
_RIGHT_W = 360

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


# ── iOS-grade spring easing ───────────────────────────────────────────────
# Qt has no native "spring" curve, so we sample a real spring step-response
# (mass / stiffness / damping) into a custom QEasingCurve — the faithful way
# to reproduce iOS motion, identical to the design mockup's linear() spring.
# "snappy" ≈ stiffness 300 / damping 20 (≈11% overshoot, then settle);
# geometry morphs use a gentler 260/24 (≈3% overshoot) so a collapsing window
# never overshoots past a valid rectangle. Callables are cached per-param so
# Qt keeps a live reference (custom easing funcs must not be garbage-collected).
_SPRING_FUNCS: dict = {}


def make_spring_curve(stiffness: float = 260.0, damping: float = 24.0,
                      mass: float = 1.0, settle: float = 0.0015) -> QEasingCurve:
    import math

    key = (stiffness, damping, mass, settle)
    f = _SPRING_FUNCS.get(key)
    if f is None:
        w0   = math.sqrt(stiffness / mass)
        zeta = min(damping / (2.0 * math.sqrt(stiffness * mass)), 0.999)
        wd   = w0 * math.sqrt(1.0 - zeta * zeta)
        T    = -math.log(settle) / (zeta * w0)          # time until settled

        def f(p: float) -> float:  # noqa: E306  (normalized 0..1 → eased value)
            if p <= 0.0:
                return 0.0
            if p >= 1.0:
                return 1.0
            t = p * T
            return 1.0 - math.exp(-zeta * w0 * t) * (
                math.cos(wd * t) + (zeta / math.sqrt(1.0 - zeta * zeta)) * math.sin(wd * t)
            )

        _SPRING_FUNCS[key] = f

    curve = QEasingCurve()
    curve.setCustomType(f)
    return curve



class C:
    BG        = "#0A0A0A"
    PANEL     = "rgba(24, 24, 28, 0.85)"
    PANEL2    = "rgba(30, 30, 35, 0.75)"
    BORDER    = "rgba(255, 255, 255, 0.08)"
    BORDER_B  = "rgba(255, 255, 255, 0.12)"
    BORDER_A  = "rgba(255, 255, 255, 0.06)"
    PRI       = "#E5E5EA"
    PRI_DIM   = "#C8C8D0"
    PRI_GHO   = "rgba(255, 255, 255, 0.05)"
    ACC       = "#C8C8D0"
    ACC2      = "#B0B0B0"
    GREEN     = "#22c55e"
    GREEN_D   = "#16a34a"
    RED       = "#ef4444"
    MUTED_C   = "#f43f5e"
    TEXT      = "#E5E5EA"
    TEXT_DIM  = "#B0B0B0"
    TEXT_MED  = "#C8C8D0"
    WHITE     = "#FFFFFF"
    DARK      = "#0F0F12"
    BAR_BG    = "rgba(255, 255, 255, 0.03)"


# Ana renge (accent) bağlı anahtarlar — durum renkleri (ACC, GREEN, RED…) sabit kalır
_HUE_LINKED = (
    "BG", "PANEL", "PANEL2", "BORDER", "BORDER_B", "BORDER_A",
    "PRI", "PRI_DIM", "PRI_GHO", "TEXT", "TEXT_DIM", "TEXT_MED",
    "WHITE", "DARK", "BAR_BG",
)
_PALETTE_DEFAULTS: dict[str, str] = {k: getattr(C, k) for k in _HUE_LINKED}

DEFAULT_UI_COLOR = _PALETTE_DEFAULTS["PRI"]


def apply_ui_accent(accent_hex: str) -> bool:
    """
    Seçilen accent rengine göre tüm turkuaz-ailesi paleti yeniden türetir
    (hue kaydırma — parlaklık/doygunluk oranları korunur, tasarım bozulmaz).
    Boyanan öğeler (HUD, dalga formu, metrikler) bir sonraki karede yeni
    rengi alır; stylesheet tabanlı paneller yeniden kurulduklarında alır.
    """
    import colorsys

    accent_hex = (accent_hex or "").strip().lower()
    if not (accent_hex.startswith("#") and len(accent_hex) == 7):
        return False
    try:
        int(accent_hex[1:], 16)
    except ValueError:
        return False

    def _hsv(h: str) -> tuple[float, float, float]:
        r = int(h[1:3], 16) / 255
        g = int(h[3:5], 16) / 255
        b = int(h[5:7], 16) / 255
        return colorsys.rgb_to_hsv(r, g, b)

    base_h            = _hsv(_PALETTE_DEFAULTS["PRI"])[0]
    acc_h, acc_s, _av = _hsv(accent_hex)
    dh   = acc_h - base_h
    grey = acc_s < 0.08   # griye yakın accent → tüm tema desaturize edilir

    for key, hex0 in _PALETTE_DEFAULTS.items():
        h, s, v = _hsv(hex0)
        if grey:
            s *= 0.15
        r, g, b = colorsys.hsv_to_rgb((h + dh) % 1.0, s, v)
        setattr(C, key, "#{:02x}{:02x}{:02x}".format(
            int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5)))
    return True


def current_palette() -> dict[str, str]:
    """C sınıfındaki accent'e bağlı renklerin anlık kopyası."""
    return {k: getattr(C, k) for k in _HUE_LINKED}


def retheme_all_widgets(old: dict[str, str], new: dict[str, str]) -> None:
    """
    CANLI tam tema değişimi. Uygulamadaki HER widget'ın stylesheet'inde eski
    palet renklerini yenileriyle değiştirir ve yeniden çizdirir. Böylece renk
    değişimi yalnızca boyanan öğelerde değil, panel/buton/kenarlık dahil tüm
    arayüzde ANINDA uygulanır — yeniden başlatma gerekmez.
    """
    mapping = {old[k].lower(): new[k].lower()
               for k in old if old[k].lower() != new.get(k, old[k]).lower()}
    if not mapping:
        return
    app = QApplication.instance()
    if app is None:
        return
    for w in app.allWidgets():
        try:
            ss = w.styleSheet()
            if ss:
                s2 = ss
                for o, n in mapping.items():
                    if o in s2:
                        s2 = s2.replace(o, n)
                if s2 != ss:
                    w.setStyleSheet(s2)
            w.update()
        except Exception:
            pass


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h)
    if a != 255 or not h.startswith("rgba"):
        c.setAlpha(a)
    return c


# ── Windows GPU via NVML DLL (no subprocess, no console window) ──────────────
_nvml_lib: object = None   # cached ctypes DLL
_nvml_ok:  object = None   # None=untested, True=works, False=unavailable


def _nvml_gpu_windows() -> float:
    """Return NVIDIA GPU utilisation % using nvml.dll directly — zero subprocess."""
    global _nvml_lib, _nvml_ok
    if _nvml_ok is False:
        return -1.0
    try:
        import ctypes

        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            for dll_name in ("nvml", r"C:\Windows\System32\nvml.dll"):
                try:
                    lib = ctypes.WinDLL(dll_name)
                    lib.nvmlInit_v2()
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            _nvml_ok = True
            return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)

        dev = ctypes.c_void_p()
        _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        util = _Util()
        _nvml_lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(util))
        _nvml_ok = True
        return float(util.gpu)
    except Exception:
        _nvml_ok = False
        return -1.0


class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0   
        self.gpu  = -1.0  
        self.tmp  = -1.0  
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(1.5)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        gpu = self._get_gpu()

        tmp = self._get_temp()

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        # pynvml — subprocess-free, works on all platforms if installed
        try:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)
        except Exception:
            pass

        # Windows: nvml.dll via ctypes (already cached in _nvml_gpu_windows)
        if _OS == "Windows":
            return _nvml_gpu_windows()

        # Linux / macOS: libnvidia-ml shared lib via ctypes
        try:
            import ctypes
            _lib = "libnvidia-ml.so.1" if _OS == "Linux" else "libnvidia-ml.dylib"

            class _Util(ctypes.Structure):
                _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

            nv = ctypes.CDLL(_lib)
            nv.nvmlInit_v2()
            dev = ctypes.c_void_p()
            nv.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
            u = _Util()
            nv.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u))
            return float(u.gpu)
        except Exception:
            pass

        return -1.0   # N/A — zero subprocess on all platforms

    def _get_temp(self) -> float:
        # psutil — works on Linux; occasionally Windows with driver support
        try:
            temps = psutil.sensors_temperatures()
            for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                         "cpu-thermal", "zenpower", "it8688"]:
                if name in temps and temps[name]:
                    return temps[name][0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass

        # Windows: wmi module (pure Python COM, zero subprocess)
        if _OS == "Windows":
            try:
                import wmi  # type: ignore
                w = wmi.WMI(namespace="root/wmi")
                tz = w.MSAcpi_ThermalZoneTemperature()
                if tz:
                    return (tz[0].CurrentTemperature / 10.0) - 273.15
            except Exception:
                pass

        return -1.0   # N/A — zero subprocess on all platforms

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()

class HudCanvas(QWidget):
    """The Eagle Crest Core — Aethelark's identity anchor.

    Replaces the former arc-reactor 'face' with the eagle emblem breathing
    inside concentric titanium rings under a slow radar sweep. The external
    interface is unchanged: callers set .muted / .speaking / .state /
    ._assistant_name, exactly as before.
    """

    def __init__(self, face_path: str, assistant_name: str = "AETHELARK", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted    = False
        self.speaking = False
        self.state    = "INITIALISING"
        self._assistant_name = assistant_name

        self._tick       = 0
        self._scale      = 1.0
        self._tgt_scale  = 1.0
        self._halo       = 55.0
        self._tgt_halo   = 55.0
        self._last_t     = time.time()
        self._ring_rot   = 0.0
        self._scan       = 0.0
        self._blink      = True
        self._blink_tick = 0

        # Eagle emblem (white on transparent) — tinted to titanium silver and
        # cached per render height so the tint work runs once, not per frame.
        self._crest_src: QImage | None = None
        self._crest_cache = None          # (height, QPixmap)
        self._load_crest()

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

    def _load_crest(self):
        for name in ("eagle_white.png", "aethelark_white.png"):
            path = Path(BASE_DIR) / "assets" / "images" / name
            if path.exists():
                img = QImage(str(path))
                if not img.isNull():
                    self._crest_src = img
                    return

    def _crest_pixmap(self, height: int):
        """Silver-tinted emblem at `height` px, cached (tint is not free)."""
        if self._crest_src is None:
            return None
        if self._crest_cache and self._crest_cache[0] == height:
            return self._crest_cache[1]
        src = self._crest_src
        w = max(1, int(src.width() * height / src.height()))
        scaled = src.scaled(w, height, Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
        tinted = QImage(scaled.size(), QImage.Format.Format_ARGB32_Premultiplied)
        tinted.fill(Qt.GlobalColor.transparent)
        tp = QPainter(tinted)
        tp.setRenderHint(QPainter.RenderHint.Antialiasing)
        tp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        tp.drawImage(0, 0, scaled)
        tp.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        g = QLinearGradient(0, 0, 0, scaled.height())
        g.setColorAt(0.0, QColor(233, 233, 242))
        g.setColorAt(0.5, QColor(200, 200, 208))
        g.setColorAt(1.0, QColor(150, 150, 158))
        tp.fillRect(tinted.rect(), QBrush(g))
        tp.end()
        px = QPixmap.fromImage(tinted)
        self._crest_cache = (height, px)
        return px

    def _accent(self) -> QColor:
        if self.muted:
            return QColor(C.MUTED_C)
        if self.speaking:
            return QColor("#3B82F6")
        if self.state == "LISTENING":
            return QColor(C.GREEN)
        return QColor(C.PRI_DIM)

    def _step(self):
        self._tick += 1
        now = time.time()
        if now - self._last_t > (0.12 if self.speaking else 0.5):
            if self.speaking:
                self._tgt_scale = random.uniform(1.05, 1.11)
                self._tgt_halo  = random.uniform(150, 195)
            elif self.muted:
                self._tgt_scale = random.uniform(0.998, 1.004)
                self._tgt_halo  = random.uniform(18, 32)
            else:
                self._tgt_scale = random.uniform(1.010, 1.045)
                self._tgt_halo  = random.uniform(55, 78)
            self._last_t = now

        sp = 0.32 if self.speaking else 0.10
        self._scale += (self._tgt_scale - self._scale) * sp
        self._halo  += (self._tgt_halo  - self._halo)  * sp

        self._ring_rot = (self._ring_rot + (0.9 if self.speaking else 0.35)) % 360
        self._scan     = (self._scan     + (3.4 if self.speaking else 1.5)) % 360

        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        W, H = self.width(), self.height()
        cx, cy = W / 2.0, H / 2.0
        fw = min(W, H)
        R  = fw * 0.30
        accent = self._accent()
        silver = QColor(C.PRI_DIM)

        # 1. Deep-space radial ground (matches the mockup's core-wrap)
        bg = QRadialGradient(cx, cy * 0.92, fw * 0.72)
        bg.setColorAt(0.0,  QColor("#1c1c24"))
        bg.setColorAt(0.34, QColor("#141419"))
        bg.setColorAt(0.66, QColor("#0a0a0d"))
        bg.setColorAt(1.0,  QColor("#060608"))
        p.fillRect(self.rect(), QBrush(bg))

        # 2. Corner brackets — targeting reticle
        m, bl = 18, 18
        p.setPen(QPen(qcol(silver, 60), 1.4))
        for bx, by, dx, dy in [(m, m, 1, 1), (W - m, m, -1, 1),
                               (m, H - m, 1, -1), (W - m, H - m, -1, -1)]:
            p.drawLine(QPointF(bx, by), QPointF(bx + dx * bl, by))
            p.drawLine(QPointF(bx, by), QPointF(bx, by + dy * bl))

        # 3. Radar sweep — conical gradient clipped to an annulus band
        p.save()
        r_out, r_in = R * 1.0, R * 0.46
        ann = QPainterPath()
        ann.setFillRule(Qt.FillRule.OddEvenFill)
        ann.addEllipse(QPointF(cx, cy), r_out, r_out)
        ann.addEllipse(QPointF(cx, cy), r_in, r_in)
        p.setClipPath(ann)
        sweep = QConicalGradient(cx, cy, self._scan)
        sc = silver
        sweep.setColorAt(0.00, QColor(sc.red(), sc.green(), sc.blue(), 90))
        sweep.setColorAt(0.10, QColor(sc.red(), sc.green(), sc.blue(), 24))
        sweep.setColorAt(0.26, QColor(0, 0, 0, 0))
        sweep.setColorAt(1.00, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(sweep))
        p.drawEllipse(QPointF(cx, cy), r_out, r_out)
        p.restore()

        # 4. Concentric titanium rings
        def ring(rr, a, w=1.0, dash=False, rot=0.0):
            pen = QPen(qcol(silver, a), w)
            if dash:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            if dash and rot:
                p.save()
                p.translate(cx, cy)
                p.rotate(rot)
                p.drawEllipse(QRectF(-rr, -rr, rr * 2, rr * 2))
                p.restore()
            else:
                p.drawEllipse(QRectF(cx - rr, cy - rr, rr * 2, rr * 2))

        ring(R * 1.00, 26, 1.0)
        ring(R * 0.78, 40, 1.0)
        ring(R * 0.60, 34, 1.0, dash=True, rot=self._ring_rot)
        ring(R * 0.90 * self._scale, int(28 + self._halo * 0.35), 1.2)  # breathing

        # 5. Tick ring
        t_out, t_in = R * 1.06, R * 0.99
        p.setPen(QPen(qcol(silver, 110), 1.0))
        for deg in range(0, 360, 6):
            rad = math.radians(deg)
            inn = t_in if deg % 30 == 0 else t_in + (t_out - t_in) * 0.45
            p.drawLine(QPointF(cx + t_out * math.cos(rad), cy - t_out * math.sin(rad)),
                       QPointF(cx + inn * math.cos(rad),  cy - inn * math.sin(rad)))

        # 6. Center glow disc — state colour, breathing
        gr = R * 0.62
        ga = int(min(self._halo, 200) * 0.6)
        glow = QRadialGradient(cx, cy, gr)
        glow.setColorAt(0.0,  QColor(accent.red(), accent.green(), accent.blue(), ga))
        glow.setColorAt(0.55, QColor(accent.red(), accent.green(), accent.blue(), int(ga * 0.25)))
        glow.setColorAt(1.0,  QColor(accent.red(), accent.green(), accent.blue(), 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(QPointF(cx, cy), gr, gr)

        # 7. Eagle emblem — breathing
        base_h = max(1, int(R * 0.92))
        px = self._crest_pixmap(base_h)
        if px is not None:
            crest_h = int(base_h * self._scale)
            dw = int(px.width() * crest_h / px.height())
            p.drawPixmap(
                int(cx - dw / 2), int(cy - crest_h / 2),
                px.scaled(dw, crest_h, Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation),
            )

        # 8. State label with a breathing status dot
        sy = cy + R * 1.28
        if self.muted:
            txt, col = "MUTED", QColor(C.MUTED_C)
        elif self.speaking:
            txt, col = "SPEAKING", QColor("#3B82F6")
        elif self.state == "THINKING":
            txt, col = "THINKING", QColor(C.PRI_DIM)
        elif self.state == "PROCESSING":
            txt, col = "PROCESSING", QColor(C.PRI_DIM)
        elif self.state == "LISTENING":
            txt, col = "LISTENING", QColor(C.GREEN)
        else:
            txt, col = str(self.state).upper(), QColor(C.PRI)
        dot = "●" if self._blink else "○"
        f = QFont("Manrope", 11, QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 3.0)
        p.setFont(f)
        p.setPen(QPen(col, 1))
        p.drawText(QRectF(0, sy, W, 28), Qt.AlignmentFlag.AlignCenter, f"{dot}   {txt}")


class MetricBar(QWidget):

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0–100
        self._text  = "--"
        self.setFixedHeight(38)
        self.setMinimumWidth(80)

    def set_value(self, pct: float, text: str):
        self._value = max(0.0, min(100.0, pct))
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        # No background box, fully borderless & transparent for premium weightless look

        bar_h   = 4
        bar_y   = H - bar_h - 4
        bar_w   = W - 4
        bar_x   = 2
        fill_w  = int(bar_w * self._value / 100)

        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)

        if fill_w > 0:
            p.setBrush(QBrush(bar_col))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)

        p.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(2, 4, 50, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(QFont("Manrope", 11, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 3, W - 2, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)

class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Manrope", 9))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                padding: 6px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG};
                width: 8px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 4px;
                min-height: 20px;
            }}
        """)
        self._queue: list[str] = []
        self._typing  = False
        self._text    = ""
        self._pos     = 0
        self._tag     = "sys"
        self._ai_name_lc = "aethelark"   # updated when assistant name changes
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text   = self._queue.pop(0)
        self._pos    = 0
        tl = self._text.lower()
        _ai_pfx = f"{self._ai_name_lc}:"
        if   tl.startswith("you:"):                              self._tag = "you"
        elif tl.startswith(_ai_pfx) or tl.startswith("aethelark:"): self._tag = "ai"
        elif tl.startswith("file:"):                             self._tag = "file"
        elif "err" in tl:                                        self._tag = "err"
        else:                                                    self._tag = "sys"
        self._tmr.start(6)

    def _step(self):
        if self._pos < len(self._text):
            ch  = self._text[self._pos]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.PRI),
                "err":  qcol(C.RED),
                "file": qcol(C.GREEN),
                "sys":  qcol(C.ACC2),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += 1
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)

_FILE_ICONS = {
    "image":   ("🖼", "#00d4ff"), "video":   ("🎬", "#ff6b00"),
    "audio":   ("🎵", "#cc44ff"), "pdf":     ("📄", "#ff4444"),
    "word":    ("📝", "#4488ff"), "excel":   ("📊", "#44bb44"),
    "code":    ("💻", "#ffcc00"), "archive": ("📦", "#ff8844"),
    "pptx":    ("📊", "#ff6622"), "text":    ("📃", "#aaaaaa"),
    "data":    ("🔧", "#88ddff"), "unknown": ("📎", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for Aethelark", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol("#001a24" if z._drag_over else ("#001218" if z._hovering else C.PANEL))
        p.setBrush(QBrush(bg_col)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   border_col = qcol(C.GREEN, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 14), QPointF(cx, cy + 4))
        p.drawLine(QPointF(cx - 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx + 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        p.setFont(QFont("Manrope", 8))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 8, W, 16), Qt.AlignmentFlag.AlignCenter,
                   "Drop file here  or  Click to Browse")
        p.setFont(QFont("Manrope", 7))
        p.setPen(QPen(qcol("#1a4a5a"), 1))
        p.drawText(QRectF(0, cy + 24, W, 14), Qt.AlignmentFlag.AlignCenter,
                   "Images · Video · Audio · PDF · Docs · Code · Data")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont("Manrope", 20))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, "⬇")
        p.setFont(QFont("Manrope", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 60
        p.setFont(QFont("Segoe UI Emoji", 22) if _OS == "Windows" else QFont("Arial", 22))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(QFont("Manrope", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(QRectF(tx, H * 0.18, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont("Manrope", 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.18 + 18, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  ·  {size_str}")

        p.setFont(QFont("Manrope", 6))
        p.setPen(QPen(qcol("#1e5c6a"), 1))
        par = str(path.parent)
        if len(par) > 42: par = "…" + par[-41:]
        p.drawText(QRectF(tx, H * 0.18 + 34, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        p.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "✕")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


class _CameraPreview(QWidget):
    """Floating overlay that briefly shows what the camera captured."""

    _W, _H = 244, 188

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            _CameraPreview {{
                background: rgba(0, 6, 10, 242);
                border: 1px solid {C.PRI};
                border-radius: 12px;
            }}
        """)
        self.setFixedWidth(self._W)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 5, 6, 6)
        lay.setSpacing(4)

        hdr = QHBoxLayout()
        title = QLabel("◈  VISUAL INPUT")
        title.setFont(QFont("Manrope", 7, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(16, 16)
        close_btn.setFont(QFont("Manrope", 8))
        close_btn.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; border: none;"
        )
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.hide)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        self._img_lbl = QLabel()
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setStyleSheet("background: transparent;")
        lay.addWidget(self._img_lbl)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        self.hide()

    def show_frame(self, img_bytes: bytes) -> None:
        px = QPixmap()
        px.loadFromData(img_bytes)
        if not px.isNull():
            max_w = self._W - 12
            scaled = px.scaled(
                max_w, 160,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img_lbl.setPixmap(scaled)
            self._img_lbl.setFixedSize(scaled.width(), scaled.height())
            self.adjustSize()
        self.show()
        self.raise_()
        self._timer.start(6_000)   # auto-dismiss after 6 s


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 12px;
            }}
        """)

        detected = {"darwin": "mac", "windows": "windows"}.get(
            _OS.lower(), "linux"
        )
        self._sel_os = detected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Manrope", font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("◈  INITIALISATION REQUIRED", 13, True))
        layout.addWidget(_lbl("Configure J.A.R.V.I.S. before first boot.", 9, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(_lbl("GEMINI API KEY", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…")
        self._key_input.setFont(QFont("Manrope", 10))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)
        layout.addSpacing(12)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(_lbl("OPERATING SYSTEM", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Auto-detected: {det_name}", 8, color=C.ACC2,
                               align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout(); os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows","⊞  Windows"),("mac","  macOS"),("linux","🐧  Linux")]:
            btn = QPushButton(label)
            btn.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        init_btn = QPushButton("▸  INITIALISE SYSTEMS")
        init_btn.setFont(QFont("Manrope", 10, QFont.Weight.Bold))
        init_btn.setFixedHeight(36)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {"windows":(C.PRI,"#001a22"),"mac":(C.ACC2,"#1a1400"),"linux":(C.GREEN,"#001a0d")}
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 3px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #000d12; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        self.done.emit(key, self._sel_os)


class HueWheel(QWidget):
    """
    Dairesel renk seçici. Kullanıcı tutamacı (küçük beyaz daire) çarkın
    çevresinde sürükleyerek TÜM renk tonları arasından seçim yapar.
    Merkezdeki dolu daire seçilen rengin canlı önizlemesidir.
    """

    hue_picked    = pyqtSignal(str)   # sürükleme sırasında (canlı)
    hue_committed = pyqtSignal(str)   # tutamaç bırakıldığında

    _RING = 16   # halka kalınlığı (px)

    def __init__(self, initial_hex: str = DEFAULT_UI_COLOR, parent=None):
        super().__init__(parent)
        self.setFixedSize(148, 148)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hue  = 0.53
        self._drag = False
        self.set_color(initial_hex)

    # ── API ──────────────────────────────────────────────────────────────────
    def color(self) -> str:
        return QColor.fromHsvF(self._hue, 1.0, 1.0).name()

    def set_color(self, hex_str: str):
        c = QColor((hex_str or "").strip())
        if c.isValid() and c.hsvHueF() >= 0:
            self._hue = c.hsvHueF()
            self.update()

    # ── geometri yardımcıları ────────────────────────────────────────────────
    def _ring_rect(self) -> QRectF:
        m = self._RING / 2 + 3
        return QRectF(self.rect()).adjusted(m, m, -m, -m)

    def _hue_from_pos(self, pos: QPointF) -> float:
        c  = QRectF(self.rect()).center()
        dx = pos.x() - c.x()
        dy = c.y() - pos.y()          # ekran y'si aşağı — matematiksel eksene çevir
        ang = math.atan2(dy, dx)      # [-π, π], saat yönünün tersi
        return (ang / (2 * math.pi)) % 1.0

    # ── çizim ────────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect   = self._ring_rect()
        center = rect.center()

        grad = QConicalGradient(center, 0)
        for i in range(0, 361, 20):
            grad.setColorAt(i / 360.0, QColor.fromHsvF((i % 360) / 360.0, 1.0, 1.0))
        p.setPen(QPen(QBrush(grad), self._RING))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)

        # merkez önizleme dairesi
        preview = QColor.fromHsvF(self._hue, 1.0, 1.0)
        inner   = rect.adjusted(30, 30, -30, -30)
        p.setPen(QPen(qcol(C.BORDER_B), 1))
        p.setBrush(QBrush(preview))
        p.drawEllipse(inner)

        # sürüklenen tutamaç
        r   = rect.width() / 2
        ang = self._hue * 2 * math.pi
        hx  = center.x() + r * math.cos(ang)
        hy  = center.y() - r * math.sin(ang)
        p.setPen(QPen(QColor("#00060a"), 2))
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawEllipse(QPointF(hx, hy), 7.5, 7.5)

    # ── fare ─────────────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        self._drag = True
        self._hue  = self._hue_from_pos(e.position())
        self.update()
        self.hue_picked.emit(self.color())

    def mouseMoveEvent(self, e):
        if self._drag:
            self._hue = self._hue_from_pos(e.position())
            self.update()
            self.hue_picked.emit(self.color())

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.hue_committed.emit(self.color())


class CustomizeOverlay(QWidget):
    """Floating overlay — change assistant name, user name and UI colour."""

    saved = pyqtSignal(str, str, str)   # assistant_name, user_name, ui_color
    _OW, _OH = 440, 740

    def __init__(self, assistant_name="Aethelark", user_name="",
                 ui_color=DEFAULT_UI_COLOR, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            CustomizeOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 12px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.setSpacing(8)

        def _lbl(txt, fs=9, bold=False, color=C.PRI, align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt); w.setAlignment(align)
            w.setFont(QFont("Manrope", fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        _fs = (f"QLineEdit {{ background: #000d12; color: {C.TEXT}; "
               f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
               f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        lay.addWidget(_lbl("⚙  CUSTOMISE ASSISTANT", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        lay.addWidget(_lbl("ASSISTANT NAME", 8, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._name_input = QLineEdit(assistant_name)
        self._name_input.setFont(QFont("Manrope", 10))
        self._name_input.setFixedHeight(32)
        self._name_input.setStyleSheet(_fs)
        lay.addWidget(self._name_input)

        lay.addSpacing(4)
        lay.addWidget(_lbl("YOUR NAME  (leave blank for default sir / efendim)", 8,
                            color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        self._user_input = QLineEdit(user_name)
        self._user_input.setPlaceholderText("e.g.  Tony   (leave blank for auto)")
        self._user_input.setFont(QFont("Manrope", 10))
        self._user_input.setFixedHeight(32)
        self._user_input.setStyleSheet(_fs)
        lay.addWidget(self._user_input)

        # ── UI colour — renk çarkı ───────────────────────────────────────────
        lay.addSpacing(4)
        clr_hdr = QHBoxLayout()
        clr_hdr.addWidget(_lbl("UI COLOUR  —  drag the handle", 8,
                               color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        clr_hdr.addStretch()
        df_btn = QPushButton("DEFAULT")
        df_btn.setFixedSize(64, 20)
        df_btn.setFont(QFont("Manrope", 7, QFont.Weight.Bold))
        df_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        df_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        df_btn.clicked.connect(lambda: self._set_color(DEFAULT_UI_COLOR))
        clr_hdr.addWidget(df_btn)
        lay.addLayout(clr_hdr)

        self._initial_color = (ui_color or DEFAULT_UI_COLOR).strip().lower()
        self._sel_color     = self._initial_color
        self.on_preview     = None   # callable(hex) — canlı önizleme; MainWindow bağlar

        self._wheel = HueWheel(self._sel_color)
        wheel_row = QHBoxLayout()
        wheel_row.addStretch(); wheel_row.addWidget(self._wheel); wheel_row.addStretch()
        lay.addLayout(wheel_row)
        self._wheel.hue_picked.connect(self._on_wheel_pick)
        self._wheel.hue_committed.connect(self._on_wheel_commit)

        self._hex_input = QLineEdit(self._sel_color)
        self._hex_input.setPlaceholderText("#00d4ff   (custom hex colour)")
        self._hex_input.setFont(QFont("Manrope", 10))
        self._hex_input.setFixedHeight(28)
        self._hex_input.setStyleSheet(_fs)
        self._hex_input.textEdited.connect(self._on_hex_edited)
        lay.addWidget(self._hex_input)

        # ── Default Browser ───────────────────────────────────────────────────
        lay.addSpacing(10)
        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep2)
        lay.addWidget(_lbl("🌐  DEFAULT BROWSER", 10, True))
        lay.addSpacing(2)
        lay.addWidget(_lbl(
            "Aethelark will use this browser for all URL and search commands",
            8, color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft
        ))

        self._browser_combo = QComboBox()
        self._browser_combo.setFont(QFont("Manrope", 10))
        self._browser_combo.setFixedHeight(32)
        self._browser_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._browser_combo.setStyleSheet(f"""
            QComboBox {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 10px;
            }}
            QComboBox:focus {{ border: 1px solid {C.PRI}; }}
            QComboBox::drop-down {{ border: none; width: 20px; }}
            QComboBox QAbstractItemView {{
                background: #080e12; color: {C.TEXT};
                border: 1px solid {C.BORDER_B}; selection-background-color: {C.PRI_GHO};
            }}
        """)
        _browser_options = [
            ("System Default",  ""),
            ("Google Chrome",   "chrome"),
            ("Chromium",        "chromium"),
            ("Mozilla Firefox", "firefox"),
            ("Brave",           "brave"),
            ("Microsoft Edge",  "edge"),
        ]
        for label, key in _browser_options:
            self._browser_combo.addItem(label, userData=key)

        # Pre-select from saved config
        _saved_browser = _read_full_config().get("default_browser", "").lower().strip()
        for i, (_, key) in enumerate(_browser_options):
            if key == _saved_browser:
                self._browser_combo.setCurrentIndex(i)
                break
        lay.addWidget(self._browser_combo)

        # ── Connected Accounts & Integrations ────────────────────────────────
        lay.addSpacing(10)
        sep3 = QFrame(); sep3.setFrameShape(QFrame.Shape.HLine)
        sep3.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep3)
        lay.addWidget(_lbl("🔗  CONNECTED ACCOUNTS", 10, True))
        lay.addSpacing(2)
        lay.addWidget(_lbl(
            "Connect apps so Aethelark can send emails, post to social media, and more",
            8, color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft
        ))
        lay.addSpacing(4)

        _acct_style = f"""
            QFrame {{
                background: rgba(255,255,255,0.03);
                border: 1px solid {C.BORDER};
                border-radius: 6px;
            }}
        """
        _btn_style = f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                padding: 3px 10px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}
        """
        _connected_style = f"color: {C.GREEN}; font-size: 8pt; background: transparent; border: none;"
        _pending_style   = f"color: {C.TEXT_DIM}; font-size: 8pt; background: transparent; border: none;"

        def _make_account_row(icon: str, name: str, desc: str, cfg_key: str) -> QFrame:
            row = QFrame()
            row.setStyleSheet(_acct_style)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(10, 6, 10, 6)
            rl.setSpacing(8)

            icon_lbl = QLabel(icon)
            icon_lbl.setFont(QFont("Manrope", 13))
            icon_lbl.setStyleSheet("background: transparent; border: none;")
            rl.addWidget(icon_lbl)

            text_col = QVBoxLayout()
            text_col.setSpacing(1)
            name_lbl = QLabel(name)
            name_lbl.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
            name_lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent; border: none;")
            desc_lbl = QLabel(desc)
            desc_lbl.setFont(QFont("Manrope", 8))
            desc_lbl.setStyleSheet(_pending_style)
            text_col.addWidget(name_lbl)
            text_col.addWidget(desc_lbl)
            rl.addLayout(text_col)
            rl.addStretch()

            _saved = _read_full_config().get(cfg_key, "")
            if _saved:
                status = QLabel("✓ Connected")
                status.setStyleSheet(_connected_style)
                rl.addWidget(status)

            cfg_btn = QPushButton("Configure")
            cfg_btn.setFont(QFont("Manrope", 8))
            cfg_btn.setFixedHeight(26)
            cfg_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            cfg_btn.setStyleSheet(_btn_style)
            cfg_btn.clicked.connect(lambda checked, k=cfg_key, n=name: self._configure_account(k, n))
            rl.addWidget(cfg_btn)
            return row

        lay.addWidget(_make_account_row("✉", "Email / SMTP",
            "Send emails on your behalf", "smtp_config"))
        lay.addSpacing(4)
        lay.addWidget(_make_account_row("📸", "Instagram",
            "Post photos, reels, and stories", "instagram_token"))
        lay.addSpacing(4)
        lay.addWidget(_make_account_row("🐦", "Twitter / X",
            "Post tweets and threads", "twitter_token"))
        lay.addSpacing(4)
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)

        save_btn = QPushButton("▸  APPLY CHANGES")
        save_btn.setFixedHeight(34)
        save_btn.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setFixedHeight(34)
        cancel_btn.setFont(QFont("Manrope", 9))
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

    # ── renk akışı ───────────────────────────────────────────────────────────
    def _set_color(self, hx: str, update_wheel: bool = True, preview: bool = True):
        """Seçili rengi günceller; hex kutusu + çark senkron kalır, tema canlı önizlenir."""
        self._sel_color = hx.strip().lower()
        self._hex_input.blockSignals(True)
        self._hex_input.setText(self._sel_color)
        self._hex_input.blockSignals(False)
        if update_wheel:
            self._wheel.set_color(self._sel_color)
        if preview and self.on_preview:
            self.on_preview(self._sel_color)

    def _on_wheel_pick(self, hx: str):
        # Sürükleme sırasında: hex kutusunu güncelle, temayı henüz uygulama
        self._sel_color = hx
        self._hex_input.blockSignals(True)
        self._hex_input.setText(hx)
        self._hex_input.blockSignals(False)

    def _on_wheel_commit(self, hx: str):
        # Tutamaç bırakıldı → tüm arayüzü canlı önizle
        self._set_color(hx, update_wheel=False)

    def _on_hex_edited(self, text: str):
        t = text.strip().lower()
        if t.startswith("#") and len(t) == 7:
            try:
                int(t[1:], 16)
            except ValueError:
                return
            self._set_color(t, update_wheel=True, preview=True)

    def _cancel(self):
        # Revert preview if applied
        if self.on_preview and self._sel_color != self._initial_color:
            self.on_preview(self._initial_color)
        self.hide()

    def _save(self):
        name = self._name_input.text().strip() or "Aethelark"
        user = self._user_input.text().strip()
        # Persist default browser selection to config
        try:
            data = _read_full_config()
            browser_key = self._browser_combo.currentData() or ""
            data["default_browser"] = browser_key
            API_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
        except Exception as e:
            print(f"[Settings] Browser config save error: {e}")
        self.saved.emit(name, user, self._sel_color or DEFAULT_UI_COLOR)
        self.hide()

    def _configure_account(self, cfg_key: str, service_name: str):
        """Show a simple credential dialog for connecting a service."""
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Configure {service_name}")
        dlg.setStyleSheet(f"""
            QDialog {{ background: #060c10; color: {C.TEXT}; }}
            QLabel  {{ color: {C.TEXT}; background: transparent; }}
            QLineEdit {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
            QDialogButtonBox QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; padding: 4px 14px;
            }}
            QDialogButtonBox QPushButton:hover {{ background: {C.PRI_GHO}; }}
        """)
        dlg_lay = QVBoxLayout(dlg)
        dlg_lay.setSpacing(8)
        dlg_lay.setContentsMargins(20, 16, 20, 16)

        dlg_lay.addWidget(QLabel(f"<b>{service_name}</b> credentials"))

        _fs_dlg = (f"QLineEdit {{ background: #000d12; color: {C.TEXT}; "
                   f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
                   f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        # Load existing values
        existing = _read_full_config().get(cfg_key, {})
        if isinstance(existing, str):
            existing = {"token": existing}

        fields: dict[str, QLineEdit] = {}

        if cfg_key == "smtp_config":
            for label, key, placeholder, is_pw in [
                ("SMTP Server",   "host",     "smtp.gmail.com",        False),
                ("Port",          "port",     "587",                   False),
                ("Email Address", "email",    "you@gmail.com",          False),
                ("Password / App Key", "password", "App-specific password", True),
            ]:
                dlg_lay.addWidget(QLabel(label))
                inp = QLineEdit(str(existing.get(key, "")))
                inp.setPlaceholderText(placeholder)
                inp.setFont(QFont("Manrope", 10))
                inp.setFixedHeight(30)
                inp.setStyleSheet(_fs_dlg)
                if is_pw:
                    inp.setEchoMode(QLineEdit.EchoMode.Password)
                dlg_lay.addWidget(inp)
                fields[key] = inp
        else:
            lbl_map = {
                "instagram_token": ("Access Token", "Instagram Graph API access token"),
                "twitter_token":   ("Bearer Token",  "Twitter/X API Bearer token"),
            }
            label, placeholder = lbl_map.get(cfg_key, ("Token", "Paste your API token here"))
            dlg_lay.addWidget(QLabel(label))
            inp = QLineEdit(existing.get("token", ""))
            inp.setPlaceholderText(placeholder)
            inp.setFont(QFont("Manrope", 10))
            inp.setFixedHeight(30)
            inp.setStyleSheet(_fs_dlg)
            inp.setEchoMode(QLineEdit.EchoMode.Password)
            dlg_lay.addWidget(inp)
            fields["token"] = inp

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        btns.setFont(QFont("Manrope", 9))
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        dlg_lay.addWidget(btns)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            values = {k: v.text().strip() for k, v in fields.items()}
            try:
                data = _read_full_config()
                data[cfg_key] = values
                API_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
                print(f"[Settings] {service_name} credentials saved.")
            except Exception as e:
                print(f"[Settings] {service_name} save error: {e}")


class ClipboardPanel(QWidget):
    """Floating panel shown when text is copied — offers quick Jarvis actions."""

    action_requested = pyqtSignal(str)
    _W, _H = 326, 112

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ClipboardPanel {{
                background: rgba(0, 8, 14, 248);
                border: 1px solid {C.BORDER_B};
                border-radius: 12px;
            }}
        """)
        self.setFixedWidth(self._W)
        self._clip_text = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 7)
        lay.setSpacing(4)

        hdr = QHBoxLayout(); hdr.setSpacing(4)
        icon_lbl = QLabel("◈  CLIPBOARD DETECTED")
        icon_lbl.setFont(QFont("Manrope", 7, QFont.Weight.Bold))
        icon_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        hdr.addWidget(icon_lbl); hdr.addStretch()
        x_btn = QPushButton("✕")
        x_btn.setFixedSize(16, 16)
        x_btn.setFont(QFont("Manrope", 8))
        x_btn.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        x_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        x_btn.clicked.connect(self.hide)
        hdr.addWidget(x_btn)
        lay.addLayout(hdr)

        self._preview = QLabel()
        self._preview.setFont(QFont("Manrope", 8))
        self._preview.setStyleSheet(f"""
            color: {C.TEXT}; background: {C.PANEL2};
            border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 6px;
        """)
        self._preview.setWordWrap(False)
        self._preview.setFixedHeight(28)
        lay.addWidget(self._preview)

        btn_row = QHBoxLayout(); btn_row.setSpacing(4)
        _bs = (f"QPushButton {{ background: {C.PANEL2}; color: {C.TEXT_MED}; "
               f"border: 1px solid {C.BORDER}; border-radius: 2px; }}"
               f"QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}")
        for label, cmd_fmt in [
            ("TRANSLATE", "Translate this text to English: {text}"),
            ("SUMMARISE", "Summarise this: {text}"),
            ("EXPLAIN",   "Explain this: {text}"),
            ("FIX",       "Fix grammar and spelling: {text}"),
        ]:
            b = QPushButton(label)
            b.setFixedHeight(22)
            b.setFont(QFont("Manrope", 7, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(_bs)
            b.clicked.connect(lambda _, c=cmd_fmt: self._trigger(c))
            btn_row.addWidget(b)
        lay.addLayout(btn_row)

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.hide)
        self.hide()

    def _trigger(self, cmd_fmt: str):
        if self._clip_text:
            self.action_requested.emit(cmd_fmt.format(text=self._clip_text[:800]))
        self.hide()

    def show_clipboard(self, text: str):
        self._clip_text = text
        preview = text[:58].replace('\n', ' ')
        if len(text) > 58:
            preview += "…"
        self._preview.setText(f'"{preview}"')
        self.show(); self.raise_()
        self._dismiss_timer.start(8000)


class RemoteKeyOverlay(QWidget):
    """Floating overlay — QR code for instant phone pairing + manual key fallback."""

    closed = pyqtSignal()

    _OW, _OH = 400, 465

    def __init__(self, url: str, key: str, auto_login_url: str = "",
                 manual_url: str = "", expiry_secs: int = 600, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            RemoteKeyOverlay {{
                background: rgba(0, 4, 12, 0.95);
                border: 1px solid {C.BORDER_B};
                border-radius: 14px;
            }}
        """)
        self._expiry          = time.time() + expiry_secs
        self._on_new_key      = None
        self._auto_login_url  = auto_login_url
        self._manual_url      = manual_url or url

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        lay.setSpacing(5)

        def _lbl(txt, fs=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Manrope", fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            w.setWordWrap(True)
            return w

        lay.addWidget(_lbl("◈  REMOTE ACCESS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep)

        # ── QR code ───────────────────────────────────────────────────────────
        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setFixedSize(176, 176)
        self._qr_label.setStyleSheet(
            "background: white; border-radius: 10px; padding: 4px;"
        )
        qr_row = QHBoxLayout()
        qr_row.addStretch()
        qr_row.addWidget(self._qr_label)
        qr_row.addStretch()
        lay.addLayout(qr_row)

        self._update_qr(auto_login_url)

        lay.addWidget(_lbl("Scan with phone camera to connect instantly", 8, color=C.TEXT_DIM))

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_lbl("Or enter manually:", 7, color=C.TEXT_DIM,
                           align=Qt.AlignmentFlag.AlignLeft))

        self._url_lbl = QLabel(self._manual_url)
        self._url_lbl.setFont(QFont("Manrope", 8))
        self._url_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        self._url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self._url_lbl)

        self._key_lbl = QLabel(key)
        self._key_lbl.setFont(QFont("Manrope", 28, QFont.Weight.Bold))
        self._key_lbl.setStyleSheet(f"""
            color: {C.ACC};
            background: {C.PANEL2};
            border: 1px solid {C.BORDER_B};
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 10px;
        """)
        self._key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._key_lbl)

        self._timer_lbl = QLabel()
        self._timer_lbl.setFont(QFont("Manrope", 8))
        self._timer_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._timer_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._timer_lbl)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        new_btn = QPushButton("NEW KEY")
        new_btn.setFixedHeight(32)
        new_btn.setFont(QFont("Manrope", 8, QFont.Weight.Bold))
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 5px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        new_btn.clicked.connect(self._refresh_key)
        btn_row.addWidget(new_btn)

        close_btn = QPushButton("DISMISS")
        close_btn.setFixedHeight(32)
        close_btn.setFont(QFont("Manrope", 8, QFont.Weight.Bold))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self._do_close)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        self._ctimer = QTimer(self)
        self._ctimer.timeout.connect(self._tick)
        self._ctimer.start(1000)
        self._tick()

    def set_new_key_callback(self, fn) -> None:
        self._on_new_key = fn

    def _update_qr(self, url: str) -> None:
        if not url:
            self._qr_label.setText("—")
            return
        try:
            import qrcode as _qrmod
            from io import BytesIO
            qr = _qrmod.QRCode(
                box_size=5, border=2,
                error_correction=_qrmod.constants.ERROR_CORRECT_M,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            try:
                img.save(buf, **{"format": "PNG"})
            except TypeError:
                img.save(buf)
            px = QPixmap()
            px.loadFromData(buf.getvalue())
            self._qr_label.setPixmap(
                px.scaled(170, 170,
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )
        except ImportError:
            self._qr_label.setText("pip install\nqrcode[pil]")
            self._qr_label.setFont(QFont("Manrope", 8))
            self._qr_label.setStyleSheet(
                "color: #888; background: white; border-radius: 10px; padding: 4px;"
            )
        except Exception:
            self._qr_label.setText(url[:28])
            self._qr_label.setFont(QFont("Manrope", 7))
            self._qr_label.setStyleSheet(
                f"color: {C.PRI}; background: white; border-radius: 10px; padding: 4px;"
            )

    def _tick(self):
        remaining = max(0, int(self._expiry - time.time()))
        m, s = divmod(remaining, 60)
        self._timer_lbl.setText(f"Key expires in  {m:02d}:{s:02d}")
        if remaining == 0:
            self._do_close()

    def mark_connected(self) -> None:
        """Call from any thread when a phone successfully connects."""
        self._ctimer.stop()
        self._key_lbl.setText("CONNECTED")
        self._key_lbl.setStyleSheet(f"""
            color: {C.GREEN};
            background: rgba(34,197,94,0.08);
            border: 2px solid rgba(34,197,94,0.4);
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 4px;
        """)
        self._qr_label.setText("✓")
        self._qr_label.setFont(QFont("Manrope", 54, QFont.Weight.Bold))
        self._qr_label.setStyleSheet(
            "color: #00ff88; background: #001a0d; border-radius: 10px;"
        )
        self._timer_lbl.setText("Phone connected — Aethelark ready")
        self._timer_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")

    def _refresh_key(self):
        if self._on_new_key:
            result = self._on_new_key()
            if result:
                url    = result[0]
                key    = result[1]
                auto   = result[2] if len(result) >= 3 else ""
                manual = result[3] if len(result) >= 4 else url
                self._manual_url     = manual or url
                self._url_lbl.setText(self._manual_url)
                self._key_lbl.setText(key)
                self._auto_login_url = auto
                self._update_qr(auto or url)
                self._expiry = time.time() + 600
                self._key_lbl.setStyleSheet(f"""
                    color: {C.ACC};
                    background: {C.PANEL2};
                    border: 1px solid {C.BORDER_B};
                    border-radius: 8px;
                    padding: 6px 4px;
                    letter-spacing: 10px;
                """)
                self._timer_lbl.setStyleSheet(
                    f"color: {C.TEXT_MED}; background: transparent;"
                )
                self._ctimer.start(1000)
                self._tick()

    def _do_close(self):
        self._ctimer.stop()
        self.hide()
        self.closed.emit()



# ── DYNAMIC ISLAND OVERLAYS ──────────────────────────────────────────────────
class SizeAdjustingStackedWidget(QStackedWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.currentChanged.connect(self.on_current_changed)

    def on_current_changed(self, index):
        self.updateGeometry()
        p = self.parentWidget()
        if p:
            p.updateGeometry()
            if p.layout():
                p.layout().invalidate()
            win = p.window()
            if win:
                win.updateGeometry()
                if win.layout():
                    win.layout().invalidate()

    def sizeHint(self):
        cur = self.currentWidget()
        if cur:
            return cur.sizeHint()
        return super().sizeHint()

    def minimumSizeHint(self):
        cur = self.currentWidget()
        if cur:
            return cur.minimumSizeHint()
        return super().minimumSizeHint()


class PillWidget(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PillWidget")
        
        self.svg_path = str(Path(BASE_DIR) / "assets/images/AE_dynamic_island_cutout.svg")
        downloads_svg = "/home/shennyonthebeat/Downloads/AE_dynamic_island_cutout.svg"
        if not os.path.exists(self.svg_path) and os.path.exists(downloads_svg):
            os.makedirs(os.path.dirname(self.svg_path), exist_ok=True)
            import shutil
            try:
                shutil.copy(downloads_svg, self.svg_path)
            except Exception:
                pass
                
        from PyQt6.QtSvg import QSvgRenderer
        self.renderer = None
        if os.path.exists(self.svg_path):
            self.renderer = QSvgRenderer(self.svg_path)
        elif os.path.exists(downloads_svg):
            self.renderer = QSvgRenderer(downloads_svg)
            
        # Load and decode base64 logo image from the SVG cutout
        self.logo_image = None
        if os.path.exists(self.svg_path):
            try:
                import re
                import base64
                content = Path(self.svg_path).read_text(encoding="utf-8")
                match = re.search(r'href="data:image/png;base64,([^"]+)"', content)
                if match:
                    b64_data = match.group(1)
                    img_bytes = base64.b64decode(b64_data)
                    from PyQt6.QtGui import QImage
                    self.logo_image = QImage.fromData(img_bytes)
            except Exception as e:
                print(f"Warning: Failed to parse SVG base64 logo: {e}")
            
        self.setStyleSheet("background: transparent; border: none;")
        
        # Color & Pulse variables
        self.target_color = QColor(C.GREEN)
        self.current_color = QColor(C.GREEN)
        self.pulse_alpha = 110
        self.pulse_grow = True
        
        # Parallax offset — updated by mouseMoveEvent on the parent window
        self._parallax_dx: float = 0.0
        self._pill_base_pos = None   # set on first show
        
        # Shadow image cache
        self._cached_shadow = None
        self._shadow_rect_key = None

        # Logo layer caches: the silver base is blurred ONCE, the state glow
        # once per color bucket — replaces a full PIL Gaussian blur per frame.
        self._logo_base_img = None          # static blurred silver letters
        self._logo_glow_cache = {}          # (r//8,g//8,b//8) -> blurred glow
        self._capsule_path_cache = None     # (rect key, QPainterPath)

        # Audio-reactive breathing: envelope pushed from the TTS stream
        self._audio_level = 0.0

        # ── Dynamic Island state content (matches the design mockup) ──────────
        self.state = "STANDBY"                 # drives the live indicator
        self._swarm = None                     # {working,needs_you,total} in HARDCORE
        self._wave_phase = 0.0                 # animates the waveform / dots
        self._pill_eagle_src = None            # eagle emblem for the pill mark
        self._pill_eagle_cache = None          # (height, tinted QPixmap)
        _ep = Path(BASE_DIR) / "assets" / "images" / "eagle_white.png"
        if _ep.exists():
            _img = QImage(str(_ep))
            if not _img.isNull():
                self._pill_eagle_src = _img

        # Pre-generate surface micro-texture noise (cached, never regenerated)
        import random as _rnd
        _noise_img = QImage(96, 24, QImage.Format.Format_ARGB32)
        for _nx in range(96):
            for _ny in range(24):
                _v = _rnd.randint(0, 255)
                _a = _rnd.randint(2, 9)
                _noise_img.setPixelColor(_nx, _ny, QColor(_v, _v, _v, _a))
        self._noise_px = QPixmap.fromImage(_noise_img)
        
        # Continuous breathe timer
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_pulse)
        self.timer.start(30)
        
    def sizeHint(self):
        return QSize(240, 84)

    def minimumSizeHint(self):
        return QSize(240, 84)

    def set_state(self, state: str):
        state = state.upper()
        self.state = state
        if state in ("STANDBY", "LISTENING", "THINKING"):
            self.target_color = QColor(C.GREEN)
        elif state == "WORKING":
            self.target_color = QColor("#EAB308")
        elif state == "SPEAKING":
            self.target_color = QColor("#3B82F6")
        self.update()

    def set_swarm_status(self, working: int = 0, needs_you: int = 0, total: int = 0):
        """Ambient swarm readout for the collapsed pill in HARDCORE — show it
        even from across the room. Pass total=0 to clear back to normal states."""
        self._swarm = {"working": working, "needs_you": needs_you, "total": total} if total else None
        self.update()

    def set_audio_level(self, level: float):
        """Voice envelope (0..1) from the TTS stream — drives glow breathing."""
        self._audio_level = max(self._audio_level, min(max(level, 0.0), 1.0))

    def _capsule_path(self, rect: QRectF):
        """Cached vector capsule path (true arc/Bezier outline) for clipping."""
        from PyQt6.QtGui import QPainterPath
        key = (rect.x(), rect.y(), rect.width(), rect.height())
        if self._capsule_path_cache is None or self._capsule_path_cache[0] != key:
            p = QPainterPath()
            p.addRoundedRect(rect, rect.height() / 2, rect.height() / 2)
            self._capsule_path_cache = (key, p)
        return self._capsule_path_cache[1]

    def _logo_layers(self, pulse_col: QColor):
        """(blurred silver base, blurred glow mask for pulse_col).

        Visually equivalent to the old per-frame tint+blur composite, but
        the expensive PIL Gaussian blur now runs once per layer instead of
        33 times per second: pulse intensity is applied at draw time via
        painter opacity (radial alpha scales linearly, so the result is
        identical).
        """
        from PyQt6.QtGui import QImage
        img_w = self.logo_image.width()
        img_h = self.logo_image.height()

        if self._logo_base_img is None:
            base = QImage(img_w, img_h, QImage.Format.Format_ARGB32_Premultiplied)
            base.fill(Qt.GlobalColor.transparent)
            tp = QPainter(base)
            tp.setRenderHint(QPainter.RenderHint.Antialiasing)
            tp.drawImage(0, 0, self.logo_image)
            tp.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            logo_grad = QLinearGradient(0, 0, 0, img_h)
            logo_grad.setColorAt(0.0, QColor(229, 229, 234, 225))
            logo_grad.setColorAt(0.5, QColor(200, 200, 208, 195))
            logo_grad.setColorAt(1.0, QColor(176, 176, 180, 210))
            tp.setBrush(QBrush(logo_grad))
            tp.setPen(Qt.PenStyle.NoPen)
            tp.drawRect(0, 0, img_w, img_h)
            tp.end()
            self._logo_base_img = _make_blurred_logo_image(base, blur_radius=1.1)

        ckey = (pulse_col.red() // 8, pulse_col.green() // 8, pulse_col.blue() // 8)
        glow = self._logo_glow_cache.get(ckey)
        if glow is None:
            g = QImage(img_w, img_h, QImage.Format.Format_ARGB32_Premultiplied)
            g.fill(Qt.GlobalColor.transparent)
            tp = QPainter(g)
            tp.setRenderHint(QPainter.RenderHint.Antialiasing)
            tp.drawImage(0, 0, self.logo_image)
            tp.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            state_glow = QRadialGradient(img_w / 2, img_h / 2, img_w * 0.4)
            state_glow.setColorAt(0.0, QColor(pulse_col.red(), pulse_col.green(), pulse_col.blue(), 255))
            state_glow.setColorAt(1.0, QColor(pulse_col.red(), pulse_col.green(), pulse_col.blue(), 0))
            tp.setBrush(QBrush(state_glow))
            tp.setPen(Qt.PenStyle.NoPen)
            tp.drawRect(0, 0, img_w, img_h)
            tp.end()
            glow = _make_blurred_logo_image(g, blur_radius=1.1)
            if len(self._logo_glow_cache) >= 48:
                self._logo_glow_cache.pop(next(iter(self._logo_glow_cache)))
            self._logo_glow_cache[ckey] = glow
        return self._logo_base_img, glow

    def update_pulse(self):
        # Advance the live-indicator animation (waveform / dots)
        self._wave_phase += 0.28

        # 0. Audio envelope decay (voice-reactive breathing while speaking)
        if self._audio_level > 0.004:
            self._audio_level *= 0.82
        else:
            self._audio_level = 0.0

        # 1. Liquid color melting interpolation
        r = self.current_color.red() + (self.target_color.red() - self.current_color.red()) * 0.08
        g = self.current_color.green() + (self.target_color.green() - self.current_color.green()) * 0.08
        b = self.current_color.blue() + (self.target_color.blue() - self.current_color.blue()) * 0.08
        self.current_color = QColor(int(r), int(g), int(b))
        
        # 2. Dimmer breathing pulsation (alpha bounds: 80 to 140)
        step = 4 if self.target_color.name() in ("#EAB308", "#3B82F6") else 2
        if self.pulse_grow:
            self.pulse_alpha += step
            if self.pulse_alpha >= 140:
                self.pulse_alpha = 140
                self.pulse_grow = False
        else:
            self.pulse_alpha -= step
            if self.pulse_alpha <= 80:
                self.pulse_alpha = 80
                self.pulse_grow = True
        self.update()

    def mouseMoveEvent(self, event):
        """Track cursor for live parallax — shifts screen-light bleed with viewing angle."""
        cx = self.width() / 2
        raw_dx = (event.position().x() - cx) / max(cx, 1)  # -1.0 … +1.0
        # Smooth toward target (exponential decay)
        self._parallax_dx += (raw_dx - self._parallax_dx) * 0.25
        self.update()
        super().mouseMoveEvent(event)

    def enterEvent(self, event):
        self.setMouseTracking(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        # Gently return parallax to center
        self._parallax_dx *= 0.3
        self.setMouseTracking(False)
        self.update()
        super().leaveEvent(event)

    def _pill_eagle_pixmap(self, height: int):
        """Silver-tinted eagle emblem for the pill mark, cached per height."""
        if self._pill_eagle_src is None:
            return None
        if self._pill_eagle_cache and self._pill_eagle_cache[0] == height:
            return self._pill_eagle_cache[1]
        src = self._pill_eagle_src
        w = max(1, int(src.width() * height / src.height()))
        scaled = src.scaled(w, height, Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
        tinted = QImage(scaled.size(), QImage.Format.Format_ARGB32_Premultiplied)
        tinted.fill(Qt.GlobalColor.transparent)
        tp = QPainter(tinted)
        tp.setRenderHint(QPainter.RenderHint.Antialiasing)
        tp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        tp.drawImage(0, 0, scaled)
        tp.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        g = QLinearGradient(0, 0, 0, scaled.height())
        g.setColorAt(0.0, QColor(236, 236, 242))
        g.setColorAt(1.0, QColor(150, 150, 158))
        tp.fillRect(tinted.rect(), QBrush(g))
        tp.end()
        px = QPixmap.fromImage(tinted)
        self._pill_eagle_cache = (height, px)
        return px

    def _pill_mode(self) -> str:
        if self._swarm:
            return "swarm"
        s = (self.state or "").upper()
        if s == "SPEAKING":
            return "speak"
        if s == "LISTENING":
            return "listen"
        if s in ("THINKING", "PROCESSING", "WORKING"):
            return "think"
        return "idle"

    def _draw_pill_content(self, p, rect, cx, cy, pulse_col):
        """Renders the collapsed Dynamic Island exactly like the design mockup:
        eagle crest (left) · live state indicator (centre) · clock (right)."""
        H = rect.height()
        mode = self._pill_mode()

        # ── Eagle crest, left ────────────────────────────────────────────────
        eagle_h = H * 0.50
        epx = self._pill_eagle_pixmap(int(eagle_h))
        ex = rect.x() + rect.width() * 0.085
        ew = 0.0
        if epx is not None:
            ew = epx.width() * eagle_h / epx.height()
            p.setOpacity(0.96)
            p.drawPixmap(int(ex), int(cy - eagle_h / 2),
                         epx.scaled(int(ew), int(eagle_h),
                                    Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation))
            p.setOpacity(1.0)

        # ── Clock, right (Doto) ──────────────────────────────────────────────
        tf = QFont("Doto", max(7, int(H * 0.24)), QFont.Weight.Bold)
        tf.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 112)
        p.setFont(tf)
        tstr = time.strftime("%H:%M")
        tw = p.fontMetrics().horizontalAdvance(tstr) + 4
        time_x1 = rect.right() - rect.width() * 0.075
        p.setPen(QPen(qcol(C.PRI_DIM, 205)))
        p.drawText(QRectF(time_x1 - tw, cy - H * 0.30, tw, H * 0.60),
                   int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), tstr)

        # ── Centre: the live indicator ───────────────────────────────────────
        c_x0 = ex + ew + rect.width() * 0.055
        c_x1 = time_x1 - tw - rect.width() * 0.045
        cregion = QRectF(c_x0, rect.y(), max(4.0, c_x1 - c_x0), H)
        if mode == "swarm":
            self._draw_pill_swarm(p, cregion, cy)
        elif mode == "think":
            self._draw_pill_dots(p, cregion, cy)
        elif mode in ("listen", "speak"):
            green = mode == "listen"
            self._draw_pill_wave(p, cregion, cy, green)
        else:
            self._draw_pill_idleline(p, cregion, cy)

    def _draw_pill_wave(self, p, region, cy, green: bool):
        n = 18
        gap = region.width() / n
        bw = max(1.4, gap * 0.5)
        top = QColor(140, 255, 190) if green else QColor(236, 236, 242)
        bot = QColor(28, 120, 78) if green else QColor(96, 96, 104)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(n):
            env = 0.35 + 0.65 * abs(math.sin(self._wave_phase * 0.5 + i * 0.7))
            env *= (0.5 + 0.9 * self._audio_level) if not green else 1.0
            h = max(1.5, region.height() * 0.62 * env)
            grad = QLinearGradient(0, cy - h / 2, 0, cy + h / 2)
            grad.setColorAt(0.0, top)
            grad.setColorAt(1.0, bot)
            p.setBrush(QBrush(grad))
            x = region.x() + i * gap + (gap - bw) / 2
            p.drawRoundedRect(QRectF(x, cy - h / 2, bw, h), bw / 2, bw / 2)

    def _draw_pill_dots(self, p, region, cy):
        p.setPen(Qt.PenStyle.NoPen)
        r = min(3.2, region.height() * 0.09)
        spacing = r * 3.4
        total = spacing * 2
        x0 = region.x() + (region.width() - total) / 2
        for i in range(3):
            a = 0.4 + 0.6 * abs(math.sin(self._wave_phase * 0.4 - i * 0.9))
            p.setBrush(QBrush(qcol(C.PRI_DIM, int(a * 235))))
            p.drawEllipse(QPointF(x0 + i * spacing, cy), r, r)

    def _draw_pill_idleline(self, p, region, cy):
        p.setPen(Qt.PenStyle.NoPen)
        w = region.width() * 0.7
        x0 = region.x() + (region.width() - w) / 2
        grad = QLinearGradient(x0, 0, x0 + w, 0)
        grad.setColorAt(0.0, qcol(C.PRI_DIM, 0))
        grad.setColorAt(0.5, qcol(C.PRI_DIM, 90))
        grad.setColorAt(1.0, qcol(C.PRI_DIM, 0))
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(QRectF(x0, cy - 1.0, w, 2.0), 1.0, 1.0)

    def _draw_pill_swarm(self, p, region, cy):
        """Ambient swarm readout — glanceable from across the room. Silver dots =
        working, amber dot = an agent that needs you; hairline = overall progress.
        No text: the colours carry it, which is the point of a collapsed pill."""
        sw = self._swarm or {}
        working = int(sw.get("working", 0))
        needs = int(sw.get("needs_you", 0))
        total = max(1, int(sw.get("total", working + needs)))
        p.setPen(Qt.PenStyle.NoPen)

        # dot row, centred just above the midline
        r = 2.9
        gap = r * 3.2
        dots = [qcol(C.PRI_DIM, 235)] * working + [QColor("#ffb060")] * needs
        dots = dots[:total] or [qcol(C.PRI_DIM, 120)]
        row_w = gap * (len(dots) - 1)
        x0 = region.x() + (region.width() - row_w) / 2
        dy = cy - region.height() * 0.15
        for i, col in enumerate(dots):
            a = col
            if col.name() == "#ffb060":   # pulse the "needs you" dot
                a = QColor(col)
                a.setAlpha(int(160 + 95 * abs(math.sin(self._wave_phase * 0.4))))
            p.setBrush(QBrush(a))
            p.drawEllipse(QPointF(x0 + i * gap, dy), r, r)

        # progress hairline, centred below
        hy = cy + region.height() * 0.20
        hw = region.width() * 0.86
        hx = region.x() + (region.width() - hw) / 2
        p.setBrush(QBrush(qcol(C.PRI_GHO, 90)))
        p.drawRoundedRect(QRectF(hx, hy, hw, 2.0), 1.0, 1.0)
        frac = working / total if total else 0.0
        grad = QLinearGradient(hx, 0, hx + hw, 0)
        grad.setColorAt(0.0, QColor(106, 106, 114))
        grad.setColorAt(1.0, QColor(236, 236, 242))
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(QRectF(hx, hy, hw * max(0.12, frac), 2.0), 1.0, 1.0)


    def paintEvent(self, event):
        painter = QPainter(self)
        # Enable high-end antialiasing and bilinear pixmap transforming
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        
        r = QRectF(self.rect())
        
        # Calculate aspect-ratio-corrected centered rendering rectangle
        # Cutout SVG is exactly 1000x280 (3.5714 aspect ratio)
        svg_ratio = 1000.0 / 280.0
        widget_w = r.width()
        widget_h = r.height()
        
        # Center the pill capsule (192 x 53.76) inside the 240 x 84 canvas to provide 22.24px shadow decay space below
        render_w = 192.0
        render_h = 53.76
        render_x = (widget_w - render_w) / 2.0
        render_y = 8.0
        render_rect = QRectF(render_x, render_y, render_w, render_h)
        
        render_cx = render_x + render_w / 2.0
        render_cy = render_y + render_h / 2.0
        
        # 1. TRUE GAUSSIAN BLURRED DROP SHADOW (Silky smooth 1:1 capsule curvature, zero flat lines/boxiness)
        shadow_key = (int(render_x), int(render_y), int(render_w), int(render_h), int(widget_w), int(widget_h))
        if self._cached_shadow is None or self._shadow_rect_key != shadow_key:
            self._cached_shadow = _make_gaussian_shadow_image(int(widget_w), int(widget_h), render_rect, offset_y=2.0, blur_radius=4.5, alpha=135)
            self._shadow_rect_key = shadow_key
            
        if self._cached_shadow and not self._cached_shadow.isNull():
            painter.drawImage(0, 0, self._cached_shadow)

        # 2. AMBIENT SCREEN-LIGHT BLEED — subtle, natural glow beneath the pill
        pulse_col = self.current_color
        bleed_alpha = int((self.pulse_alpha / 140.0) * 14) + 4 + int(self._audio_level * 12)
        screen_bleed = QRadialGradient(
            render_cx + self._parallax_dx * 0.4,
            render_rect.bottom() - 4.0,
            render_rect.width() * 0.35
        )
        screen_bleed.setColorAt(0.0, QColor(pulse_col.red(), pulse_col.green(), pulse_col.blue(), bleed_alpha))
        screen_bleed.setColorAt(0.5, QColor(pulse_col.red(), pulse_col.green(), pulse_col.blue(), int(bleed_alpha * 0.25)))
        screen_bleed.setColorAt(1.0, QColor(pulse_col.red(), pulse_col.green(), pulse_col.blue(), 0))
        bleed_ellipse = QRectF(
            render_cx - render_rect.width() * 0.35,
            render_rect.bottom() - 12.0,
            render_rect.width() * 0.7,
            24.0
        )
        painter.setBrush(QBrush(screen_bleed))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(bleed_ellipse)
            
        # 3. Soft pulsating outer border aura
        glow_opacity = int((self.pulse_alpha / 140.0) * 16) + 4
        outer_color = QColor(self.current_color)
        outer_color.setAlpha(glow_opacity)
        
        glow_pen = QPen(outer_color, 1.0)
        painter.setPen(glow_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        
        r_glow = render_rect.adjusted(0.5, 0.5, -0.5, -0.5)
        radius = r_glow.height() / 2.0
        painter.drawRoundedRect(r_glow, radius, radius)

        # 3B. Draw solid premium 3D piano black polished obsidian base
        bg_grad = QLinearGradient(render_rect.topLeft(), render_rect.bottomLeft())
        bg_grad.setColorAt(0.0, QColor("#141419"))
        bg_grad.setColorAt(0.2, QColor("#08080A"))
        bg_grad.setColorAt(1.0, QColor("#000000"))
        
        painter.setBrush(QBrush(bg_grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(render_rect, render_rect.height() / 2, render_rect.height() / 2)
        
        # Draw inner shadow vignette directly on the solid base (for portal depth)
        painter.save()
        painter.setClipPath(self._capsule_path(render_rect))
        for i in range(6):
            inner_color = QColor(0, 0, 0, int(195 * (1 - i / 6)))
            inner_pen = QPen(inner_color, 1.0 + i * 0.9)
            painter.setPen(inner_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            adj = i * 0.5
            painter.drawRoundedRect(render_rect.adjusted(adj, adj, -adj, -adj), render_rect.height() / 2 - adj, render_rect.height() / 2 - adj)
        painter.restore()
        
        # 3C. Draw soft ambient glow spill on the capsule surface
        pulse_col = self.current_color
        ambient_glow = QRadialGradient(render_cx, render_cy, render_rect.width() * 0.35)
        ambient_glow.setColorAt(0.0, QColor(pulse_col.red(), pulse_col.green(), pulse_col.blue(), int(self.pulse_alpha * 0.22)))
        ambient_glow.setColorAt(0.6, QColor(pulse_col.red(), pulse_col.green(), pulse_col.blue(), int(self.pulse_alpha * 0.05)))
        ambient_glow.setColorAt(1.0, QColor(pulse_col.red(), pulse_col.green(), pulse_col.blue(), 0))
        
        painter.setBrush(QBrush(ambient_glow))
        painter.setPen(Qt.PenStyle.NoPen)
        # Radial ellipse matching the logo proportions
        painter.drawEllipse(QRectF(render_cx - render_rect.width() * 0.35, render_cy - render_rect.height() * 0.45,
                                   render_rect.width() * 0.7, render_rect.height() * 0.9))
        
        # 4. State-aware Dynamic Island content: eagle crest + live indicator + time
        painter.save()
        painter.setClipPath(self._capsule_path(render_rect))
        self._draw_pill_content(painter, render_rect, render_cx, render_cy, pulse_col)
        painter.restore()

        # 4A. Specular Glass Glossy Curved Reflection Overlay
        painter.save()
        painter.setClipPath(self._capsule_path(render_rect))
        
        highlight_rect = QRectF(render_rect.x(), render_rect.y(), render_rect.width(), render_rect.height() * 0.46)
        highlight_grad = QLinearGradient(highlight_rect.topLeft(), highlight_rect.bottomLeft())
        highlight_grad.setColorAt(0.0, QColor(255, 255, 255, 38))
        highlight_grad.setColorAt(0.9, QColor(255, 255, 255, 4))
        highlight_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        
        painter.setBrush(QBrush(highlight_grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(highlight_rect)
        
        painter.restore()

        # 4B. TOP-EDGE SPECULAR RIM LIGHT — bright crescent on the very top edge
        # This is the single biggest trick to make the pill read as a 3D object.
        # Light source is above-left, so the rim is brightest center-left.
        painter.save()
        painter.setClipPath(self._capsule_path(render_rect))

        rim_rect = QRectF(
            render_rect.x() + render_rect.width() * 0.12,
            render_rect.y() + 0.8,
            render_rect.width() * 0.72,
            render_rect.height() * 0.16,
        )
        rim_grad = QLinearGradient(rim_rect.topLeft(), rim_rect.bottomLeft())
        rim_grad.setColorAt(0.0, QColor(255, 255, 255, 110))   # bright peak
        rim_grad.setColorAt(0.5, QColor(255, 255, 255, 32))
        rim_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(QBrush(rim_grad))
        painter.setPen(Qt.PenStyle.NoPen)
        # Ellipse so it falls off at the sides naturally
        painter.drawEllipse(rim_rect)
        painter.restore()

        # 4C. SURFACE MICRO-TEXTURE — cached noise overlay for tactile material feel
        if self._noise_px and not self._noise_px.isNull():
            painter.save()
            painter.setClipPath(self._capsule_path(render_rect))
            painter.setOpacity(0.12)
            painter.drawTiledPixmap(render_rect.toRect(), self._noise_px)
            painter.setOpacity(1.0)
            painter.restore()
            
        # 5. Bevel light-bending silver edge highlight ring centered on render_rect
        border_grad = QLinearGradient(render_rect.topLeft(), render_rect.bottomRight())
        border_grad.setColorAt(0.0, QColor(255, 255, 255, 75))
        border_grad.setColorAt(0.4, QColor(255, 255, 255, 20))
        border_grad.setColorAt(0.8, QColor(255, 255, 255, 5))
        border_grad.setColorAt(1.0, QColor(255, 255, 255, 35))
        
        painter.setPen(QPen(border_grad, 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        r_highlight = render_rect.adjusted(0.6, 0.6, -0.6, -0.6)
        radius_highlight = r_highlight.height() / 2 - 0.6
        painter.drawRoundedRect(r_highlight, radius_highlight, radius_highlight)


class SwarmLane(QFrame):
    """One agent's live lane in the HARDCORE swarm view — status stripe, glyph,
    name / branch, current thought, and diffstat / file / elapsed."""

    _STATUS = {
        "work":   ("#ECECF2", "WORKING"),
        "review": ("#4ee08a", "IN REVIEW"),
        "block":  ("#ffb060", "NEEDS YOU"),
        "idle":   ("#5a5a62", "STANDBY"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SwarmLane")
        self.setStyleSheet(
            "QFrame#SwarmLane {"
            " background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            " stop:0 rgba(30,30,36,0.55), stop:1 rgba(12,12,15,0.35));"
            " border-radius: 12px; }"
        )
        self._stripe = QColor("#5a5a62")

        v = QVBoxLayout(self)
        v.setContentsMargins(17, 11, 14, 11)
        v.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(10)
        self._glyph = QLabel("•")
        self._glyph.setFixedSize(26, 26)
        self._glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._glyph.setFont(QFont("Doto", 11, QFont.Weight.Bold))
        top.addWidget(self._glyph)
        namecol = QVBoxLayout()
        namecol.setSpacing(1)
        self._name = QLabel("—")
        self._name.setFont(QFont("Manrope", 12, QFont.Weight.Bold))
        self._name.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        self._branch = QLabel("")
        self._branch.setFont(QFont("Manrope", 8))
        self._branch.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        namecol.addWidget(self._name)
        namecol.addWidget(self._branch)
        top.addLayout(namecol)
        top.addStretch()
        self._badge = QLabel("")
        self._badge.setFont(QFont("Manrope", 7, QFont.Weight.Bold))
        top.addWidget(self._badge)
        v.addLayout(top)

        self._thought = QLabel("")
        self._thought.setWordWrap(True)
        self._thought.setFont(QFont("Manrope", 10))
        self._thought.setStyleSheet(
            "color: #c3c3cc; background: rgba(0,0,0,0.30);"
            " border-radius: 8px; padding: 8px 10px;")
        v.addWidget(self._thought)

        self._meta = QLabel("")
        self._meta.setFont(QFont("Manrope", 8))
        self._meta.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        v.addWidget(self._meta)

    def set_data(self, glyph="•", name="—", branch="", status="idle",
                 thought="", adds=0, dels=0, file="", elapsed=""):
        stripe, badge_txt = self._STATUS.get(status, self._STATUS["idle"])
        self._stripe = QColor(stripe)
        self._glyph.setText(glyph)
        self._glyph.setStyleSheet(
            f"color: #0a0a0d; background: {stripe}; border-radius: 7px;")
        self._name.setText(name)
        self._branch.setText(branch)
        self._badge.setText(badge_txt)
        self._badge.setStyleSheet(f"color: {stripe}; background: transparent;")
        self._thought.setText(thought)
        meta = f"+{adds}   −{dels}"
        if file:
            meta += f"      {file}"
        if elapsed:
            meta += f"      ⏱ {elapsed}"
        self._meta.setText(meta)
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(self._stripe))
        p.drawRoundedRect(QRectF(0, 6, 3.0, self.height() - 12), 1.5, 1.5)


class MainWindow(QMainWindow):
    _log_sig        = pyqtSignal(str)
    _state_sig      = pyqtSignal(str)
    _content_sig    = pyqtSignal(str, str)   # (title, text) — thread-safe content display
    _reconfig_sig   = pyqtSignal()           # trigger setup overlay from any thread
    _camera_sig     = pyqtSignal(bytes)      # show camera frame preview (small overlay)
    _cam_stream_sig = pyqtSignal(bool)       # True=start live stream, False=stop
    _cam_frame_sig  = pyqtSignal(bytes)      # live camera frame → HUD area
    _clipboard_sig  = pyqtSignal(str)        # clipboard text changed (thread-safe)
    _audio_sig      = pyqtSignal(float)      # TTS output envelope → pill breathing

    def __init__(self, face_path: str):
        super().__init__()
        self._face_path = face_path

        # Load customization from config
        _cfg = _read_full_config()
        self._assistant_name: str = (_cfg.get("assistant_name") or "Aethelark").strip()
        _display = self._assistant_name.upper()

        # Kayıtlı UI rengini panel/stylesheet'ler kurulmadan ÖNCE uygula
        _ui_color = (_cfg.get("ui_color") or "").strip()
        if _ui_color and _ui_color.lower() != DEFAULT_UI_COLOR:
            apply_ui_accent(_ui_color)

        self.setWindowTitle(f"{_display} — AETHELARK")
        # Managed dynamically based on mode
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command   = None
        self.on_remote_clicked = None   # callable: () -> (url, key) | None
        self.on_interrupt      = None   # callable: () -> None — stop Aethelark mid-speech
        self._muted            = False
        self._current_file: str | None = None
        self._remote_overlay: RemoteKeyOverlay | None = None
        self._customize_overlay: CustomizeOverlay | None = None

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.central_widget = QWidget()
        self.central_widget.setObjectName("CentralWidget")
        self.central_widget.setStyleSheet("#CentralWidget { background: transparent; }")
        self.setCentralWidget(self.central_widget)

        self._main_layout = QVBoxLayout(self.central_widget)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)

        self._stacked = SizeAdjustingStackedWidget(self.central_widget)
        self._main_layout.addWidget(self._stacked)

        # 1. Build Dashboard Layout container
        self._dashboard_container = QWidget()
        self._dashboard_container.setObjectName("DashboardContainer")
        self._dashboard_container.setStyleSheet(f"#DashboardContainer {{ background: {C.BG}; border-radius: 12px; }}")
        dash_layout = QVBoxLayout(self._dashboard_container)
        dash_layout.setContentsMargins(12, 10, 12, 10)
        dash_layout.setSpacing(10)
        dash_layout.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 4, 0, 4)
        body.setSpacing(12)

        self._left_panel = self._build_left_panel()
        self._swarm_left = self._build_swarm_left_panel()
        self._left_stack = QStackedWidget()
        self._left_stack.addWidget(self._left_panel)   # 0 = CASUAL
        self._left_stack.addWidget(self._swarm_left)   # 1 = HARDCORE
        body.addWidget(self._left_stack, stretch=0)
        self.set_swarm_mission()                       # standby defaults

        # Center column: HUD + resizable content panel via QSplitter
        self.hud = HudCanvas(face_path, _display)
        self.hud.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._content_panel = self._build_content_panel()

        # Live camera container
        _cam_cont = QWidget()
        _cam_cont.setStyleSheet("background: #000308;")
        _cam_v = QVBoxLayout(_cam_cont)
        _cam_v.setContentsMargins(0, 0, 0, 0)
        _cam_v.setSpacing(0)
        _cam_hdr = QHBoxLayout()
        _cam_hdr.setContentsMargins(8, 5, 8, 5)
        _cam_title = QLabel("◈  CAMERA FEED")
        _cam_title.setFont(QFont("Manrope", 8, QFont.Weight.Bold))
        _cam_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _cam_hdr.addWidget(_cam_title)
        _cam_hdr.addStretch()
        _cam_x = QPushButton("✕  CLOSE")
        _cam_x.setFont(QFont("Manrope", 8, QFont.Weight.Bold))
        _cam_x.setCursor(Qt.CursorShape.PointingHandCursor)
        _cam_x.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: transparent;
                border: none; padding: 2px 6px;
            }}
            QPushButton:hover {{ color: {C.PRI}; }}
        """)
        _cam_x.clicked.connect(self.stop_camera_stream)
        _cam_hdr.addWidget(_cam_x)
        _cam_v.addLayout(_cam_hdr)
        self._cam_live_lbl = QLabel()
        self._cam_live_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cam_live_lbl.setStyleSheet("background: transparent;")
        self._cam_live_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        _cam_v.addWidget(self._cam_live_lbl, stretch=1)

        # Stack: 0 = animated HUD, 1 = live camera
        self._hud_cam_stack = QStackedWidget()
        self._hud_cam_stack.addWidget(self.hud)
        self._hud_cam_stack.addWidget(_cam_cont)

        self._center_split = QSplitter(Qt.Orientation.Vertical)
        self._center_split.setStyleSheet(f"""
            QSplitter::handle {{
                background: {C.BORDER};
                height: 4px;
            }}
            QSplitter::handle:hover {{
                background: {C.PRI_DIM};
            }}
        """)
        self._center_split.addWidget(self._hud_cam_stack)
        self._center_split.addWidget(self._content_panel)
        self._center_split.setStretchFactor(0, 3)
        self._center_split.setStretchFactor(1, 1)
        self._center_split.setCollapsible(0, False)

        # Center column = the core/content splitter + starter chips beneath it
        _center_col = QWidget()
        _cc = QVBoxLayout(_center_col)
        _cc.setContentsMargins(0, 0, 0, 0)
        _cc.setSpacing(9)
        _cc.addWidget(self._center_split, stretch=1)
        _cc.addWidget(self._build_starter_chips())

        # Center stage swaps between CASUAL (core) and HARDCORE (swarm lanes)
        self._swarm_stage = self._build_swarm_stage()
        self._center_stack = QStackedWidget()
        self._center_stack.addWidget(_center_col)        # index 0 = CASUAL
        self._center_stack.addWidget(self._swarm_stage)  # index 1 = HARDCORE
        body.addWidget(self._center_stack, stretch=5)

        # Right rail swaps too: CASUAL activity/command ↔ HARDCORE timeline
        self._right_panel = self._build_right_panel()
        self._swarm_right = self._build_swarm_right_panel()
        self._right_stack = QStackedWidget()
        self._right_stack.addWidget(self._right_panel)   # 0 = CASUAL
        self._right_stack.addWidget(self._swarm_right)   # 1 = HARDCORE
        body.addWidget(self._right_stack, stretch=0)

        dash_layout.addLayout(body, stretch=1)
        dash_layout.addWidget(self._build_footer())
        self._stacked.addWidget(self._dashboard_container)

        # 2. Build Pill Layout container
        self._pill_widget = PillWidget(self.central_widget)
        self._stacked.addWidget(self._pill_widget)

        # Drag details & states
        self._drag_active = False
        self._drag_pos = QPointF(0, 0)
        self._ui_mode = "DASHBOARD"
        self._normal_geom = None
        self._anim = None

        # Quick-access drawer (floating overlay, built after central widget layout is done)
        self._quick_drawer = self._build_quick_drawer()
        self._update_autostart_btn(self._check_autostart())
        from memory.config_manager import get_brief_enabled as _gbe
        self._update_brief_btn(_gbe())

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        # Metrik güncelleme timer'ı
        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        self._log_sig.connect(self._log.append_log)
        self._state_sig.connect(self._apply_state)
        self._content_sig.connect(self._show_content)
        self._reconfig_sig.connect(self._show_setup)
        self._camera_sig.connect(self._show_camera_frame)
        self._cam_stream_sig.connect(self._on_cam_stream)
        self._cam_frame_sig.connect(self._on_cam_frame)
        self._clipboard_sig.connect(self._show_clipboard_panel)
        self._audio_sig.connect(self._pill_widget.set_audio_level)
        self._cam_stop = threading.Event()

        # Camera preview overlay (child of central widget, positioned in resizeEvent)
        self._cam_preview = _CameraPreview(self.centralWidget())

        # Clipboard panel (child of central widget, bottom-center)
        self._clipboard_panel = ClipboardPanel(self.centralWidget())
        self._clipboard_panel.action_requested.connect(self._on_clipboard_action)
        QApplication.clipboard().dataChanged.connect(self._on_clipboard_changed)

        self._overlay: SetupOverlay | None = None
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)
        sc_intr = QShortcut(QKeySequence("Escape"), self)
        sc_intr.activated.connect(self._do_interrupt)

    def _show_camera_frame(self, img_bytes: bytes):
        """Slot — display camera preview overlay (main thread)."""
        self._cam_preview.show_frame(img_bytes)
        cw = self.centralWidget()
        pw = _CameraPreview._W
        ph = self._cam_preview.height()
        self._cam_preview.setGeometry(
            cw.width() - _RIGHT_W - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )

    # --- Live camera stream in HUD area ------------------------------------
    def _on_cam_stream(self, start: bool) -> None:
        if start:
            self._hud_cam_stack.setCurrentIndex(1)
        else:
            self._hud_cam_stack.setCurrentIndex(0)
            self._cam_live_lbl.clear()

    def _on_cam_frame(self, data: bytes) -> None:
        px = QPixmap()
        px.loadFromData(data)
        if not px.isNull():
            w, h = self._cam_live_lbl.width(), self._cam_live_lbl.height()
            if w > 1 and h > 1:
                self._cam_live_lbl.setPixmap(
                    px.scaled(w, h,
                              Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
                )

    def start_camera_stream(self) -> None:
        self._cam_stop.clear()
        self._cam_stream_sig.emit(True)
        t = threading.Thread(target=self._cam_loop, daemon=True, name="cam-stream")
        t.start()

    def _cam_loop(self) -> None:
        try:
            import cv2
            # Reuse camera index detected by screen_processor (cached in api_keys.json)
            cam_idx = 0
            try:
                import json as _j
                cfg = _j.loads((CONFIG_DIR / "api_keys.json").read_text())
                cam_idx = int(cfg.get("camera_index", 0))
            except Exception:
                pass
            try:
                backend = cv2.CAP_DSHOW if _OS == "Windows" else cv2.CAP_ANY
            except AttributeError:
                backend = 0
            cap = cv2.VideoCapture(cam_idx, backend)
            if not cap.isOpened():
                cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                return
            # warm-up frames
            for _ in range(5):
                cap.read()
            while not self._cam_stop.wait(0.033) and cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                    self._cam_frame_sig.emit(buf.tobytes())
            cap.release()
        except Exception as e:
            print(f"[Camera] Stream error: {e}")
        finally:
            self._cam_stream_sig.emit(False)

    def stop_camera_stream(self) -> None:
        self._cam_stop.set()

    # ------------------------------------------------------------------
    # Icon generation — arc-reactor style, rendered with Pillow
    # ------------------------------------------------------------------
    @staticmethod
    def _build_aethelark_icon(out_path: Path) -> bool:
        """
        Render a JARVIS arc-reactor icon at 4× resolution and downsample
        for crisp results at all sizes. Saves a multi-res .ico to out_path.
        Returns True on success.
        """
        try:
            import math
            import PIL.Image
            import PIL.ImageDraw
            import PIL.ImageFilter
        except ImportError:
            return False

        CYAN   = (0, 212, 255)
        DIM    = (0, 100, 140)
        DARK   = (0, 6, 10)
        GLOW   = (0, 160, 200)
        WHITE  = (220, 240, 255)

        def _render(sz: int) -> PIL.Image.Image:
            S  = sz * 4                     # draw at 4× then downscale
            img = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            d   = PIL.ImageDraw.Draw(img)
            cx = cy = S // 2

            # ── filled background circle ──────────────────────────────────
            R = S // 2 - 2
            d.ellipse([cx-R, cy-R, cx+R, cy+R], fill=(*DARK, 255))

            # ── outer border ring ─────────────────────────────────────────
            lw = max(2, S // 40)
            d.ellipse([cx-R, cy-R, cx+R, cy+R],
                      outline=(*CYAN, 220), width=lw)

            # ── mid decorative ring ───────────────────────────────────────
            R2 = int(R * 0.72)
            d.ellipse([cx-R2, cy-R2, cx+R2, cy+R2],
                      outline=(*DIM, 180), width=max(1, lw // 2))

            # ── 6 radial spokes (hex bolt) ────────────────────────────────
            R_inner = int(R * 0.30)
            R_outer = int(R * 0.62)
            spoke_w = max(1, S // 80)
            for i in range(6):
                angle = math.radians(i * 60 - 30)
                x1 = cx + int(R_inner * math.cos(angle))
                y1 = cy + int(R_inner * math.sin(angle))
                x2 = cx + int(R_outer * math.cos(angle))
                y2 = cy + int(R_outer * math.sin(angle))
                d.line([x1, y1, x2, y2], fill=(*GLOW, 200), width=spoke_w)

            # ── 6 tick marks on outer ring ────────────────────────────────
            for i in range(6):
                angle = math.radians(i * 60)
                for dr in range(lw * 2):
                    rx = (R - lw - dr)
                    d.point(
                        [cx + int(rx * math.cos(angle)),
                         cy + int(rx * math.sin(angle))],
                        fill=(*WHITE, 220),
                    )

            # ── inner glowing ring ────────────────────────────────────────
            Ri = int(R * 0.26)
            d.ellipse([cx-Ri, cy-Ri, cx+Ri, cy+Ri],
                      outline=(*CYAN, 255), width=max(2, lw))

            # ── bright glow soft blur applied before core ─────────────────
            # (draw a slightly larger cyan circle on a separate layer)
            glow_layer = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            gd = PIL.ImageDraw.Draw(glow_layer)
            Rc = int(R * 0.13)
            gd.ellipse([cx-Rc*2, cy-Rc*2, cx+Rc*2, cy+Rc*2],
                       fill=(*CYAN, 110))
            glow_layer = glow_layer.filter(PIL.ImageFilter.GaussianBlur(S // 14))
            img = PIL.Image.alpha_composite(img, glow_layer)
            d   = PIL.ImageDraw.Draw(img)

            # ── core dot ──────────────────────────────────────────────────
            d.ellipse([cx-Rc, cy-Rc, cx+Rc, cy+Rc], fill=(*WHITE, 255))

            # ── downscale to target size ──────────────────────────────────
            return img.resize((sz, sz), PIL.Image.LANCZOS)

        try:
            sizes  = [256, 128, 64, 48, 32, 16]
            frames = [_render(s) for s in sizes]
            frames[0].save(
                out_path,
                format="ICO",
                append_images=frames[1:],
                sizes=[(s, s) for s in sizes],
            )
            return True
        except Exception as e:
            print(f"[Shortcut] ⚠️  Icon generation failed: {e}")
            return False

    @staticmethod
    def _get_desktop_dir() -> Path:
        """
        Resolve the user's REAL desktop directory instead of assuming
        ~/Desktop, which breaks when:
          • OneDrive "Known Folder Move" relocates the desktop
            (C:/Users/x/OneDrive/Desktop) — very common on Win 10/11;
          • the XDG desktop is localized on Linux (~/Masaüstü,
            ~/Schreibtisch, ~/Bureau, …).
        Falls back to ~/Desktop only as a last resort.
        """
        home = Path.home()
        _os = platform.system()

        if _os == "Windows":
            # ── 1) SHGetKnownFolderPath(FOLDERID_Desktop) — the canonical
            #       answer; follows OneDrive redirection. No dependencies. ──
            try:
                import ctypes
                from ctypes import wintypes

                class _GUID(ctypes.Structure):
                    _fields_ = [("Data1", wintypes.DWORD),
                                ("Data2", wintypes.WORD),
                                ("Data3", wintypes.WORD),
                                ("Data4", ctypes.c_ubyte * 8)]

                # FOLDERID_Desktop {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
                fid = _GUID(0xB4BFCC3A, 0xDB2C, 0x424C,
                            (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9,
                                                 0x9A, 0x87, 0xC6, 0x41))
                buf = ctypes.c_wchar_p()
                if ctypes.windll.shell32.SHGetKnownFolderPath(
                        ctypes.byref(fid), 0, None, ctypes.byref(buf)) == 0:
                    p = Path(buf.value)
                    ctypes.windll.ole32.CoTaskMemFree(buf)
                    if p.is_dir():
                        return p
            except Exception:
                pass

            # ── 2) Registry: User Shell Folders (may contain %VARS%) ──────
            try:
                import winreg
                with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion"
                        r"\Explorer\User Shell Folders") as key:
                    val, _t = winreg.QueryValueEx(key, "Desktop")
                p = Path(os.path.expandvars(val))
                if p.is_dir():
                    return p
            except Exception:
                pass

        elif _os == "Linux":
            # ── xdg-user-dir honours localized names (~/Masaüstü, …) ──────
            try:
                out = subprocess.run(["xdg-user-dir", "DESKTOP"],
                                     capture_output=True, text=True, timeout=5)
                p = Path(out.stdout.strip())
                if out.stdout.strip() and p != home and p.is_dir():
                    return p
            except Exception:
                pass
            try:
                cfg = home / ".config" / "user-dirs.dirs"
                for line in cfg.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("XDG_DESKTOP_DIR"):
                        val = line.split("=", 1)[1].strip().strip('"')
                        p = Path(val.replace("$HOME", str(home)))
                        if p != home and p.is_dir():
                            return p
            except Exception:
                pass

        # macOS: ~/Desktop is always the real path (localization is
        # display-only). Everything else lands here as a last resort.
        return home / "Desktop"

    @staticmethod
    def _create_lnk_windows(lnk: str, target: str, args: str,
                             work_dir: str, icon_loc: str) -> None:
        """
        Create a Windows .lnk shortcut WITHOUT launching PowerShell or cmd.
        Tries win32com (pywin32) first; falls back to wscript.exe + VBScript.
        wscript.exe is a GUI-mode host — it never opens a console window.
        Raises on failure so the caller can log a useful error.
        """
        # ── Option 1: pywin32 (pure Python COM, zero subprocess) ──────────
        com_err: Exception | None = None
        try:
            from win32com.client import Dispatch   # type: ignore
            sh = Dispatch("WScript.Shell")
            sc = sh.CreateShortCut(lnk)
            sc.TargetPath       = target
            sc.Arguments        = f'"{args}"'
            sc.WorkingDirectory = work_dir
            sc.Description      = "J.A.R.V.I.S AI Assistant"
            sc.IconLocation     = icon_loc
            sc.save()
            return
        except ImportError:
            pass
        except Exception as e:            # COM error — still try VBScript
            com_err = e

        # ── Option 2: wscript.exe + VBScript (always available on Windows,
        #    GUI-mode executable — never opens a console window) ────────────
        def q(s: str) -> str:              # escape for a VBScript string literal
            return s.replace('"', '""')

        vbs = "\n".join([
            'On Error Resume Next',
            'Set ws = CreateObject("WScript.Shell")',
            f'Set sc = ws.CreateShortcut("{q(lnk)}")',
            f'sc.TargetPath = "{q(target)}"',
            f'sc.Arguments = Chr(34) & "{q(args)}" & Chr(34)',
            f'sc.WorkingDirectory = "{q(work_dir)}"',
            'sc.Description = "J.A.R.V.I.S AI Assistant"',
            f'sc.IconLocation = "{q(icon_loc)}"',
            'sc.Save',
            'If Err.Number <> 0 Then WScript.Quit 1',
        ])
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".vbs")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(vbs)
            proc = subprocess.Popen(
                ["wscript.exe", "/nologo", tmp],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
            proc.wait(timeout=10)
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

        if not Path(lnk).exists():
            raise RuntimeError(
                f"could not create '{lnk}'"
                + (f" ({com_err})" if com_err else "")
            )

    def _create_desktop_shortcut(self):
        """
        Create a desktop shortcut on Windows / macOS / Linux.
        Never opens a terminal, console, or PowerShell window on any platform.
        """
        import stat as _stat
        script  = Path(__file__).resolve().parent / "main.py"
        python  = Path(sys.executable)
        desktop = self._get_desktop_dir()

        # Arc-reactor icon (.ico — also exported as .png for Linux/macOS)
        ico_path = Path(__file__).resolve().parent / "config" / "aethelark.ico"
        if not ico_path.exists():
            self._build_aethelark_icon(ico_path)

        try:
            _os = platform.system()
            desktop.mkdir(parents=True, exist_ok=True)

            # ── Windows ───────────────────────────────────────────────────────
            if _os == "Windows":
                pythonw  = python.parent / "pythonw.exe"
                target   = str(pythonw if pythonw.exists() else python)
                lnk      = str(desktop / "Aethelark.lnk")
                icon_loc = str(ico_path) if ico_path.exists() else f"{target},0"
                self._create_lnk_windows(lnk, target, str(script),
                                         str(script.parent), icon_loc)

            # ── macOS — proper .app bundle (no Terminal window) ───────────────
            elif _os == "Darwin":
                app     = desktop / "Aethelark.app"
                mac_dir = app / "Contents" / "MacOS"
                res_dir = app / "Contents" / "Resources"
                mac_dir.mkdir(parents=True, exist_ok=True)
                res_dir.mkdir(exist_ok=True)

                # Launcher executable (bash — runs as background process,
                # macOS does NOT open Terminal for executables inside .app bundles)
                launcher = mac_dir / "Aethelark"
                launcher.write_text(
                    "#!/usr/bin/env bash\n"
                    f'cd "{script.parent}"\n'
                    f'exec "{python}" "{script}"\n'
                )
                launcher.chmod(launcher.stat().st_mode
                               | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH)

                # Minimal Info.plist (required for .app recognition)
                (app / "Contents" / "Info.plist").write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    '<plist version="1.0"><dict>\n'
                    '  <key>CFBundleExecutable</key><string>Aethelark</string>\n'
                    '  <key>CFBundleIdentifier</key>'
                    '<string>com.aethelark.assistant</string>\n'
                    '  <key>CFBundleName</key><string>Aethelark</string>\n'
                    '  <key>CFBundlePackageType</key><string>APPL</string>\n'
                    '  <key>CFBundleVersion</key><string>1.0</string>\n'
                    '</dict></plist>\n'
                )

                # Optional: copy icon as .icns (skip silently if Pillow is missing)
                try:
                    import PIL.Image
                    icns = res_dir / "AppIcon.icns"
                    PIL.Image.open(ico_path).save(icns, format="ICNS")
                    # Inject icon reference into plist
                    plist = app / "Contents" / "Info.plist"
                    txt = plist.read_text()
                    plist.write_text(
                        txt.replace(
                            '</dict></plist>',
                            '  <key>CFBundleIconFile</key>'
                            '<string>AppIcon</string>\n</dict></plist>\n',
                        )
                    )
                except Exception:
                    pass  # icon is optional

            # ── Linux — .desktop file (Terminal=false, no console) ────────────
            else:
                # Export .ico → .png for better desktop integration
                png_path = ico_path.with_suffix(".png")
                if not png_path.exists() and ico_path.exists():
                    try:
                        import PIL.Image
                        PIL.Image.open(ico_path).resize(
                            (256, 256), PIL.Image.LANCZOS
                        ).save(png_path, format="PNG")
                    except Exception:
                        png_path = ico_path  # fallback to .ico

                icon_line = f"Icon={png_path}\n" if png_path.exists() else ""
                desk = desktop / "J.A.R.V.I.S.desktop"
                desk.write_text(
                    "[Desktop Entry]\n"
                    "Name=J.A.R.V.I.S\n"
                    f'Exec="{python}" "{script}"\n'
                    f"Path={script.parent}\n"
                    "Type=Application\n"
                    "Terminal=false\n"
                    "Categories=Utility;\n"
                    + icon_line
                )
                desk.chmod(desk.stat().st_mode | 0o755)
                # GNOME refuses to launch desktop files until they are
                # marked trusted ("Allow Launching") — do it automatically.
                try:
                    subprocess.run(
                        ["gio", "set", str(desk),
                         "metadata::trusted", "true"],
                        capture_output=True, timeout=5,
                    )
                except Exception:
                    pass  # non-GNOME desktops don't need (or have) gio

            self._log.append_log(f"SYS: Desktop shortcut created in '{desktop}'.")
        except Exception as e:
            self._log.append_log(
                f"ERR: Shortcut failed — {e} (desktop dir: '{desktop}')"
            )

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cw = self.centralWidget()
        if self._overlay and self._overlay.isVisible():
            ow, oh = 460, 390
            self._overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._remote_overlay and self._remote_overlay.isVisible():
            ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
            self._remote_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._customize_overlay and self._customize_overlay.isVisible():
            ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
            self._customize_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        # Camera preview — bottom-right corner of the center/HUD area
        pw = _CameraPreview._W
        ph = self._cam_preview.height() or _CameraPreview._H
        self._cam_preview.setGeometry(
            cw.width() - _RIGHT_W - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )
        # Clipboard panel — bottom-center
        if hasattr(self, '_clipboard_panel') and self._clipboard_panel.isVisible():
            self._position_clipboard_panel()
        # Quick drawer — reposition if open
        if hasattr(self, '_quick_drawer') and self._quick_drawer.isVisible():
            self._position_quick_drawer()

    def _update_metrics(self):
        snap = _metrics.snapshot()
        cpu, mem, gpu = snap["cpu"], snap["mem"], snap["gpu"]
        gpu_str = f"{gpu:.0f}%" if gpu >= 0 else "N/A"

        # CASUAL calm telemetry tiles
        if hasattr(self, "_cas_stats"):
            self._cas_stats["cpu"].setText(f"{cpu:.0f}%")
            self._cas_stats["mem"].setText(f"{mem:.0f}%")
            self._cas_stats["gpu"].setText(gpu_str)
        # HARDCORE mission CPU tile (tasks/elapsed come from set_swarm_mission)
        if hasattr(self, "_hc_stats"):
            self._hc_stats["cpu"].setText(f"{cpu:.0f}%")

        # Keep the "Aethelark Remembers" rail current as memory grows
        self._refresh_memory_card()


    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(54)
        w.setStyleSheet(f"""
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(15, 15, 18, 0.95), stop:1 rgba(8, 8, 10, 0.9));
            border-bottom: 1px solid {C.BORDER};
        """)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(12)

        # Left Section: Settings + Glowing White Logo
        left_layout = QHBoxLayout()
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        self._drawer_btn = QPushButton("⚙")
        self._drawer_btn.setFixedSize(28, 28)
        self._drawer_btn.setFont(QFont("Manrope", 12))
        self._drawer_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._drawer_btn.setToolTip("Settings & Controls")
        self._drawer_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 255, 255, 0.05); color: {C.PRI};
                border: 1px solid {C.BORDER}; border-radius: 12px;
            }}
            QPushButton:hover {{ color: {C.WHITE}; border-color: rgba(255, 255, 255, 0.2); background: rgba(255, 255, 255, 0.1); }}
            QPushButton:checked {{ color: {C.WHITE}; border-color: {C.PRI}; background: rgba(255, 255, 255, 0.15); }}
        """)
        self._drawer_btn.setCheckable(True)
        self._drawer_btn.clicked.connect(self._toggle_drawer)
        left_layout.addWidget(self._drawer_btn)

        logo_lbl = QLabel()
        logo_path = str(BASE_DIR / "assets/images/eagle_white.png")
        if os.path.exists(logo_path):
            px = QPixmap(logo_path)
            logo_lbl.setPixmap(px.scaled(36, 36, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            logo_lbl.setText("AE")
            logo_lbl.setFont(QFont("Doto", 15, QFont.Weight.Bold))
            logo_lbl.setStyleSheet(f"color: {C.WHITE};")
        logo_lbl.setToolTip("Aethelark System")
        logo_lbl.setStyleSheet("background: transparent;")
        left_layout.addWidget(logo_lbl)

        # Wordmark — flight-strip identity beside the eagle crest
        name_lbl = QLabel(self._assistant_name.upper())
        _wf = QFont("Doto", 13, QFont.Weight.Bold)
        _wf.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 132)
        name_lbl.setFont(_wf)
        name_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent; padding-left: 2px;")
        left_layout.addWidget(name_lbl)

        # CASUAL / HARDCORE segmented control — lives in the app's title bar
        self._dash_mode = "CASUAL"
        self._mode_seg = QWidget()
        self._mode_seg.setStyleSheet("background: rgba(0, 0, 0, 0.34); border-radius: 10px;")
        _seg = QHBoxLayout(self._mode_seg)
        _seg.setContentsMargins(3, 3, 3, 3)
        _seg.setSpacing(3)
        self._seg_casual = QPushButton("CASUAL")
        self._seg_hard   = QPushButton("HARDCORE")
        for _b in (self._seg_casual, self._seg_hard):
            _b.setCursor(Qt.CursorShape.PointingHandCursor)
            _b.setFont(QFont("Manrope", 8, QFont.Weight.Bold))
            _b.setFixedHeight(24)
            _seg.addWidget(_b)
        self._seg_casual.clicked.connect(lambda: self._set_dash_mode("CASUAL"))
        self._seg_hard.clicked.connect(lambda: self._set_dash_mode("HARDCORE"))
        left_layout.addSpacing(6)
        left_layout.addWidget(self._mode_seg)
        self._style_mode_seg()

        lay.addLayout(left_layout)
        lay.addStretch()

        # Center Section: Centered Time Capsule (Date & Time)
        time_capsule = QFrame()
        time_capsule.setStyleSheet("background: transparent; border: none;")
        
        from PyQt6.QtWidgets import QGraphicsDropShadowEffect
        tc_shadow = QGraphicsDropShadowEffect()
        tc_shadow.setBlurRadius(16)
        tc_shadow.setOffset(0, 1)
        tc_shadow.setColor(QColor(0, 0, 0, 180))
        time_capsule.setGraphicsEffect(tc_shadow)
        
        tc_layout = QHBoxLayout(time_capsule)
        tc_layout.setContentsMargins(14, 4, 14, 4)
        tc_layout.setSpacing(8)

        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setFont(QFont("Doto", 11, QFont.Weight.Bold))
        self._clock_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent; border: none;")
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tc_layout.addWidget(self._clock_lbl)

        self._date_lbl = QLabel("Mon 01 Jan 2000")
        self._date_lbl.setFont(QFont("Manrope", 8, QFont.Weight.Bold))
        self._date_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; border: none;")
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tc_layout.addWidget(self._date_lbl)

        lay.addWidget(time_capsule)
        lay.addStretch()

        # Right Section: Window Control Buttons
        right_layout = QHBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        # Minimize Button
        min_btn = QPushButton("—")
        min_btn.setFixedSize(26, 26)
        min_btn.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        min_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.03); color: #B0B0B0;
                border: 1px solid rgba(255, 255, 255, 0.06); border-radius: 13px;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 0.1); color: #FFF; }
        """)
        min_btn.clicked.connect(self.showMinimized)
        right_layout.addWidget(min_btn)

        # Collapse to Pill Button
        collapse_btn = QPushButton("Collapse")
        collapse_btn.setFixedHeight(26)
        collapse_btn.setFont(QFont("Manrope", 8, QFont.Weight.Bold))
        collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        collapse_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 255, 255, 0.03); color: {C.PRI};
                border: 1px solid rgba(255, 255, 255, 0.06); border-radius: 13px;
                padding: 0 10px;
            }}
            QPushButton:hover {{ background: rgba(255, 255, 255, 0.1); color: #FFF; }}
        """)
        collapse_btn.clicked.connect(lambda: self.set_ui_mode("PILL"))
        right_layout.addWidget(collapse_btn)

        # Close Button
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(26, 26)
        close_btn.setFont(QFont("Arial", 9, QFont.Weight.Bold))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.03); color: #B0B0B0;
                border: 1px solid rgba(255, 255, 255, 0.06); border-radius: 13px;
            }
            QPushButton:hover { background: #ef4444; color: #FFF; border-color: #ef4444; }
        """)
        close_btn.clicked.connect(self.close)
        right_layout.addWidget(close_btn)

        lay.addLayout(right_layout)
        return w

    def _tick_clock(self):
        self._clock_lbl.setText(time.strftime("%H:%M:%S"))
        self._date_lbl.setText(time.strftime("%a %d %b %Y"))

    # ── "Aethelark Remembers" — surfaced long-term memory ────────────────────
    def _memory_facts(self) -> list:
        """A few high-signal facts Aethelark knows about the user, from
        long-term memory. Returns [(icon, label, value), …] — empty when the
        store is still cold."""
        try:
            from memory.memory_manager import load_memory
            mem = load_memory()
        except Exception:
            return []

        def _val(d: dict, k: str) -> str:
            e = d.get(k)
            if isinstance(e, dict):
                return str(e.get("value") or "").strip()
            return str(e).strip() if isinstance(e, str) else ""

        ident = mem.get("identity", {}) if isinstance(mem, dict) else {}
        facts: list = []
        for icon, label, key in (("◈", "You go by", "name"),
                                 ("⌖", "Based in", "city"),
                                 ("✦", "Work", "job"),
                                 ("◈", "Speaks", "language")):
            v = _val(ident, key)
            if v:
                facts.append((icon, label, v))

        def _take(cat: str, icon: str, label: str, n: int = 1):
            for _k, e in list(mem.get(cat, {}).items())[:n]:
                v = e.get("value") if isinstance(e, dict) else e
                if v:
                    lbl = label or _k.replace("_", " ").title()
                    facts.append((icon, lbl, str(v)))

        _take("projects", "⬢", "Building", 2)
        _take("preferences", "⚡", "", 1)
        _take("wishes", "✧", "Wants", 1)
        return facts[:5]

    def _make_mem_pill(self, icon: str, label: str, value: str) -> QWidget:
        if len(value) > 26:
            value = value[:25].rstrip() + "…"
        f = QFrame()
        f.setStyleSheet("QFrame { background: rgba(0, 0, 0, 0.26); border-radius: 8px; }")
        h = QHBoxLayout(f)
        h.setContentsMargins(9, 7, 9, 7)
        h.setSpacing(9)
        ic = QLabel(icon)
        ic.setFixedSize(20, 20)
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ic.setFont(QFont("Manrope", 9))
        ic.setStyleSheet(f"color: {C.ACC}; background: rgba(200, 200, 208, 0.10); border-radius: 6px;")
        h.addWidget(ic)
        col = QVBoxLayout()
        col.setSpacing(1)
        col.setContentsMargins(0, 0, 0, 0)
        kl = QLabel(label.upper())
        kl.setFont(QFont("Manrope", 7, QFont.Weight.Bold))
        kl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        vl = QLabel(value)
        vl.setFont(QFont("Manrope", 10, QFont.Weight.Bold))
        vl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        col.addWidget(kl)
        col.addWidget(vl)
        h.addLayout(col)
        h.addStretch()
        return f

    def _refresh_memory_card(self):
        if not hasattr(self, "_mem_container"):
            return
        facts = self._memory_facts()
        sig = tuple((l, v) for _, l, v in facts)
        if getattr(self, "_mem_sig", None) == sig:
            return
        self._mem_sig = sig
        while self._mem_container.count():
            item = self._mem_container.takeAt(0)
            wdg = item.widget()
            if wdg is not None:
                wdg.deleteLater()
        if not facts:
            empty = QLabel("Getting to know you — I'll remember what matters as we talk.")
            empty.setWordWrap(True)
            empty.setFont(QFont("Manrope", 8))
            empty.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            self._mem_container.addWidget(empty)
            return
        for icon, label, value in facts:
            self._mem_container.addWidget(self._make_mem_pill(icon, label, value))

    def _build_memory_card(self) -> QWidget:
        card = QWidget()
        card.setStyleSheet("background: transparent;")
        v = QVBoxLayout(card)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        hdr = QLabel("◈ AETHELARK REMEMBERS")
        hdr.setFont(QFont("Manrope", 8, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 5px;")
        v.addWidget(hdr)
        self._mem_container = QVBoxLayout()
        self._mem_container.setSpacing(6)
        v.addLayout(self._mem_container)
        self._mem_sig = None
        self._refresh_memory_card()
        return card

    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_LEFT_W)
        w.setObjectName("LeftPanel")
        w.setStyleSheet(f"""
            QWidget#LeftPanel {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(24, 24, 28, 0.75), stop:1 rgba(12, 12, 15, 0.6));
                border-right: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 12px;
            }}
        """)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 16, 12, 16)
        lay.setSpacing(12)

        # Memory rail leads — the "it knows me" moment
        lay.addWidget(self._build_memory_card())
        lay.addStretch()

        # Compact telemetry at the base — Casual stays calm; full detail is HARDCORE
        mb = QHBoxLayout()
        mb.setSpacing(6)
        self._cas_stats = {}
        for key, label in (("cpu", "CPU"), ("mem", "MEM"), ("gpu", "GPU")):
            tile = QWidget()
            tile.setStyleSheet("background: rgba(0,0,0,0.24); border-radius: 8px;")
            tl = QVBoxLayout(tile)
            tl.setContentsMargins(4, 8, 4, 8)
            tl.setSpacing(2)
            val = QLabel("—")
            val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            val.setFont(QFont("Doto", 12, QFont.Weight.Bold))
            val.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            cap = QLabel(label)
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cap.setFont(QFont("Manrope", 7))
            cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            tl.addWidget(val)
            tl.addWidget(cap)
            mb.addWidget(tile)
            self._cas_stats[key] = val
        lay.addLayout(mb)

        return w

    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_RIGHT_W)
        w.setObjectName("RightPanel")
        w.setStyleSheet(f"""
            QWidget#RightPanel {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(24, 24, 28, 0.75), stop:1 rgba(12, 12, 15, 0.6));
                border-left: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 12px;
            }}
        """)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 16, 12, 16)
        lay.setSpacing(12)

        sec = QLabel("▸ TODAY")
        sec.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        sec.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        lay.addWidget(sec)

        self._log = LogWidget()
        lay.addWidget(self._log, stretch=1)

        # Search everything Aethelark has done — press Enter to find in the log
        self._log_search = QLineEdit()
        self._log_search.setPlaceholderText("🔍  Search everything Aethelark's done…")
        self._log_search.setFont(QFont("Manrope", 9))
        self._log_search.setFixedHeight(30)
        self._log_search.setStyleSheet(f"""
            QLineEdit {{ background: rgba(255,255,255,0.03); color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 15px; padding: 4px 12px; }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        self._log_search.returnPressed.connect(
            lambda: self._log.find(self._log_search.text()))
        lay.addWidget(self._log_search)

        lay.addLayout(self._build_input_row())

        self._mute_btn = QPushButton("🎙  MICROPHONE ACTIVE")
        self._mute_btn.setFixedHeight(30)
        self._mute_btn.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.clicked.connect(self._toggle_mute)
        if not hasattr(self, "_mute_btns"):
            self._mute_btns = []
        self._mute_btns.append(self._mute_btn)
        self._style_mute_btn()
        lay.addWidget(self._mute_btn)

        return w

    def _build_quick_drawer(self) -> QWidget:
        """Floating overlay panel shown when the ⚙ header button is toggled."""
        _BTN_STYLE_PRI = f"""
            QPushButton {{
                background: #00091a; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
                text-align: left; padding: 0 8px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """
        _BTN_STYLE_DIM = f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}
        """

        w = QWidget(self.centralWidget())
        w.setObjectName("QuickDrawer")
        w.setStyleSheet(f"""
            QWidget#QuickDrawer {{
                background: {C.DARK};
                border: 1px solid {C.BORDER_B};
                border-top: none;
                border-radius: 0 0 6px 6px;
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(5)

        hdr = QLabel("◈ CONTROLS")
        hdr.setFont(QFont("Manrope", 7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)

        remote_btn = QPushButton("◉  REMOTE CONTROL")
        remote_btn.setFixedHeight(30)
        remote_btn.setFont(QFont("Manrope", 8, QFont.Weight.Bold))
        remote_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remote_btn.setStyleSheet(_BTN_STYLE_PRI)
        remote_btn.clicked.connect(self._open_remote)
        lay.addWidget(remote_btn)

        fs_btn = QPushButton("⛶  FULLSCREEN  [F11]")
        fs_btn.setFixedHeight(26)
        fs_btn.setFont(QFont("Manrope", 7))
        fs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fs_btn.setStyleSheet(_BTN_STYLE_DIM)
        fs_btn.clicked.connect(self._toggle_fullscreen)
        lay.addWidget(fs_btn)

        sc_btn = QPushButton("⊞  CREATE DESKTOP SHORTCUT")
        sc_btn.setFixedHeight(26)
        sc_btn.setFont(QFont("Manrope", 7))
        sc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sc_btn.setStyleSheet(_BTN_STYLE_DIM)
        sc_btn.clicked.connect(self._create_desktop_shortcut)
        lay.addWidget(sc_btn)

        self._autostart_btn = QPushButton("◉  AUTO-START: OFF")
        self._autostart_btn.setFixedHeight(26)
        self._autostart_btn.setFont(QFont("Manrope", 7))
        self._autostart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._autostart_btn.clicked.connect(self._toggle_autostart)
        lay.addWidget(self._autostart_btn)

        cust_btn = QPushButton("⚙  CUSTOMISE ASSISTANT")
        cust_btn.setFixedHeight(26)
        cust_btn.setFont(QFont("Manrope", 7))
        cust_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cust_btn.setStyleSheet(_BTN_STYLE_DIM)
        cust_btn.clicked.connect(self._open_customize)
        lay.addWidget(cust_btn)

        self._brief_btn = QPushButton()
        self._brief_btn.setFixedHeight(26)
        self._brief_btn.setFont(QFont("Manrope", 7))
        self._brief_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._brief_btn.clicked.connect(self._toggle_brief)
        lay.addWidget(self._brief_btn)

        # File upload lives here now (kept out of the calm Casual rail)
        up_sep = QFrame(); up_sep.setFrameShape(QFrame.Shape.HLine)
        up_sep.setStyleSheet(f"color: {C.BORDER}; margin: 4px 0;")
        lay.addWidget(up_sep)
        up_hdr = QLabel("◈ FILE UPLOAD")
        up_hdr.setFont(QFont("Manrope", 7, QFont.Weight.Bold))
        up_hdr.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        lay.addWidget(up_hdr)
        self._drop_zone = FileDropZone()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        lay.addWidget(self._drop_zone)
        self._file_hint = QLabel("No file loaded — drop or click above to upload")
        self._file_hint.setFont(QFont("Manrope", 8))
        self._file_hint.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._file_hint.setWordWrap(True)
        lay.addWidget(self._file_hint)

        w.adjustSize()
        return w

    def _toggle_drawer(self, checked: bool):
        if checked:
            self._position_quick_drawer()
            self._quick_drawer.show()
            self._quick_drawer.raise_()
        else:
            self._quick_drawer.hide()

    def _position_quick_drawer(self):
        if not hasattr(self, '_quick_drawer'):
            return
        _W = 220
        self._quick_drawer.setFixedWidth(_W)
        self._quick_drawer.adjustSize()
        self._quick_drawer.setGeometry(12, 54, _W, self._quick_drawer.sizeHint().height())

    def _build_input_row(self) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(5)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Say it, or type it…")
        self._input.setFont(QFont("Manrope", 9))
        self._input.setFixedHeight(30)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: rgba(255, 255, 255, 0.03); color: {C.WHITE};
                border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 15px; padding: 5px 12px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; background: rgba(255, 255, 255, 0.06); }}
        """)
        self._input.returnPressed.connect(self._send)
        row.addWidget(self._input)

        send = QPushButton("▸")
        send.setFixedSize(30, 30)
        send.setFont(QFont("Manrope", 11, QFont.Weight.Bold))
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 255, 255, 0.05); color: {C.PRI};
                border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 15px;
            }}
            QPushButton:hover {{ background: rgba(255, 255, 255, 0.12); color: {C.WHITE}; border-color: {C.PRI}; }}
        """)
        send.clicked.connect(self._send)
        row.addWidget(send)
        return row

    def _build_content_panel(self) -> QWidget:
        """
        Collapsible panel below the HUD — shows search results, news, briefings.
        Hidden by default; appears when show_content() is called.
        """
        w = QWidget()
        w.setObjectName("ContentPanel")
        w.setStyleSheet(f"""
            QWidget#ContentPanel {{
                background: {C.PANEL};
                border-top: 1px solid {C.BORDER_B};
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(5)

        # ── header row ───────────────────────────────────────────────────────
        hdr = QHBoxLayout(); hdr.setSpacing(6)

        dot = QLabel("◈")
        dot.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._content_title_lbl = QLabel("BRIEFING")
        self._content_title_lbl.setFont(QFont("Manrope", 8, QFont.Weight.Bold))
        self._content_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;"
        )
        hdr.addWidget(self._content_title_lbl)
        hdr.addStretch()

        self._content_ts_lbl = QLabel("")
        self._content_ts_lbl.setFont(QFont("Manrope", 7))
        self._content_ts_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._content_ts_lbl)

        dismiss = QPushButton("DISMISS  ✕")
        dismiss.setFont(QFont("Manrope", 7))
        dismiss.setFixedHeight(18)
        dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        dismiss.clicked.connect(w.hide)
        hdr.addWidget(dismiss)
        lay.addLayout(hdr)

        # ── separator ─────────────────────────────────────────────────────────
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); lay.addWidget(sep)

        # ── text display ──────────────────────────────────────────────────────
        self._content_display = QTextEdit()
        self._content_display.setReadOnly(True)
        self._content_display.setFont(QFont("Manrope", 8))
        self._content_display.setMinimumHeight(60)
        self._content_display.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._content_display.setStyleSheet(f"""
            QTextEdit {{
                background: {C.DARK};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 6px 8px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG}; width: 6px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B}; border-radius: 3px; min-height: 16px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0; border: none;
            }}
        """)
        lay.addWidget(self._content_display)

        return w

    def _show_content(self, title: str, text: str):
        """Slot — runs on Qt main thread. Updates and shows the content panel."""
        import time as _time
        self._content_title_lbl.setText(title.upper()[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setPlainText(text)
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start
        )
        first_show = not self._content_panel.isVisible()
        self._content_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 220, 120), 220])

    def _build_footer(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(22)
        w.setStyleSheet(f"background: {C.DARK}; border-top: 1px solid {C.BORDER};")
        lay = QHBoxLayout(w); lay.setContentsMargins(14, 0, 14, 0)

        def _fl(txt, color=C.TEXT_MED):
            l = QLabel(txt); l.setFont(QFont("Manrope", 7))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_fl("[F4] Mute  ·  [F11] Fullscreen  ·  [ESC] Interrupt"))
        lay.addStretch()
        lay.addWidget(_fl("Space-Eagle", C.PRI_DIM))
        return w

    def _on_file_selected(self, path: str):
        self._current_file = path
        p    = Path(path)
        cat  = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size = _fmt_size(p.stat().st_size)
        self._file_hint.setText(f"{icon}  {p.name}  ·  {size}  ·  Tell {self._assistant_name} what to do with it")
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def notify_phone_connected(self) -> None:
        if self._remote_overlay and self._remote_overlay.isVisible():
            self._remote_overlay.mark_connected()

    def _open_remote(self):
        if not self.on_remote_clicked:
            self._log.append_log("SYS: Dashboard not running — remote unavailable.")
            return
        result = self.on_remote_clicked()
        if not result:
            self._log.append_log("SYS: Could not generate remote key.")
            return
        url    = result[0]
        key    = result[1]
        auto   = result[2] if len(result) >= 3 else ""
        manual = result[3] if len(result) >= 4 else url
        if self._remote_overlay:
            self._remote_overlay._do_close()
        cw  = self.centralWidget()
        ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
        ov  = RemoteKeyOverlay(url, key, auto_login_url=auto, manual_url=manual,
                               expiry_secs=600, parent=cw)
        ov.set_new_key_callback(self.on_remote_clicked)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.closed.connect(lambda: setattr(self, '_remote_overlay', None))
        ov.show()
        self._remote_overlay = ov
        self._log.append_log(f"SYS: Remote key generated — manual: {manual or url}")

    # ── Auto-start ──────────────────────────────────────────────────────────────

    def _check_autostart(self) -> bool:
        """Returns True if auto-start is currently registered on this OS."""
        try:
            if _OS == "Windows":
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
                try:
                    winreg.QueryValueEx(key, "Aethelark_AI")
                    return True
                except FileNotFoundError:
                    return False
                finally:
                    winreg.CloseKey(key)
            elif _OS == "Darwin":
                return (Path.home() / "Library" / "LaunchAgents"
                        / "com.aethelark.assistant.plist").exists()
            else:
                return (Path.home() / ".config" / "autostart" / "aethelark.desktop").exists()
        except Exception:
            return False

    def _toggle_autostart(self):
        currently_on = self._check_autostart()
        try:
            script = str(Path(__file__).resolve().parent / "main.py")
            if _OS == "Windows":
                import winreg
                reg = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS)
                if currently_on:
                    winreg.DeleteValue(reg, "Aethelark_AI")
                else:
                    pythonw = Path(sys.executable).parent / "pythonw.exe"
                    exe = str(pythonw if pythonw.exists() else sys.executable)
                    winreg.SetValueEx(reg, "Aethelark_AI", 0, winreg.REG_SZ,
                                      f'"{exe}" "{script}"')
                winreg.CloseKey(reg)
            elif _OS == "Darwin":
                plist_dir = Path.home() / "Library" / "LaunchAgents"
                plist_dir.mkdir(parents=True, exist_ok=True)
                plist = plist_dir / "com.aethelark.assistant.plist"
                if currently_on:
                    plist.unlink(missing_ok=True)
                else:
                    plist.write_text(
                        '<?xml version="1.0" encoding="UTF-8"?>\n'
                        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                        '<plist version="1.0"><dict>\n'
                        '  <key>Label</key><string>com.aethelark.assistant</string>\n'
                        '  <key>ProgramArguments</key><array>\n'
                        f'    <string>{sys.executable}</string>\n'
                        f'    <string>{script}</string>\n'
                        '  </array>\n'
                        '  <key>RunAtLoad</key><true/>\n'
                        '</dict></plist>\n'
                    )
            else:
                desk_dir = Path.home() / ".config" / "autostart"
                desk_dir.mkdir(parents=True, exist_ok=True)
                desk = desk_dir / "aethelark.desktop"
                if currently_on:
                    desk.unlink(missing_ok=True)
                else:
                    desk.write_text(
                        "[Desktop Entry]\n"
                        f"Name={self._assistant_name}\n"
                        f"Exec={sys.executable} {script}\n"
                        "Type=Application\nTerminal=false\n"
                        "X-GNOME-Autostart-enabled=true\n"
                    )
            enabled = not currently_on
            self._update_autostart_btn(enabled)
            self._log.append_log(
                f"SYS: Auto-start {'enabled' if enabled else 'disabled'}.")
        except Exception as e:
            self._log.append_log(f"ERR: Auto-start failed — {e}")

    def _update_autostart_btn(self, enabled: bool):
        if not hasattr(self, '_autostart_btn'):
            return
        if enabled:
            self._autostart_btn.setText("◉  AUTO-START: ON")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            self._autostart_btn.setText("◉  AUTO-START: OFF")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    def _toggle_brief(self):
        from memory.config_manager import get_brief_enabled, save_brief_enabled
        new_val = not get_brief_enabled()
        save_brief_enabled(new_val)
        self._update_brief_btn(new_val)

    def _update_brief_btn(self, enabled: bool):
        if not hasattr(self, '_brief_btn'):
            return
        if enabled:
            self._brief_btn.setText("☀  MORNING BRIEF: ON")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            self._brief_btn.setText("☀  MORNING BRIEF: OFF")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    # ── Customization ────────────────────────────────────────────────────────────

    def _open_customize(self):
        cfg = _read_full_config()
        if self._customize_overlay:
            self._customize_overlay.hide()
        cw = self.centralWidget()
        ov = CustomizeOverlay(
            cfg.get("assistant_name", "Aethelark") or "Aethelark",
            cfg.get("user_name", ""),
            cfg.get("ui_color", "") or DEFAULT_UI_COLOR,
            parent=cw,
        )
        ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
        oh = min(oh, cw.height() - 16)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.on_preview = self._preview_ui_color
        ov.saved.connect(self._apply_name_update)
        ov.show()
        self._customize_overlay = ov

    def _preview_ui_color(self, hex_color: str):
        """Canlı önizleme — tüm arayüzü yeni renge boyar (config'e YAZMAZ)."""
        old = current_palette()
        if apply_ui_accent(hex_color):
            retheme_all_widgets(old, current_palette())

    def _apply_name_update(self, name: str, user_name: str, ui_color: str = ""):
        """Update all name/theme-dependent UI elements and persist to config."""
        self._assistant_name = name.strip() or "Aethelark"
        display = self._assistant_name.upper()
        self.setWindowTitle(f"{display} — AETHELARK")
        self._title_lbl.setText(display)
        if display in ("AETHELARK", "A.E.T.H.E.L.A.R.K"):
            self._sub_lbl.setText("Autonomous Agentic Development Core")
        elif display in ("JARVIS", "J.A.R.V.I.S"):
            self._sub_lbl.setText("Just A Rather Very Intelligent System")
        else:
            self._sub_lbl.setText("Personal AI Assistant")
        self._log._ai_name_lc = self._assistant_name.lower()
        self.hud._assistant_name = display

        color_changed = False
        if ui_color:
            old = current_palette()
            if apply_ui_accent(ui_color):
                # Tüm arayüzü (paneller, butonlar, kenarlıklar, HUD) canlı boya
                retheme_all_widgets(old, current_palette())
                color_changed = old["PRI"] != C.PRI

        try:
            data = _read_full_config()
            data["assistant_name"] = self._assistant_name
            data["user_name"] = user_name.strip()
            if ui_color:
                data["ui_color"] = ui_color.strip().lower()
            API_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
            self._log.append_log(f"SYS: Identity updated — {display}")
            if color_changed:
                self._log.append_log(f"SYS: UI colour applied — {ui_color}")
        except Exception as e:
            self._log.append_log(f"ERR: Config save failed — {e}")

    # ── Clipboard intelligence ───────────────────────────────────────────────────

    def _on_clipboard_changed(self):
        try:
            text = QApplication.clipboard().text().strip()
            if len(text) >= 10:
                self._clipboard_sig.emit(text)
        except Exception:
            pass

    def _show_clipboard_panel(self, text: str):
        self._clipboard_panel.show_clipboard(text)
        self._position_clipboard_panel()

    def _position_clipboard_panel(self):
        cw = self.centralWidget()
        pw = ClipboardPanel._W
        ph = self._clipboard_panel.sizeHint().height() or ClipboardPanel._H
        x = (cw.width() - pw) // 2
        y = cw.height() - ph - 6
        self._clipboard_panel.setGeometry(x, y, pw, ph)
        self._clipboard_panel.raise_()

    def _on_clipboard_action(self, cmd: str):
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(cmd,), daemon=True).start()

    # ────────────────────────────────────────────────────────────────────────────

    def _do_interrupt(self):
        if self.on_interrupt:
            self.on_interrupt()

    def _toggle_mute(self):
        self._muted = not self._muted
        self.hud.muted = self._muted
        self._style_mute_btn()
        if self._muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted.")
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active.")

    def _style_mute_btn(self):
        if self._muted:
            text = "🔇  MICROPHONE MUTED"
            css = (f"QPushButton {{ background: #140006; color: {C.MUTED_C};"
                   f" border: 1px solid {C.MUTED_C}; border-radius: 3px; }}")
        else:
            text = "🎙  MICROPHONE ACTIVE"
            css = (f"QPushButton {{ background: #00140a; color: {C.GREEN};"
                   f" border: 1px solid {C.GREEN}; border-radius: 3px; }}"
                   f" QPushButton:hover {{ background: #001f10; }}")
        for b in getattr(self, "_mute_btns", []):
            b.setText(text)
            b.setStyleSheet(css)

    def _send(self):
        txt = self._input.text().strip()
        if not txt: return
        self._input.clear()
        self._send_text(txt)

    def _send_text(self, txt: str):
        """Dispatch an arbitrary command through the same path as the input box —
        used by the input, the send button, and the starter chips."""
        txt = (txt or "").strip()
        if not txt:
            return
        self._log.append_log(f"You: {txt}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    def _build_starter_chips(self) -> QWidget:
        """Conversation-starter chips under the core — teach new users what
        Aethelark can do, and fire the command straight through on click."""
        chip_css = f"""
            QPushButton {{
                background: rgba(0, 0, 0, 0.28); color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 15px; padding: 7px 12px;
            }}
            QPushButton:hover {{
                color: #FFFFFF; border: 1px solid {C.PRI_DIM};
                background: rgba(255, 255, 255, 0.05);
            }}
        """
        starters = [
            ("♪   Focus playlist",   "Play my focus playlist on YouTube"),
            ("✉   New emails",       "Check my email and read me anything new"),
            ("▤   Notes → PDF",      "Turn my latest notes into a PDF"),
            ("◇   What can you do?", "What can you do?"),
        ]
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        outer = QVBoxLayout(w)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(7)
        for row in (starters[:2], starters[2:]):
            r = QHBoxLayout()
            r.setSpacing(8)
            for label, cmd in row:
                b = QPushButton(label)
                b.setFont(QFont("Manrope", 9))
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                b.setStyleSheet(chip_css)
                b.clicked.connect(lambda _=False, c=cmd: self._send_text(c))
                r.addWidget(b)
            outer.addLayout(r)
        return w

    # ── HARDCORE swarm view — full three-column re-layout ────────────────────
    def _section_label(self, txt: str) -> QLabel:
        l = QLabel(f"◈ {txt}")
        l.setFont(QFont("Manrope", 8, QFont.Weight.Bold))
        l.setStyleSheet(f"color: {C.PRI}; background: transparent; "
                        f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 5px;")
        return l

    def _build_swarm_left_panel(self) -> QWidget:
        """HARDCORE left rail — conductor badge + MISSION status + minibars."""
        w = QWidget()
        w.setFixedWidth(_LEFT_W)
        w.setObjectName("LeftPanel")
        w.setStyleSheet(f"""
            QWidget#LeftPanel {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(24,24,28,0.75), stop:1 rgba(12,12,15,0.6));
                border-right: 1px solid rgba(255,255,255,0.08); border-radius: 12px;
            }}
        """)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 16, 12, 16)
        lay.setSpacing(12)

        cond = QWidget()
        cond.setStyleSheet("background: rgba(0,0,0,0.28); border-radius: 12px;")
        ch = QHBoxLayout(cond)
        ch.setContentsMargins(13, 10, 13, 10)
        ch.setSpacing(11)
        badge = QLabel()
        ep = str(BASE_DIR / "assets/images/eagle_white.png")
        if os.path.exists(ep):
            badge.setPixmap(QPixmap(ep).scaled(28, 28, Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation))
        badge.setStyleSheet("background: transparent;")
        ch.addWidget(badge)
        ccol = QVBoxLayout()
        ccol.setSpacing(2)
        ct = QLabel("CONDUCTING")
        _tf = QFont("Doto", 11, QFont.Weight.Bold)
        _tf.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 115)
        ct.setFont(_tf)
        ct.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        self._swarm_sub = QLabel("SWARM STANDBY")
        self._swarm_sub.setFont(QFont("Manrope", 8, QFont.Weight.Bold))
        self._swarm_sub.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        ccol.addWidget(ct)
        ccol.addWidget(self._swarm_sub)
        ch.addLayout(ccol)
        ch.addStretch()
        lay.addWidget(cond)

        lay.addWidget(self._section_label("MISSION"))
        self._mission_lbls = {}
        for key, label in (("repo", "REPO"), ("worktrees", "WORKTREES"),
                           ("merged", "MERGED"), ("conflicts", "CONFLICTS")):
            row = QHBoxLayout()
            k = QLabel(label)
            k.setFont(QFont("Manrope", 9))
            k.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            v = QLabel("—")
            v.setFont(QFont("Doto", 10, QFont.Weight.Bold))
            v.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(k)
            row.addStretch()
            row.addWidget(v)
            lay.addLayout(row)
            self._mission_lbls[key] = v

        self._mission_gauge = QProgressBar()
        self._mission_gauge.setTextVisible(False)
        self._mission_gauge.setFixedHeight(6)
        self._mission_gauge.setRange(0, 100)
        self._mission_gauge.setValue(0)
        self._mission_gauge.setStyleSheet(
            "QProgressBar { background: rgba(255,255,255,0.05); border: none; border-radius: 3px; }"
            " QProgressBar::chunk { border-radius: 3px; background: qlineargradient("
            "x1:0,y1:0,x2:1,y2:0, stop:0 #6a6a72, stop:1 #ECECF2); }")
        lay.addSpacing(2)
        lay.addWidget(self._mission_gauge)

        lay.addStretch()

        mb = QHBoxLayout()
        mb.setSpacing(6)
        self._hc_stats = {}
        for key, label in (("cpu", "CPU"), ("tasks", "TASKS"), ("elapsed", "ELAPSED")):
            tile = QWidget()
            tile.setStyleSheet("background: rgba(0,0,0,0.24); border-radius: 8px;")
            tl = QVBoxLayout(tile)
            tl.setContentsMargins(4, 7, 4, 7)
            tl.setSpacing(2)
            val = QLabel("—")
            val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            val.setFont(QFont("Doto", 11, QFont.Weight.Bold))
            val.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            cap = QLabel(label)
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cap.setFont(QFont("Manrope", 7))
            cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            tl.addWidget(val)
            tl.addWidget(cap)
            mb.addWidget(tile)
            self._hc_stats[key] = val
        lay.addLayout(mb)
        return w

    def _build_swarm_stage(self) -> QWidget:
        """HARDCORE centre — the agent lanes + blackboard."""
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(11)
        self._swarm_lanes = []
        for _ in range(3):
            lane = SwarmLane()
            self._swarm_lanes.append(lane)
            v.addWidget(lane, stretch=1)
        self._blackboard = QLabel("")
        self._blackboard.setWordWrap(True)
        self._blackboard.setFont(QFont("Manrope", 9))
        self._blackboard.setStyleSheet(
            "color: #d8cdf0; border-radius: 10px; padding: 10px 13px;"
            " background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            " stop:0 rgba(200,162,255,0.09), stop:1 rgba(0,0,0,0.30));")
        v.addWidget(self._blackboard)
        self.set_swarm_agents([])   # standby by default
        return w

    def _build_swarm_right_panel(self) -> QWidget:
        """HARDCORE right rail — searchable timeline + INTERJECT + mic."""
        w = QWidget()
        w.setFixedWidth(_RIGHT_W)
        w.setObjectName("RightPanel")
        w.setStyleSheet(f"""
            QWidget#RightPanel {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba(24,24,28,0.75), stop:1 rgba(12,12,15,0.6));
                border-left: 1px solid rgba(255,255,255,0.08); border-radius: 12px;
            }}
        """)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 16, 12, 16)
        lay.setSpacing(10)

        sec = QLabel("▸ TIMELINE")
        sec.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        sec.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        lay.addWidget(sec)

        self._tl_search = QLineEdit()
        self._tl_search.setPlaceholderText("🔍  Search the mission…")
        self._tl_search.setFont(QFont("Manrope", 9))
        self._tl_search.setFixedHeight(30)
        self._tl_search.setStyleSheet(f"""
            QLineEdit {{ background: rgba(255,255,255,0.03); color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 15px; padding: 4px 12px; }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        self._tl_search.textChanged.connect(lambda _t: self._render_timeline())
        lay.addWidget(self._tl_search)

        self._tl_box_w = QWidget()
        self._tl_box_w.setStyleSheet("background: rgba(0,0,0,0.30); border-radius: 10px;")
        self._tl_box = QVBoxLayout(self._tl_box_w)
        self._tl_box.setContentsMargins(11, 10, 11, 10)
        self._tl_box.setSpacing(7)
        lay.addWidget(self._tl_box_w, stretch=1)

        interject = QPushButton("⛔  INTERJECT · HALT SWARM")
        interject.setFixedHeight(36)
        interject.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        interject.setCursor(Qt.CursorShape.PointingHandCursor)
        interject.setStyleSheet(f"""
            QPushButton {{ background: #140008; color: {C.MUTED_C};
                border: 1px solid {C.MUTED_C}; border-radius: 3px; }}
            QPushButton:hover {{ background: #200010; border: 1px solid #ff6688; }}
        """)
        interject.clicked.connect(self._do_interrupt)
        lay.addWidget(interject)

        if not hasattr(self, "_mute_btns"):
            self._mute_btns = []
        mic = QPushButton()
        mic.setFixedHeight(30)
        mic.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        mic.setCursor(Qt.CursorShape.PointingHandCursor)
        mic.clicked.connect(self._toggle_mute)
        self._mute_btns.append(mic)
        lay.addWidget(mic)
        self._style_mute_btn()

        self._tl_events = []
        self._render_timeline()
        return w

    def _render_timeline(self):
        if not hasattr(self, "_tl_box"):
            return
        while self._tl_box.count():
            item = self._tl_box.takeAt(0)
            wdg = item.widget()
            if wdg is not None:
                wdg.setParent(None)   # remove from view immediately (deleteLater is async)
                wdg.deleteLater()
        q = (self._tl_search.text() or "").lower().strip() if hasattr(self, "_tl_search") else ""
        evs = [e for e in getattr(self, "_tl_events", []) if not q or q in str(e[1]).lower()]
        if not evs:
            lab = QLabel("Timeline idle — events stream here as the swarm works.")
            lab.setWordWrap(True)
            lab.setFont(QFont("Manrope", 9))
            lab.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            self._tl_box.addWidget(lab)
        else:
            for ts, text, done in evs:
                row = QLabel(f"<span style='color:{C.TEXT_DIM}'>{ts}</span>&nbsp;&nbsp;"
                             f"{'✓ ' if done else ''}{text}")
                row.setTextFormat(Qt.TextFormat.RichText)
                row.setWordWrap(True)
                row.setFont(QFont("Manrope", 9))
                row.setStyleSheet(f"color: {'#b9b9c2' if not done else C.GREEN}; background: transparent;")
                self._tl_box.addWidget(row)
        self._tl_box.addStretch()

    def set_swarm_timeline(self, events: list):
        """Feed the HARDCORE timeline. events = [(ts, text, done_bool), …]."""
        self._tl_events = list(events or [])
        self._render_timeline()

    def set_swarm_mission(self, repo="—", worktrees=0, merged="0 / 0", conflicts=0,
                          progress=0, cpu="—", tasks="—", elapsed="—"):
        """Update the HARDCORE MISSION panel from the orchestrator."""
        if not hasattr(self, "_mission_lbls"):
            return
        self._mission_lbls["repo"].setText(str(repo))
        self._mission_lbls["worktrees"].setText(str(worktrees))
        self._mission_lbls["merged"].setText(str(merged))
        cl = self._mission_lbls["conflicts"]
        cl.setText(str(conflicts))
        cl.setStyleSheet(f"color: {'#ffb060' if conflicts else C.TEXT_MED}; background: transparent;")
        self._mission_gauge.setValue(int(progress))
        self._hc_stats["cpu"].setText(str(cpu))
        self._hc_stats["tasks"].setText(str(tasks))
        self._hc_stats["elapsed"].setText(str(elapsed))

    def set_swarm_agents(self, agents: list):
        """Feed live swarm state into the HARDCORE view. Each item is a dict with
        glyph/name/branch/status/thought/adds/dels/file/elapsed. Empty list →
        standby. The swarm orchestrator calls this; must run on the UI thread."""
        if not hasattr(self, "_swarm_lanes"):
            return
        active = [a for a in agents if a]
        n = len(active)
        if hasattr(self, "_swarm_sub"):
            self._swarm_sub.setText(
                f"{n} AGENT{'S' if n != 1 else ''} · ACTIVE" if n else "SWARM STANDBY")
        for i, lane in enumerate(self._swarm_lanes):
            if i < len(agents) and agents[i]:
                a = agents[i]
                lane.show()
                lane.set_data(
                    glyph=a.get("glyph", "•"), name=a.get("name", "—"),
                    branch=a.get("branch", ""), status=a.get("status", "idle"),
                    thought=a.get("thought", ""), adds=a.get("adds", 0),
                    dels=a.get("dels", 0), file=a.get("file", ""),
                    elapsed=a.get("elapsed", ""))
            elif active:
                lane.hide()          # some agents active — hide spare lanes
            else:
                lane.show()          # full standby — dim idle lane
                lane.set_data(glyph="•", name="Awaiting deployment",
                              branch="no worktree", status="idle",
                              thought="Delegate a task, or say “assemble the swarm.”")
        if hasattr(self, "_blackboard"):
            self._blackboard.setText(
                "Blackboard: agents broadcast architectural decisions here — kept in sync, no collisions."
                if n else
                "Blackboard: idle. When agents deploy, their shared decisions stream here.")

    def _style_mode_seg(self):
        on = ("QPushButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
              " stop:0 rgba(200,200,208,0.18), stop:1 rgba(200,200,208,0.05));"
              f" color: {C.TEXT}; border: 1px solid rgba(200,200,208,0.20);"
              " border-radius: 7px; padding: 0 12px; }")
        off = ("QPushButton { background: transparent;"
               f" color: {C.TEXT_DIM}; border: none; border-radius: 7px;"
               " padding: 0 12px; }"
               f" QPushButton:hover {{ color: {C.PRI_DIM}; }}")
        casual = getattr(self, "_dash_mode", "CASUAL") == "CASUAL"
        self._seg_casual.setStyleSheet(on if casual else off)
        self._seg_hard.setStyleSheet(off if casual else on)

    def _set_dash_mode(self, mode: str):
        self._dash_mode = mode.upper()
        idx = 1 if self._dash_mode == "HARDCORE" else 0
        for name in ("_left_stack", "_center_stack", "_right_stack"):
            st = getattr(self, name, None)
            if st is not None:
                st.setCurrentIndex(idx)
        self._style_mode_seg()

    def _apply_state(self, state: str):
        self.hud.state    = state
        self.hud.speaking = (state == "SPEAKING")
        if hasattr(self, "_pill_widget") and self._pill_widget is not None:
            self._pill_widget.set_state(state)
            if state.upper() == "SPEAKING":
                self.expand_pill_for_speech(True)
            else:
                self.expand_pill_for_speech(False)

    def _check_config(self) -> bool:
        if not API_FILE.exists(): return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return bool(d.get("gemini_api_key")) and bool(d.get("os_system"))
        except Exception:
            return False

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        cw = self.centralWidget()
        ow, oh = 460, 390
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_setup_done)
        ov.show()
        self._overlay = ov

    def _on_setup_done(self, key: str, os_name: str):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        API_FILE.write_text(
            json.dumps({"gemini_api_key": key, "os_system": os_name}, indent=4),
            encoding="utf-8",
        )
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._apply_state("LISTENING")
        self._assistant_name = _read_full_config().get("assistant_name", "Aethelark") or "Aethelark"
        self._log.append_log(f"SYS: Initialised. OS={os_name.upper()}. {self._assistant_name} online.")


    def set_ui_mode(self, mode: str):
        mode = mode.upper()
        if not hasattr(self, "_ui_mode"):
            self._ui_mode = "DASHBOARD"
        if self._ui_mode == mode and self._anim:
            return
            
        screen = QApplication.primaryScreen().availableGeometry()
        
        # Clear size constraints so morph animations work smoothly
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        
        if mode == "PILL":
            if self._ui_mode == "DASHBOARD":
                self._normal_geom = self.geometry()
            
            # Hide overlays in pill mode
            if hasattr(self, "_cam_preview") and self._cam_preview is not None:
                self._cam_preview.hide()
            if hasattr(self, "_clipboard_panel") and self._clipboard_panel is not None:
                self._clipboard_panel.hide()
                
            # Switch views
            self._dashboard_container.hide()
            self._pill_widget.show()
            self._stacked.setCurrentIndex(1)
            
            target_w, target_h = 240, 84
            if self._normal_geom:
                target_x = self.x() + (self.width() - target_w) // 2
                target_y = max(10, self.y())
            else:
                target_x = (screen.width() - target_w) // 2
                target_y = 15
                
            target_rect = QRectF(target_x, target_y, target_w, target_h).toRect()
            self._ui_mode = "PILL"
            self._animate_geometry(target_rect)
            
        elif mode == "DASHBOARD":
            # Switch views
            self._pill_widget.hide()
            self._dashboard_container.show()
            self._stacked.setCurrentIndex(0)
            
            target_w, target_h = 980, 700
            if self._normal_geom:
                pill_center_x = self.x() + self.width() // 2
                target_x = pill_center_x - target_w // 2
                target_y = max(30, self.y())
            else:
                target_x = (screen.width() - target_w) // 2
                target_y = (screen.height() - target_h) // 2
                
            target_x = max(0, min(target_x, screen.width() - target_w))
            target_y = max(0, min(target_y, screen.height() - target_h))
            
            target_rect = QRectF(target_x, target_y, target_w, target_h).toRect()
            self._ui_mode = "DASHBOARD"
            self._animate_geometry(target_rect)
            self.activateWindow()
            self.raise_()

    def expand_pill_for_speech(self, expand: bool):
        if not hasattr(self, "_ui_mode") or self._ui_mode != "PILL":
            return
        target_w = 260 if expand else 200
        target_h = 60 if expand else 56
        screen = QApplication.primaryScreen().availableGeometry()
        target_x = self.x() + (self.width() - target_w) // 2
        target_y = self.y()
        target_rect = QRectF(target_x, target_y, target_w, target_h).toRect()
        # Subtle, frequent breathing nudge — quick and near-critically-damped
        # (no bounce) so it never feels twitchy while Aethelark speaks.
        self._animate_geometry(target_rect, duration=240,
                               curve=make_spring_curve(320.0, 34.0))

    def _animate_geometry(self, target_rect, duration: int = 520, curve=None):
        from PyQt6.QtCore import QPropertyAnimation
        if self._anim:
            self._anim.stop()
        self._anim = QPropertyAnimation(self, b"geometry")
        self._anim.setDuration(duration)
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(target_rect)

        # True iOS-style spring physics — the window collapses / expands with the
        # exact ~3% overshoot-and-settle from the approved design mockup.
        if curve is None:
            curve = make_spring_curve(260.0, 24.0)
        self._anim.setEasingCurve(curve)

        def on_finished():
            if self._ui_mode == "DASHBOARD":
                self.setMinimumSize(_MIN_W, _MIN_H)
            self._anim = None

        self._anim.finished.connect(on_finished)
        self._anim.start()

    def changeEvent(self, event):
        super().changeEvent(event)
        from PyQt6.QtCore import QEvent
        if event.type() == QEvent.Type.ActivationChange:
            if not self.isActiveWindow() and hasattr(self, "_ui_mode") and self._ui_mode == "DASHBOARD":
                if not QApplication.activeModalWidget():
                    self.set_ui_mode("PILL")

    def mousePressEvent(self, event):
        is_header_click = False
        if hasattr(self, "_ui_mode") and self._ui_mode == "DASHBOARD":
            if event.position().y() <= 54:
                is_header_click = True

        if hasattr(self, "_ui_mode") and (self._ui_mode == "PILL" or is_header_click) and event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = True
            from PyQt6.QtGui import QCursor
            self._drag_pos = QCursor.pos() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_active:
            from PyQt6.QtGui import QCursor
            self.move(QCursor.pos() - self._drag_pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = False
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        is_header_click = False
        if hasattr(self, "_ui_mode") and self._ui_mode == "DASHBOARD":
            if event.position().y() <= 54:
                is_header_click = True

        if hasattr(self, "_ui_mode") and event.button() == Qt.MouseButton.LeftButton:
            if self._ui_mode == "PILL":
                self.set_ui_mode("DASHBOARD")
                event.accept()
            elif is_header_click:
                self.set_ui_mode("PILL")
                event.accept()
            else:
                super().mouseDoubleClickEvent(event)
        else:
            super().mouseDoubleClickEvent(event)

class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass
    def quit(self):
        self._app.quit()


class AethelarkUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        load_app_fonts()
        self._app.setStyle("Fusion")
        self._win = MainWindow(face_path)
        self._win.show()
        self.root = _RootShim(self._app)

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    @property
    def current_file(self) -> str | None:
        return self._win._drop_zone.current_file()

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_remote_clicked(self):
        return self._win.on_remote_clicked

    @on_remote_clicked.setter
    def on_remote_clicked(self, cb):
        self._win.on_remote_clicked = cb

    @property
    def on_interrupt(self):
        return self._win.on_interrupt

    @on_interrupt.setter
    def on_interrupt(self, cb):
        self._win.on_interrupt = cb

    def notify_phone_connected(self) -> None:
        self._win.notify_phone_connected()

    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    def write_log(self, text: str):
        self._win._log_sig.emit(text)

    def set_audio_level(self, level: float):
        """Thread-safe: push the TTS output envelope (0..1) for pill breathing."""
        self._win._audio_sig.emit(level)

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def show_content(self, title: str, text: str):
        """Thread-safe: display content in the panel below the HUD."""
        self._win._content_sig.emit(title[:48], text[:4000])

    def prompt_reconfig(self):
        """Thread-safe: show the API key setup overlay (e.g. after an auth error)."""
        self._win._ready = False
        self._win._reconfig_sig.emit()

    def show_camera_frame(self, img_bytes: bytes):
        """Thread-safe: show a webcam frame in the small overlay (screen captures)."""
        self._win._camera_sig.emit(img_bytes)

    def start_camera_stream(self) -> None:
        """Thread-safe: start live camera feed in the full HUD area."""
        self._win.start_camera_stream()

    def stop_camera_stream(self) -> None:
        """Thread-safe: stop the live camera feed."""
        self._win.stop_camera_stream()

    @property
    def assistant_name(self) -> str:
        return self._win._assistant_name

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")