"""What the web shell needs from the old cockpit, and nothing else.

The QPainter interface this file used to hold — HudCanvas through MainWindow
through AethelarkUI, about 4,750 lines — was deleted on 2026-08-28. The shipped
surface is `aethelark_web.WebShellUI`.

Three things outlived it, and `aethelark_web.py` imports exactly these:

  * `load_app_fonts`  — registers Doto and Manrope so text matches the design
  * `_metrics`        — the background CPU / memory / net / GPU / temp sampler
  * `make_spring_curve` — a real spring step-response sampled into a QEasingCurve

Everything else went with the cockpit, including 35 unused Qt widget imports
this module was still paying for at startup.
"""
from __future__ import annotations

import math
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

from PyQt6.QtCore import QEasingCurve

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()


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






_DEFAULT_W, _DEFAULT_H = 980, 700
_MIN_W,     _MIN_H     = 820, 580

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"
_PYNVML_UI = None  # cached pynvml handle: None=untested, False=absent, module=ready


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





# Keys tied to the accent colour -- the status colours (ACC, GREEN, RED…) stay fixed











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
            except Exception as _e:
                print(f"[ui.py] Non-fatal error at line 364: {_e}")
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
        # pynvml — subprocess-free, works on all platforms if installed. OPTIONAL:
        # cache the import so a missing dep doesn't spam this metrics-poll loop.
        global _PYNVML_UI
        if _PYNVML_UI is None:
            try:
                import pynvml  # type: ignore
                _PYNVML_UI = pynvml
            except Exception:
                _PYNVML_UI = False  # not installed — fall through quietly
        if _PYNVML_UI:
            try:
                _PYNVML_UI.nvmlInit()
                h = _PYNVML_UI.nvmlDeviceGetHandleByIndex(0)
                return float(_PYNVML_UI.nvmlDeviceGetUtilizationRates(h).gpu)
            except Exception:
                pass  # nvml runtime error — fall through to the ctypes probe

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
        except Exception as _e:
            print(f"[ui.py] Non-fatal error at line 424: {_e}")

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
        except Exception as _e:
            print(f"[ui.py] Non-fatal error at line 440: {_e}")

        # Windows: wmi module (pure Python COM, zero subprocess)
        if _OS == "Windows":
            try:
                import wmi  # type: ignore
                w = wmi.WMI(namespace="root/wmi")
                tz = w.MSAcpi_ThermalZoneTemperature()
                if tz:
                    return (tz[0].CurrentTemperature / 10.0) - 273.15
            except Exception as _e:
                print(f"[ui.py] Non-fatal error at line 451: {_e}")

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


# The QPainter cockpit (HudCanvas … MainWindow … AethelarkUI) was removed on
# 2026-08-28. The shipped surface is aethelark_web.WebShellUI, which satisfies
# core.ui_contract.AethelarkUI exactly as the old class did. What remains here
# is only what the web shell imports: fonts, metrics, and the spring curve.
