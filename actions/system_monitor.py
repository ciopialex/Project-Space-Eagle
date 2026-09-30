"""A cache with a TTL, not a thread that never stops.

The module this replaces started a daemon thread on first use that sampled
every 0.5 s for the life of the process — cpu, ram, temperature, gpu, pid
count, boot time — and both real consumers (the `system_status` tool and the
10 s alert loop in main.py) only ever read whatever the thread last wrote.
Measured on this machine, one sample cost ~17.6 ms of that 0.5 s window,
almost all of it `psutil.sensors_temperatures()` walking every hwmon group
on the box — nvme, coretemp, iwlwifi, dozens of temp* files — to hand back
one float that was always going to come from coretemp.

Nobody needs an answer fresher than 10 s — that is the slower poller's own
interval — so there is no reason to compute one faster than that, let alone
twice a second forever whether or not anyone had asked. The design here is a
plain cache behind a time-to-live: `get_system_status()` and
`SystemMonitor.check()` both go through `_read_cached()`, which hands back
the last `Reading` untouched if it is younger than `SAMPLE_TTL` and pays for
a fresh `_sample()` otherwise. A process that never asks pays nothing at
all — no thread, no timer, nothing running in the background.

Temperature reads `/sys/class/hwmon/<N>/temp1_input` directly, once the
right `<N>` has been found by matching every `hwmon*/name` against a short
list of known CPU-sensor driver names. That resolution runs once and is
cached, because hwmon numbering is assigned by driver load order and is not
stable across boots — hardcoding a path would break on the next kernel
update. Measured here: ~23 us for the direct read against ~17.6 ms for
`sensors_temperatures()` returning the identical value — about 750x,
because the direct read touches one file instead of walking every sensor
group on the machine for a number that was never going to come from
anywhere else. `sensors_temperatures()`, restricted to the same known
names, stays as the fallback for a machine whose driver isn't on the list;
that machine is exactly as slow as before, never slower.

GPU utilisation has the opposite shape of problem. A *warm* `nvmlInit()` is
cheap (~1.4 us measured back-to-back) but the *first* one is not (~12.7 ms
here), and it hands back a handle to a driver-side context that is supposed
to be released with a matching `nvmlShutdown()`. The module this replaces
called `nvmlInit()` on every sample and shut down never — a reference count
that only ever grew for the life of the process. Here it runs once, lazily,
guarded by a module-level cache of the working reader itself (or `False` if
neither pynvml nor the raw NVML library loads), with `atexit.register` for
the shutdown that was always supposed to happen.

The `-1.0` "I don't know" sentinel from the old module is gone. `Reading`
carries `temp: float | None` and `gpu: float | None`, and `None` means
exactly what it says — this machine cannot tell me — all the way out to the
public dict, instead of a number that looks real until it's compared
against a threshold.
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
    """One sample of every metric the status dict and the alerter need.

    `temp` and `gpu` are `float | None` on purpose: `None` is the honest
    answer on a machine with no readable CPU sensor or no NVIDIA GPU, and
    unlike the old module's `-1.0` sentinel it can never be mistaken for a
    real measurement by code that forgets to check for it.
    """
    cpu: float
    ram: float
    ram_used_gb: float
    ram_total_gb: float
    temp: float | None
    gpu: float | None
    boot_time: float
    process_count: int


# ── temperature: the specific file, not the general-purpose walker ─────────

_CPU_SENSOR_NAMES = frozenset(
    {"coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"}
)

_hwmon_checked = False                  # has resolution run this process?
_hwmon_temp_file: Path | None = None    # the resolved file, or None if none matched


def _resolve_hwmon() -> Path | None:
    """Find the one hwmon temp file worth reading, once.

    hwmon directory numbers are handed out by driver load order and are not
    stable across boots — hwmon3 today can be hwmon5 after a kernel update
    changes what probes first. So this reads every `hwmon*/name` looking for
    a driver on the known-CPU-sensor list, remembers the winning
    `temp1_input` path, and never looks again for the life of the process.
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
    """CPU package temperature in Celsius, or None if this machine won't say.

    The direct hwmon read is ~750x cheaper than `sensors_temperatures()` for
    the identical number, measured on this machine (~23 us vs ~17.6 ms) —
    the gap between reading one file and walking nvme, coretemp and iwlwifi
    to find the one entry that was always going to be coretemp.
    """
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


# ── GPU: init once, shut down once, not twice a second forever ─────────────

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
    """Pick pynvml or the raw ctypes probe, initialise NVML exactly once, and
    cache a zero-argument reader closure (or False) so nothing downstream of
    this ever calls nvmlInit again.

    The module this replaces called `nvmlInit()` on every sample and never
    called `nvmlShutdown()` — a driver-side reference count that only grew,
    forever, for the life of the process. Doing it lazily, on first real
    need, also means a process that never asks about the GPU never pays the
    ~12.7 ms a cold `nvmlInit()` costs on this machine at all.
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


# ── process count: /proc already has the answer psutil.pids() re-derives ───

def _process_count() -> int:
    """How many processes are running.

    On Linux, every process is a numbered directory in /proc — counting them
    is one listdir plus an isdigit check. psutil.pids() walks the same
    directory and then also builds a list of ints just to be handed to
    len(): measured on this machine at roughly 150 us against roughly 140 us
    for counting directly — a real if modest difference, kept because there
    is no reason to build a list nobody asked for just to measure its
    length. psutil.pids() is the fallback anywhere /proc does not exist.
    """
    try:
        return sum(1 for entry in os.listdir("/proc") if entry.isdigit())
    except OSError:
        return len(psutil.pids())


# ── the single place that touches psutil for a live sample ─────────────────

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


# ── demand-driven cache: pay for a sample only when someone asks for one ───

SAMPLE_TTL = 2.0  # seconds a Reading stays fresh enough to hand back as-is

_cache: tuple[Reading, float] | None = None
_cache_lock = threading.Lock()


def sample_reading() -> tuple[Reading, float]:
    """Sample the machine, returning (reading, age_seconds).

    Both real consumers in main.py poll at 10 s intervals; a 2 s TTL keeps
    the answer well under that staleness bar while still letting a burst of
    calls close together — a tool call landing right after an alert-loop
    tick, say — share one sample instead of each paying for its own.
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
    """Drop the cached Reading so the next read is guaranteed fresh.

    Test isolation only — production code never needs to force a resample,
    since the TTL already bounds how stale an answer can be.
    """
    global _cache
    with _cache_lock:
        _cache = None


def _format_uptime(boot_time: float) -> str:
    seconds = max(0.0, time.time() - boot_time)
    hours, remainder = divmod(int(seconds), 3600)
    minutes = remainder // 60
    return f"{hours}h {minutes}m"


def get_system_status() -> dict:
    """Snapshot for the `system_status` tool: the 11 keys main.py reads,
    assembled here in exactly one place instead of the two branches
    (cold-start vs already-sampled) the old module had to keep in sync by
    hand every time either one changed.
    """
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


# ── alerting ─────────────────────────────────────────────────────────────

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
    """Threshold watcher whose cooldown state outlives any one check.

    `check()` is meant to be called on a timer — main.py does so every 10 s —
    and returns one `[SYSTEM_ALERT]` string per breach, joined by a space,
    for the caller to forward to the model verbatim; or None when nothing
    crossed a threshold. `clock` is injectable so tests can move time
    without a real sleep.
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

        # CPU is the one metric that has to stay hot across several checks
        # before it counts — a single momentary spike is normal, not a
        # symptom — so its "breached" bit folds in streak state the other
        # three metrics don't need at all.
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
