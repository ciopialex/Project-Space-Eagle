"""
System Monitor — background metric checks with voice alert support.
Zero subprocess calls on all platforms — uses ctypes/pynvml/psutil/wmi only.
"""
import ctypes
import platform
import time

import psutil

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

DEFAULT_THRESHOLDS = {
    "cpu":  90.0,
    "ram":  90.0,
    "temp": 85.0,
    "gpu":  95.0,
}

_COOLDOWN   = 300
_CPU_STREAK = 3

# ── NVML DLL cache (Windows: nvml.dll, Linux: libnvidia-ml.so.1) ─────────────
_nvml_lib: object = None
_nvml_ok:  object = None   # None=untested  True=works  False=unavailable


def _nvml_gpu() -> float:
    """GPU utilisation via NVML — zero subprocess on all platforms."""
    global _nvml_lib, _nvml_ok
    if _nvml_ok is False:
        return -1.0
    try:
        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            if _OS == "Windows":
                candidates = ("nvml", r"C:\Windows\System32\nvml.dll")
                _load = ctypes.WinDLL
            else:
                candidates = (
                    "libnvidia-ml.so.1",
                    "libnvidia-ml.so",
                    "libnvidia-ml.dylib",
                )
                _load = ctypes.CDLL
            for name in candidates:
                try:
                    lib = _load(name)
                    lib.nvmlInit_v2()
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            _nvml_ok = False
            return -1.0

        dev = ctypes.c_void_p()
        _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        u = _Util()
        _nvml_lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u))
        _nvml_ok = True
        return float(u.gpu)
    except Exception:
        _nvml_ok = False
        return -1.0


_PYNVML = None  # None = untested · False = unavailable (optional) · module = ready


def _get_gpu_usage() -> float:
    # pynvml — subprocess-free, works everywhere if installed. It's OPTIONAL; when
    # absent we fall through to the ctypes probe silently. Cache the import result
    # so a missing dep doesn't spam this ~2s-poll loop (that was the regression).
    global _PYNVML
    if _PYNVML is None:
        try:
            import pynvml  # type: ignore
            _PYNVML = pynvml
        except Exception:
            _PYNVML = False  # optional dependency not installed — stay quiet
    if _PYNVML:
        try:
            _PYNVML.nvmlInit()
            h = _PYNVML.nvmlDeviceGetHandleByIndex(0)
            return float(_PYNVML.nvmlDeviceGetUtilizationRates(h).gpu)
        except Exception:
            pass  # nvml runtime error — fall through to the ctypes fallback
    return _nvml_gpu()


def _get_cpu_temp() -> float:
    # psutil — works on Linux; occasionally Windows with proper drivers
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
        print(f"[system_monitor.py] Non-fatal error at line 96: {_e}")

    # Windows: wmi module (pure Python COM, zero subprocess)
    if _OS == "Windows":
        try:
            import wmi  # type: ignore
            w = wmi.WMI(namespace="root/wmi")
            tz = w.MSAcpi_ThermalZoneTemperature()
            if tz:
                return (tz[0].CurrentTemperature / 10.0) - 273.15
        except Exception as _e:
            print(f"[system_monitor.py] Non-fatal error at line 107: {_e}")

    return -1.0


import threading

_metrics_lock = threading.Lock()
_cached_metrics = None
_last_sampled_time = 0.0
_sampler_started = False

def _metrics_sampler_loop():
    global _cached_metrics, _last_sampled_time
    # Initialize cpu_percent calculation (first call establishes baseline)
    psutil.cpu_percent(interval=None)
    while True:
        try:
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory()
            temp = _get_cpu_temp()
            gpu = _get_gpu_usage()
            pids_count = len(psutil.pids())
            boot_time = psutil.boot_time()
            
            with _metrics_lock:
                _cached_metrics = {
                    "cpu_percent": cpu,
                    "ram_percent": ram.percent,
                    "ram_used_gb": ram.used / 1024 ** 3,
                    "ram_total_gb": ram.total / 1024 ** 3,
                    "cpu_temp_c": temp,
                    "gpu_percent": gpu,
                    "boot_time": boot_time,
                    "process_count": pids_count,
                }
                _last_sampled_time = time.monotonic()
        except Exception as _e:
            print(f"[system_monitor.py] Non-fatal error at line 145: {_e}")
        time.sleep(0.5)

def _ensure_sampler_started():
    global _sampler_started
    if not _sampler_started:
        with _metrics_lock:
            if not _sampler_started:
                t = threading.Thread(target=_metrics_sampler_loop, name="AethelarkMetricsSampler", daemon=True)
                t.start()
                _sampler_started = True

