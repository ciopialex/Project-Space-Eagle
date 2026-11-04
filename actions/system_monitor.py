"""System metrics behind a cache with a TTL: a process that never asks pays nothing,
and callers get a reading at most SAMPLE_TTL old.

Temperature reads one /sys/class/hwmon/<N>/temp1_input, found once by matching
hwmon*/name against known CPU drivers (numbering changes across boots); about 750x
cheaper than sensors_temperatures(), which remains the fallback.

NVML is initialised once, lazily, and shut down at exit.

temp and gpu are None when this machine cannot tell, never a sentinel number.
"""
from __future__ import annotations

import atexit
import ctypes
import os
import platform
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

DEFAULT_THRESHOLDS = {
    "cpu":  90.0,
    "ram":  90.0,
    "temp": 85.0,
    "gpu":  95.0,
}

_COOLDOWN   = 300.0  # seconds an already-fired alert key stays quiet
_CPU_STREAK = 3      # consecutive over-threshold checks CPU needs before it alerts


@dataclass(frozen=True)
class Reading:
    """One sample of every metric. temp and gpu are None when unreadable."""
    cpu: float
    ram: float
    ram_used_gb: float
    ram_total_gb: float
    temp: float | None
    gpu: float | None
    boot_time: float
    process_count: int


_CPU_SENSOR_NAMES = frozenset(
    {"coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"}
)

_hwmon_checked = False                  # has resolution run this process?
_hwmon_temp_file: Path | None = None    # the resolved file, or None if none matched


def _resolve_hwmon() -> Path | None:
    """Find the one hwmon temp file worth reading, once: numbering follows driver
    load order and changes across boots.
    """
    global _hwmon_checked, _hwmon_temp_file
    if _hwmon_checked:
        return _hwmon_temp_file
    _hwmon_checked = True
    try:
        for name_file in sorted(Path("/sys/class/hwmon").glob("hwmon*/name")):
            if name_file.read_text().strip() in _CPU_SENSOR_NAMES:
                candidate = name_file.with_name("temp1_input")
                if candidate.exists():
                    _hwmon_temp_file = candidate
                    break
    except OSError:
        pass  # no /sys/class/hwmon at all — not Linux, or a stripped-down one
    return _hwmon_temp_file


def _cpu_temp() -> float | None:
    """CPU package temperature in Celsius, or None."""
    path = _resolve_hwmon()
    if path is not None:
        try:
            return int(path.read_text()) / 1000.0
        except (OSError, ValueError):
            pass  # sensor existed at resolve time, unreadable now — fall through

    try:
        temps = psutil.sensors_temperatures()
        for name in _CPU_SENSOR_NAMES:
            entries = temps.get(name)
            if entries:
                return entries[0].current
    except Exception:
        pass  # macOS, and some minimal Linux builds, don't implement this at all

    if _OS == "Windows":
        try:
            import wmi  # type: ignore
            zones = wmi.WMI(namespace="root/wmi").MSAcpi_ThermalZoneTemperature()
            if zones:
                return (zones[0].CurrentTemperature / 10.0) - 273.15
        except Exception:
            pass

    return None


_gpu_backend = None  # None = untried · False = unavailable · callable = ready to read


def _load_nvml_ctypes():
    """The bare NVML shared library, tried under every name it ships as."""
    if _OS == "Windows":
        loader, candidates = ctypes.WinDLL, ("nvml", r"C:\Windows\System32\nvml.dll")
    else:
        loader, candidates = ctypes.CDLL, (
            "libnvidia-ml.so.1", "libnvidia-ml.so", "libnvidia-ml.dylib",
        )
    for name in candidates:
        try:
            return loader(name)
        except OSError:
            continue
    raise OSError("no NVML library found under any known name")


