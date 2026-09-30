"""Machine detection + local-brain recommendation for onboarding.

The eagle detects the user's hardware so the onboarding can (a) show honest
specs and (b) recommend a local model that fits — while deliberately leaving
headroom, because the local brain is ALSO the swarm manager: we must not eat all
the VRAM/RAM just running the eagle, or nothing is left for Chrome tabs, tools,
and the agent swarm it orchestrates.

Everything degrades gracefully: any probe that fails returns a safe default
rather than raising, so onboarding never breaks on an exotic machine.
"""
from __future__ import annotations

import platform
import shutil
import subprocess


def _try(fn, default):
    try:
        return fn()
    except Exception:
        return default


def _total_ram_gb() -> float:
    def _psutil():
        import psutil
        return round(psutil.virtual_memory().total / (1024 ** 3), 1)
    return _try(_psutil, 0.0)


def _cpu() -> dict:
    def _probe():
        import psutil
        name = platform.processor() or platform.machine() or "CPU"
        # On Linux platform.processor() is often blank — read model name.
        if platform.system() == "Linux" and (not name or name == platform.machine()):
            try:
                with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
                    for line in f:
                        if line.lower().startswith("model name"):
                            name = line.split(":", 1)[1].strip()
                            break
            except Exception as _e:
                print(f"[machine_profile.py] Non-fatal error at line 45: {_e}")
        return {
            "name": name,
            "cores": psutil.cpu_count(logical=False) or psutil.cpu_count() or 0,
            "threads": psutil.cpu_count(logical=True) or 0,
        }
    return _try(_probe, {"name": platform.processor() or "CPU", "cores": 0, "threads": 0})


def _gpu() -> dict:
    """Best-effort GPU + VRAM. NVIDIA via nvidia-smi; otherwise report integrated
    / unknown. VRAM is the number that actually gates local-model choice."""
    # NVIDIA
    if shutil.which("nvidia-smi"):
        def _nvidia():
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=6,
            ).stdout.strip().splitlines()
            if out:
                name, mem = [p.strip() for p in out[0].split(",")[:2]]
                return {"name": name, "vram_gb": round(float(mem) / 1024, 1),
                        "kind": "discrete"}
            raise RuntimeError("no nvidia output")
        got = _try(_nvidia, None)
        if got:
            return got

    # Apple Silicon — unified memory acts as VRAM
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return {"name": "Apple Silicon (unified)", "vram_gb": 0.0, "kind": "unified"}

    # Linux: try lspci for a name (no reliable VRAM without vendor tools)
    if platform.system() == "Linux" and shutil.which("lspci"):
        def _lspci():
            out = subprocess.run(["lspci"], capture_output=True, text=True, timeout=6).stdout
            for line in out.splitlines():
                low = line.lower()
                if "vga" in low or "3d controller" in low:
                    return {"name": line.split(":", 2)[-1].strip(),
                            "vram_gb": 0.0, "kind": "integrated"}
            raise RuntimeError("no vga line")
        got = _try(_lspci, None)
        if got:
            return got

    return {"name": "Integrated / Unknown", "vram_gb": 0.0, "kind": "unknown"}


# Recommendation tiers. `budget_gb` is the memory the brain may use — we always
# leave headroom for the swarm + the user's own apps (the offset the vision asks
# for). For unified/integrated machines we size off RAM; for discrete off VRAM.
_TIERS = [
    (48, "qwen2.5:32b",  "Flagship local brain — near-frontier reasoning."),
    (24, "qwen2.5:14b",  "Strong local brain — great for daily driving + light swarm."),
    (12, "llama3.1:8b",  "Balanced local brain — solid everyday performance."),
    (6,  "llama3.2:3b",  "Light local brain — fast, runs comfortably alongside your apps."),
    (0,  "qwen2.5:1.5b", "Minimal local brain — best for very constrained machines."),
]


def _recommend(ram_gb: float, gpu: dict) -> dict:
    vram = gpu.get("vram_gb", 0.0)
    kind = gpu.get("kind", "unknown")
    # Discrete NVIDIA: size off VRAM. Unified/integrated: size off system RAM,
    # since the model runs in shared memory. Apply the headroom offset (~40%)
    # so the eagle never starves the machine it's supposed to be helping.
    if kind == "discrete" and vram > 0:
        budget = vram * 0.75
    else:
        budget = ram_gb * 0.55
    for floor, model, note in _TIERS:
        if budget >= floor:
            return {"model": model, "note": note,
                    "budget_gb": round(budget, 1),
                    "basis": "vram" if kind == "discrete" and vram > 0 else "ram"}
    return {"model": "qwen2.5:1.5b", "note": _TIERS[-1][2],
            "budget_gb": round(budget, 1), "basis": "ram"}


def detect_machine() -> dict:
    """Full machine profile for the onboarding brain-choice screen."""
    ram = _total_ram_gb()
    cpu = _cpu()
    gpu = _gpu()
    rec = _recommend(ram, gpu)

    # Can the machine realistically run a *useful* local brain? Drives whether
    # onboarding presents Local as "recommended" or gently steers toward API.
    local_capable = rec["budget_gb"] >= 6

    os_name = {"Windows": "Windows", "Darwin": "macOS", "Linux": "Linux"}.get(
        platform.system(), platform.system())

    return {
        "os": os_name,
        "os_detail": platform.platform(),
        "cpu": cpu,
        "ram_gb": ram,
        "gpu": gpu,
        "recommended": rec,
        "local_capable": local_capable,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(detect_machine(), indent=2))