def get_system_status() -> dict:
    """Snapshot of current system metrics for the system_status tool (retrieved from background cache)."""
    _ensure_sampler_started()
    
    with _metrics_lock:
        metrics = _cached_metrics
        sampled_time = _last_sampled_time

    if metrics is None:
        # Cold start fallback (not sampled yet)
        cpu = psutil.cpu_percent(interval=None)
        ram = psutil.virtual_memory()
        temp = _get_cpu_temp()
        gpu = _get_gpu_usage()
        boot_time = psutil.boot_time()
        uptime_secs = time.time() - boot_time
        uptime_h = int(uptime_secs // 3600)
        uptime_m = int((uptime_secs % 3600) // 60)
        return {
            "cpu_percent": round(cpu, 1),
            "ram_percent": round(ram.percent, 1),
            "ram_used_gb": round(ram.used / 1024 ** 3, 1),
            "ram_total_gb": round(ram.total / 1024 ** 3, 1),
            "cpu_temp_c": round(temp, 1) if temp > 0 else None,
            "gpu_percent": round(gpu, 1) if gpu >= 0 else None,
            "uptime": f"{uptime_h}h {uptime_m}m",
            "process_count": len(psutil.pids()),
            "age_ms": 0,
            "gpu_available": gpu >= 0,
            "temp_available": temp > 0,
        }

    age_ms = int((time.monotonic() - sampled_time) * 1000)
    uptime_secs = time.time() - metrics["boot_time"]
    uptime_h = int(uptime_secs // 3600)
    uptime_m = int((uptime_secs % 3600) // 60)
    
    temp = metrics["cpu_temp_c"]
    gpu = metrics["gpu_percent"]

    return {
        "cpu_percent": round(metrics["cpu_percent"], 1),
        "ram_percent": round(metrics["ram_percent"], 1),
        "ram_used_gb": round(metrics["ram_used_gb"], 1),
        "ram_total_gb": round(metrics["ram_total_gb"], 1),
        "cpu_temp_c": round(temp, 1) if temp > 0 else None,
        "gpu_percent": round(gpu, 1) if gpu >= 0 else None,
        "uptime": f"{uptime_h}h {uptime_m}m",
        "process_count": metrics["process_count"],
        "age_ms": age_ms,
        "gpu_available": gpu >= 0,
        "temp_available": temp > 0,
    }


class SystemMonitor:
    """
    Stateful monitor — cooldown state persists across session reconnections.
    Call check() periodically; returns a [SYSTEM_ALERT] string or None.
    """

    def __init__(self, thresholds: dict | None = None):
        self.thresholds   = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        self._last_alert: dict[str, float] = {}
        self._cpu_streak  = 0

    def _can_alert(self, key: str) -> bool:
        return (time.monotonic() - self._last_alert.get(key, 0)) > _COOLDOWN

    def _record(self, key: str):
        self._last_alert[key] = time.monotonic()

    def check(self) -> str | None:
        _ensure_sampler_started()
        
        with _metrics_lock:
            metrics = _cached_metrics
            
        if metrics is None:
            return None

        cpu = metrics["cpu_percent"]
        ram = metrics["ram_percent"]
        temp = metrics["cpu_temp_c"]
        gpu = metrics["gpu_percent"]

        alerts: list[str] = []

        if cpu >= self.thresholds["cpu"]:
            self._cpu_streak += 1
            if self._cpu_streak >= _CPU_STREAK and self._can_alert("cpu"):
                alerts.append(
                    f"[SYSTEM_ALERT] CPU usage has been critically high ({cpu:.0f}%) "
                    "for several seconds. Warn the user in their language and suggest "
                    "closing heavy applications."
                )
                self._record("cpu")
                self._cpu_streak = 0
        else:
            self._cpu_streak = 0

        if ram >= self.thresholds["ram"] and self._can_alert("ram"):
            alerts.append(
                f"[SYSTEM_ALERT] RAM is at {ram:.0f}% — nearly exhausted. "
                "Warn the user in their language and suggest freeing memory."
            )
            self._record("ram")

        if temp > 0 and temp >= self.thresholds["temp"] and self._can_alert("temp"):
            alerts.append(
                f"[SYSTEM_ALERT] CPU temperature is {temp:.0f}°C — above the safe limit. "
                "Warn the user in their language and advise reducing system load "
                "or checking cooling."
            )
            self._record("temp")

        if gpu >= 0 and gpu >= self.thresholds["gpu"] and self._can_alert("gpu"):
            alerts.append(
                f"[SYSTEM_ALERT] GPU load is at {gpu:.0f}%. "
                "Briefly inform the user in their language."
            )
            self._record("gpu")

        return " ".join(alerts) if alerts else None
