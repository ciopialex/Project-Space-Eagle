"""End-to-end: the real app in tools/sandbox.py, driven through its own pages.

    .venv/bin/python -m pytest e2e -v

Outside tests/ on purpose: tests/conftest.py makes the process offscreen and points
it at fixture modules, and the app under test must not inherit either. These need
Xvfb, ffmpeg, the network and your Gemini key (read from your config, never
written anywhere but the sandbox, which shreds it), and they spend its quota.
On failure, the run's eagle.log and screenshots are in the printed directory.
"""
from __future__ import annotations

import itertools
import json
import math
import shutil
import subprocess
import sys
import time
import wave
from array import array
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from cdp import Eagle  # noqa: E402
from sandbox import STATE, Sandbox, real_config  # noqa: E402

_ports = itertools.count(9240)


def pytest_collection_modifyitems(config, items):
    missing = [t for t in ("Xvfb", "dbus-run-session", "systemd-run", "ffmpeg") if not shutil.which(t)]
    try:
        has_key = bool(real_config().get("gemini_api_key"))
    except Exception:
        has_key = False
    why = (f"missing {', '.join(missing)}" if missing
           else "" if has_key else "no Gemini key in your config")
    if why:
        for item in items:
            item.add_marker(pytest.mark.skip(reason=why))


@pytest.fixture(scope="session")
def gemini_key() -> str:
    return real_config()["gemini_api_key"]


@pytest.fixture(scope="session")
def say(tmp_path_factory):
    """say("text") -> a 16 kHz mono WAV of a synthetic voice saying it."""
    d = tmp_path_factory.mktemp("voice")

    def _make(text: str) -> Path:
        out = d / f"{abs(hash(text))}.wav"
        if not out.exists():
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                            "-i", f"flite=text='{text}':voice=slt", "-af", "apad=pad_dur=0.6,volume=2.0",
                            "-ar", "16000", "-ac", "1", str(out)], check=True)
        return out
    return _make


@pytest.fixture
def launch(tmp_path):
    """launch(**Sandbox options) -> (sandbox, Eagle). Everything started is torn down."""
    if STATE.exists():
        pytest.skip(f"another sandbox is running ({STATE})")
    started: list[Sandbox] = []

    def _launch(**kw) -> tuple[Sandbox, Eagle]:
        kw.setdefault("seconds", 300)
        sb = Sandbox(tmp_path / f"run{len(started) + 1}", port=next(_ports), **kw)
        sb.__enter__()
        started.append(sb)
        print(f"\nsandbox: {sb.out}")
        return sb, Eagle(sb.port, shots=sb.out / "shots")
    yield _launch
    for sb in started:
        sb.stop()


def wait_for(fn, timeout: float, what: str, every: float = 0.25):
    end = time.monotonic() + timeout
    while True:
        v = fn()
        if v:
            return v
        if time.monotonic() > end:
            raise AssertionError(f"waited {timeout:.0f}s for {what}")
        time.sleep(every)


def loud_seconds(path: Path) -> float:
    """Seconds of a WAV louder than room noise."""
    if not path.exists():
        return 0.0
    try:
        with wave.open(str(path)) as w:
            pcm, rate = array("h", w.readframes(w.getnframes())), w.getframerate()
    except (wave.Error, EOFError):
        return 0.0
    blk = max(1, rate // 20)
    loud = sum(1 for i in range(0, len(pcm) - blk + 1, blk)
               if math.sqrt(sum(x * x for x in pcm[i:i + blk]) / blk) > 300)
    return loud / 20


def spoken(sb: Sandbox) -> float:
    return sum(loud_seconds(p) for p in sb.out.glob("speaker*.wav"))


def log_lines(eagle: Eagle, speaker: str = "") -> list[str]:
    """The dashboard's conversation log; speaker is 'you', 'ae' or '' for all."""
    dash = eagle.page("dashboard.html")
    sel = ".logline" + (f".{speaker}" if speaker else "") + " .msg"
    return dash.eval(f"[...document.querySelectorAll({json.dumps(sel)})].map(e => e.innerText)") or []


def island_state(eagle: Eagle) -> str | None:
    return eagle.page("pill.html").eval("window.island ? window.island.state : null")


def connections(sb: Sandbox) -> int:
    """Live sessions Gemini has accepted so far."""
    return sb.log_path.read_text(errors="replace").count("[Aethelark] Connected.")


def log_tail(sb: Sandbox, n: int = 40) -> str:
    noise = ("dbus", "portal", "fuse", "libEGL", "DevTools", "debugging server", "bus can't")
    lines = [ln for ln in sb.log_path.read_text(errors="replace").splitlines()
             if ln.strip() and not any(w in ln for w in noise)]
    return "\n".join(lines[-n:])