def _resolve_gpu_backend() -> None:
    """Pick pynvml or the raw ctypes probe, initialise NVML once, and cache a reader
    (or False). A process that never asks never pays the cold nvmlInit().
    """
    global _gpu_backend
    if _gpu_backend is not None:
        return

    def _safe_nvml_shutdown(fn):
        try:
            fn()
        except Exception:
            pass

    try:
        import pynvml  # type: ignore
        pynvml.nvmlInit()
        atexit.register(_safe_nvml_shutdown, pynvml.nvmlShutdown)
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)

        def _read_pynvml() -> float:
            return float(pynvml.nvmlDeviceGetUtilizationRates(handle).gpu)

        _gpu_backend = _read_pynvml
        return
    except Exception:
        pass  # pynvml not installed, or no NVIDIA driver under it — try ctypes

    try:
        lib = _load_nvml_ctypes()
        lib.nvmlInit_v2()
        atexit.register(_safe_nvml_shutdown, lib.nvmlShutdown)
        device = ctypes.c_void_p()
        lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(device))

        class _Utilization(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        def _read_ctypes() -> float:
            rates = _Utilization()
            lib.nvmlDeviceGetUtilizationRates(device, ctypes.byref(rates))
            return float(rates.gpu)

        _gpu_backend = _read_ctypes
        return
    except Exception:
        pass  # no usable NVML anywhere on this machine

    _gpu_backend = False


def _gpu_percent() -> float | None:
    """GPU utilisation 0-100, or None on a machine with no usable NVML."""
    _resolve_gpu_backend()
    if not _gpu_backend:
        return None
    try:
        return _gpu_backend()
    except Exception:
        return None


def _process_count() -> int:
    """How many processes are running: count numbered /proc entries; psutil.pids()
    where /proc does not exist.
    """
    try:
        return sum(1 for entry in os.listdir("/proc") if entry.isdigit())
    except OSError:
        return len(psutil.pids())


def _sample() -> Reading:
    vm = psutil.virtual_memory()
    return Reading(
        cpu=psutil.cpu_percent(interval=None),
        ram=vm.percent,
        ram_used_gb=vm.used / 1024 ** 3,
        ram_total_gb=vm.total / 1024 ** 3,
        temp=_cpu_temp(),
        gpu=_gpu_percent(),
        boot_time=psutil.boot_time(),
        process_count=_process_count(),
    )


SAMPLE_TTL = 2.0  # seconds a Reading stays fresh enough to hand back as-is

_cache: tuple[Reading, float] | None = None
_cache_lock = threading.Lock()


def sample_reading() -> tuple[Reading, float]:
    """Sample the machine, returning (reading, age_seconds). A 2 s TTL lets close
    calls share one sample.
    """
    global _cache
    with _cache_lock:
        now = time.monotonic()
        if _cache is not None:
            reading, stamp = _cache
            age = now - stamp
            if age < SAMPLE_TTL:
                return reading, age

        reading = _sample()
        _cache = (reading, now)
        return reading, 0.0

_read_cached = sample_reading


def reset_sampler() -> None:
    """Drop the cached Reading. Tests only."""
    global _cache
    with _cache_lock:
        _cache = None


def _format_uptime(boot_time: float) -> str:
    seconds = max(0.0, time.time() - boot_time)
    hours, remainder = divmod(int(seconds), 3600)
    minutes = remainder // 60
    return f"{hours}h {minutes}m"


def get_system_status() -> dict:
    """Snapshot for the system_status tool: the 11 keys main.py reads."""
    reading, age = _read_cached()
    return {
        "cpu_percent":    round(reading.cpu, 1),
        "ram_percent":    round(reading.ram, 1),
        "ram_used_gb":    round(reading.ram_used_gb, 1),
        "ram_total_gb":   round(reading.ram_total_gb, 1),
        "cpu_temp_c":     round(reading.temp, 1) if reading.temp is not None else None,
        "gpu_percent":    round(reading.gpu, 1) if reading.gpu is not None else None,
        "uptime":         _format_uptime(reading.boot_time),
        "process_count":  reading.process_count,
        "age_ms":         int(age * 1000),
        "gpu_available":  reading.gpu is not None,
        "temp_available": reading.temp is not None,
    }


def _cpu_message(value: float) -> str:
    return (f"[SYSTEM_ALERT] CPU load has held at {value:.0f}% across three "
            "consecutive checks (about 30 seconds), not a brief spike. Tell "
            "the user in their own language and suggest closing whatever is "
            "using the most CPU.")


def _ram_message(value: float) -> str:
    return (f"[SYSTEM_ALERT] Memory is at {value:.0f}% and close to full. "
            "Tell the user in their own language and suggest freeing some up.")


def _temp_message(value: float) -> str:
    return (f"[SYSTEM_ALERT] The CPU has reached {value:.0f}\u00b0C, above the "
            "safe threshold. Tell the user in their own language and suggest "
            "easing off system load or checking that cooling is working.")


def _gpu_message(value: float) -> str:
    return (f"[SYSTEM_ALERT] GPU utilisation is at {value:.0f}%. Briefly "
            "mention it to the user in their own language.")


class SystemMonitor:
    """Threshold watcher whose cooldowns outlive any one check. check() returns the
    [SYSTEM_ALERT] strings joined by a space, or None. `clock` is injectable.
    """

    def __init__(self, thresholds: dict | None = None, *, clock=time.monotonic):
        self.thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        self._clock = clock
        self._last_alert: dict[str, float] = {}
        self._cpu_streak = 0

    def _read(self) -> Reading:
        reading, _age = _read_cached()
        return reading

    def _can_alert(self, key: str) -> bool:
        return (self._clock() - self._last_alert.get(key, 0.0)) > _COOLDOWN

    def _record(self, key: str) -> None:
        self._last_alert[key] = self._clock()

    def check(self) -> str | None:
        r = self._read()

        # CPU must stay hot across several checks before it alerts.
        cpu_hot = r.cpu >= self.thresholds["cpu"]
        self._cpu_streak = self._cpu_streak + 1 if cpu_hot else 0

        table = (
            ("cpu",  cpu_hot and self._cpu_streak >= _CPU_STREAK,             r.cpu,  _cpu_message),
            ("ram",  r.ram >= self.thresholds["ram"],                         r.ram,  _ram_message),
            ("temp", r.temp is not None and r.temp >= self.thresholds["temp"], r.temp, _temp_message),
            ("gpu",  r.gpu is not None and r.gpu >= self.thresholds["gpu"],    r.gpu,  _gpu_message),
        )

        alerts: list[str] = []
        for key, breached, value, message in table:
            if breached and self._can_alert(key):
                alerts.append(message(value))
                self._record(key)
                if key == "cpu":
                    self._cpu_streak = 0

        return " ".join(alerts) if alerts else None
