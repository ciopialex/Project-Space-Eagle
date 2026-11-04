"""Run the eagle headless in a sandbox that cannot hurt the machine, and talk to it.

    .venv/bin/python tools/sandbox.py run --say "What time is it?"
    .venv/bin/python tools/sandbox.py run --seconds 300 --mic-wav hello.wav
    .venv/bin/python tools/sandbox.py say "Open the weather"     # into a running sandbox

What the sandbox is:
- Its own HOME, data dir, virtual screen (Xvfb) and D-Bus. Only the voice settings
  and the Gemini key are copied from the real config, and the copy is deleted at exit.
- Fake audio at real-time pace. The mic plays silence, or a WAV once; the speaker
  writes what the eagle says to speaker.wav. Every other way of opening a real audio
  device raises.
- Three stops on memory: a watchdog inside the eagle writes report.txt (what holds the
  memory, every thread's CPU, Python and native stacks) and exits at --limit-mb; the
  monitor here kills the run at limit + 1200 MB; the kernel caps it at limit + 1500 MB
  with no swap.

Gemini is real, so a run spends the key's quota like a normal session.
"""
import argparse
import asyncio
import collections
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = Path(os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir())) / "eagle-sandbox.json"
CONFIG_KEYS = ("gemini_api_key", "address_style", "assistant_name", "brain_mode",
               "brain_provider", "default_browser", "os_system")
NOISE = re.compile(r"dbus-daemon|portal|SpiRegistry|keyring|SSH_AUTH|fusermount|fuse init|libEGL")


# ---------------------------------------------------------------- inside the eagle

def _install_fake_audio(out: Path, wav: str | None, wav_at: float, unpaced: bool,
                        on_mic_open) -> None:
    import numpy as np
    import sounddevice as sd
    import wave

    clip = None
    if wav:
        with wave.open(wav, "rb") as w:
            if w.getsampwidth() != 2 or w.getnchannels() != 1:
                raise SystemExit(f"{wav}: needs 16-bit mono")
            clip = (w.getframerate(), np.frombuffer(w.readframes(w.getnframes()), "<i2"))

    class FakeInputStream:
        def __init__(self, samplerate=16000, blocksize=0, channels=1, dtype="int16",
                     callback=None, **_):
            self.samplerate, self.channels = float(samplerate), channels
            self.blocksize = blocksize or 1024
            self.latency, self.active = 0.02, False
            self._callback, self._stop = callback, threading.Event()
            self._played = 0
            self._clip = None
            if clip:
                rate, pcm = clip
                n = int(len(pcm) * self.samplerate / rate)
                self._clip = np.interp(np.linspace(0, len(pcm) - 1, n),
                                       np.arange(len(pcm)), pcm).astype("<i2")
            self._clip_from = int(wav_at * self.samplerate)

        def _block(self, frames):
            block = np.zeros(frames, "<i2")
            if self._clip is not None:
                start = self._played - self._clip_from
                lo, hi = max(start, 0), min(start + frames, len(self._clip))
                if hi > lo:
                    block[lo - start:hi - start] = self._clip[lo:hi]
            self._played += frames
            return block.reshape(-1, self.channels)

        def _pace(self, frames):
            if unpaced:
                return
            self._due += frames / self.samplerate
            wait = self._due - time.monotonic()
            if wait > 0:
                time.sleep(wait)

        def start(self):
            self.active, self._due = True, time.monotonic()
            on_mic_open()
            if self._callback:
                threading.Thread(target=self._run, daemon=True, name="sandbox-mic").start()

        def _run(self):
            while not self._stop.is_set():
                self._pace(self.blocksize)
                if not self._stop.is_set():
                    self._callback(self._block(self.blocksize), self.blocksize, None, 0)

        def read(self, frames):
            self._pace(frames)
            return self._block(frames), False

        def stop(self):
            self._stop.set()
            self.active = False

        close = abort = stop

        def __enter__(self):
            self.start()
            return self

        def __exit__(self, *_):
            self.stop()

    speakers = iter(f"speaker{'' if n == 1 else f'-{n}'}.wav" for n in range(1, 100))

    class FakeRawOutputStream:
        def __init__(self, samplerate=24000, channels=1, dtype="int16", blocksize=0, **_):
            self.samplerate, self.channels = float(samplerate), channels
            self.latency, self.active, self._due = 0.05, False, time.monotonic()
            self._wav = wave.open(str(out / next(speakers)), "wb")
            self._wav.setnchannels(channels)
            self._wav.setsampwidth(2)
            self._wav.setframerate(int(samplerate))
            self._lock = threading.Lock()

        def start(self):
            self.active, self._due = True, time.monotonic()

        def write(self, data):
            data = bytes(data)
            with self._lock:
                self._wav.writeframes(data)
            self._due = max(self._due, time.monotonic()) + len(data) / 2 / self.channels / self.samplerate
            wait = self._due - time.monotonic() - self.latency
            if wait > 0:
                time.sleep(wait)
            return False

        def stop(self):
            self.active = False

        abort = stop

        def close(self):
            self.stop()
            with self._lock:
                self._wav.close()

        def __enter__(self):
            self.start()
            return self

        def __exit__(self, *_):
            self.close()

    def blocked(*_, **__):
        raise RuntimeError("sandbox: this audio call would open a real device")

    fake_device = {"name": "sandbox", "index": 0, "max_input_channels": 1,
              "max_output_channels": 1, "default_samplerate": 16000.0}
    sd.InputStream, sd.RawOutputStream = FakeInputStream, FakeRawOutputStream
    for name in ("OutputStream", "RawInputStream", "Stream", "RawStream", "play", "rec", "playrec"):
        setattr(sd, name, blocked)
    sd.query_devices = lambda device=None, kind=None: (dict(fake_device) if kind or device is not None
                                                       else [dict(fake_device)])


def _rss_mb() -> int:
    return int(open("/proc/self/statm").read().split()[1]) * os.sysconf("SC_PAGE_SIZE") >> 20


def _smaps() -> collections.Counter:
    sizes, name = collections.Counter(), None
    for line in open("/proc/self/smaps"):
        if re.match(r"^[0-9a-f]+-[0-9a-f]+ ", line):
            parts = line.split()
            name = parts[5] if len(parts) > 5 else "[anon mmap]"
        elif line.startswith("Rss:"):
            sizes[name] += int(line.split()[1])
    return sizes


def _thread_cpu() -> dict:
    out = {}
    for tid in os.listdir("/proc/self/task"):
        try:
            stat = open(f"/proc/self/task/{tid}/stat").read()
        except OSError:
            continue
        comm = stat[stat.index("(") + 1:stat.rindex(")")]
        fields = stat[stat.rindex(")") + 2:].split()
        out[tid] = (comm, int(fields[11]) + int(fields[12]))
    return out


def _report(out: Path, why: str, base: dict) -> None:
    import asyncio as _asyncio
    import gc
    with open(out / "report.txt", "w", buffering=1) as f:
        f.write(f"STOPPED: {why}. rss={_rss_mb()}MB, rss when the mic opened={base.get('rss', '?')}MB\n")
        f.write("\n== CPU per thread since the mic opened (clock ticks) ==\n")
        before = base.get("cpu", {})
        now = _thread_cpu()
        for tid, (comm, ticks) in sorted(now.items(), key=lambda kv: before.get(kv[0], ("", 0))[1] - kv[1][1])[:12]:
            f.write(f"{ticks - before.get(tid, ('', 0))[1]:>8}  {comm}  tid={tid}\n")
        f.write("\n== memory growth by region since the mic opened (MB) ==\n")
        was, grown = base.get("smaps", collections.Counter()), _smaps()
        for name, kb in sorted(((k, grown[k] - was.get(k, 0)) for k in grown), key=lambda kv: -kv[1])[:8]:
            f.write(f"{kb >> 10:>8}  {name}\n")
        f.write("\n== Python stacks ==\n")
        for tid, frame in sys._current_frames().items():
            f.write(f"--- {tid}\n" + "".join(traceback.format_stack(frame)[-8:]))
        objs = gc.get_objects()
        f.write("\n== live Python objects ==\n")
        for name, n in collections.Counter(type(o).__name__ for o in objs).most_common(12):
            f.write(f"{n:>10}  {name}\n")
        for o in objs:
            if isinstance(o, _asyncio.BaseEventLoop):
                ready = list(o._ready)
                f.write(f"\nasyncio ready queue: {len(ready)} waiting, first {[repr(h)[:120] for h in ready[:2]]}\n")
        if shutil.which("gdb"):
            f.write("\nnative stacks: native_stacks.txt\n")
            with open(out / "native_stacks.txt", "w") as g:
                try:
                    subprocess.run(["gdb", "-p", str(os.getpid()), "-batch", "-nx",
                                    "-ex", "set pagination off", "-ex", "info threads",
                                    "-ex", "thread apply all bt 25"],
                                   stdout=g, stderr=subprocess.STDOUT, timeout=120)
                except subprocess.TimeoutExpired:
                    g.write("gdb timed out\n")
        f.write("\nDONE\n")


def cmd_child() -> None:
    out = Path(os.environ["EAGLE_SANDBOX_DIR"])
    limit = int(os.environ["EAGLE_SANDBOX_LIMIT_MB"])
    base: dict = {}
    import ctypes
    ctypes.CDLL(None).prctl(0x59616D61, ctypes.c_ulong(2**64 - 1), 0, 0, 0)  # PR_SET_PTRACER_ANY, for gdb

    def mic_opened():
        if not base:
            base.update(rss=_rss_mb(), cpu=_thread_cpu(), smaps=_smaps(), at=time.monotonic())
            (out / "mic_open").write_text(str(base["rss"]))

    _install_fake_audio(out, os.environ.get("EAGLE_SANDBOX_MIC_WAV") or None,
                        float(os.environ.get("EAGLE_SANDBOX_MIC_WAV_AT", "3")),
                        os.environ.get("EAGLE_SANDBOX_MIC_UNPACED") == "1", mic_opened)

    def watchdog():
        with open(out / "memory.txt", "w", buffering=1) as curve:
            started = time.monotonic()
            while True:
                rss = _rss_mb()
                curve.write(f"{time.monotonic() - started:7.1f}s rss={rss}MB\n")
                if rss > limit:
                    break
                time.sleep(0.2)
        _report(out, f"rss passed {limit} MB", base)
        os._exit(99)

    threading.Thread(target=watchdog, daemon=True, name="sandbox-watchdog").start()
    sys.path[0] = str(ROOT)
    os.chdir(ROOT)
    sys.argv = ["aethelark_web.py"]
    import runpy
    runpy.run_path(str(ROOT / "aethelark_web.py"), run_name="__main__")


# ---------------------------------------------------------------- outside the eagle

async def _cdp_pages(port: int) -> dict:
    import urllib.request
    pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2))
    return {p["url"].rsplit("/", 1)[-1]: p["webSocketDebuggerUrl"] for p in pages}


async def _cdp_eval(ws, expr: str, n=[0]):
    n[0] += 1
    await ws.send(json.dumps({"id": n[0], "method": "Runtime.evaluate",
                              "params": {"expression": expr, "returnByValue": True}}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == n[0]:
            return msg.get("result", {}).get("result", {}).get("value")


async def _say(text: str, port: int, watch: float, log: Path | None) -> None:
    from websockets.asyncio.client import connect
    start = log.stat().st_size if log and log.exists() else 0
    pages = await _cdp_pages(port)
    async with connect(pages["dashboard.html"], max_size=None) as dash, \
               connect(pages["pill.html"], max_size=None) as pill:
        for _ in range(40):
            if await _cdp_eval(dash, "typeof pybridge !== 'undefined'"):
                break
            await asyncio.sleep(0.5)
        await _cdp_eval(dash, f"pybridge.send_command({json.dumps(text)})")
        print(f"\n>>> {text}")
        sent, last, left_idle = time.monotonic(), None, False
        while time.monotonic() - sent < watch:
            state = await _cdp_eval(pill, "window.island ? window.island.state : null")
            if state != last:
                print(f"  {time.monotonic() - sent:5.1f}s island: {state}")
                last, left_idle = state, left_idle or state not in ("idle", None)
            if left_idle and state == "idle" and time.monotonic() - sent > 2:
                break
            await asyncio.sleep(0.1)
    if log and log.exists():
        with open(log, errors="replace") as f:
            f.seek(start)
            for line in f.read().splitlines():
                if line.strip() and not NOISE.search(line):
                    print("  | " + line[:200])


def cmd_say(a) -> None:
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if not state:
        raise SystemExit("no sandbox is running; start one with: tools/sandbox.py run")
    asyncio.run(_say(a.text, state["port"], a.watch, Path(state["dir"]) / "eagle.log"))


def _write_config(home: Path) -> Path:
    sys.path.insert(0, str(ROOT))
    from core import user_paths
    real = json.loads(user_paths.api_keys_path().read_text())
    keep = {k: real[k] for k in CONFIG_KEYS if k in real}
    keep.update(onboarded=True, user_name="Sam")
    cfg = home / "data" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    cfg.chmod(0o700)
    path = cfg / "api_keys.json"
    path.touch(0o600)
    path.write_text(json.dumps(keep))
    return path


def _free_display() -> int:
    for n in range(97, 140):
        if not Path(f"/tmp/.X11-unix/X{n}").exists() and not Path(f"/tmp/.X{n}-lock").exists():
            return n
    raise SystemExit("no free X display number")


def cmd_run(a) -> None:
    for tool in ("Xvfb", "dbus-run-session", "systemd-run"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} is not installed")
    if STATE.exists():
        raise SystemExit(f"a sandbox may already be running ({STATE}); delete it if not")
    out = Path(a.dir).resolve() if a.dir else Path(tempfile.mkdtemp(prefix="eagle-sandbox-"))
    home = out / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / ".asoundrc").write_text("pcm.!default { type null }\n")
    key_file = _write_config(home)
    display = _free_display()
    unit = f"eagle-sandbox-{os.getpid()}"
    hard_mb, kill_mb = a.limit_mb + 1500, a.limit_mb + 1200
    env = {k: v for k, v in os.environ.items() if not k.startswith("XDG_") or k == "XDG_RUNTIME_DIR"}
    env.update(HOME=str(home), DISPLAY=f":{display}", PIPEWIRE_REMOTE="eagle-sandbox-none",
               AETHELARK_DATA_DIR=str(home / "data"), PYTHONUNBUFFERED="1",
               QTWEBENGINE_REMOTE_DEBUGGING=f"127.0.0.1:{a.port}",
               EAGLE_SANDBOX_DIR=str(out), EAGLE_SANDBOX_LIMIT_MB=str(a.limit_mb),
               EAGLE_SANDBOX_MIC_WAV=str(Path(a.mic_wav).resolve()) if a.mic_wav else "",
               EAGLE_SANDBOX_MIC_WAV_AT=str(a.mic_wav_at),
               EAGLE_SANDBOX_MIC_UNPACED="1" if a.mic_unpaced else "")
    xvfb = subprocess.Popen(["Xvfb", f":{display}", "-screen", "0", "1920x1080x24", "-nolisten", "tcp"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    log = open(out / "eagle.log", "w")
    proc = subprocess.Popen(
        ["systemd-run", "--user", "--scope", "-q", f"--unit={unit}",
         "-p", f"MemoryMax={hard_mb}M", "-p", "MemorySwapMax=0", "--",
         "timeout", "-s", "KILL", str(a.seconds + 60),
         "dbus-run-session", "--", sys.executable, str(Path(__file__).resolve()), "_child"],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    STATE.write_text(json.dumps({"dir": str(out), "port": a.port, "unit": unit}))
    print(f"sandbox: {out}  (display :{display}, DevTools 127.0.0.1:{a.port}, memory cap {hard_mb} MB)")

    def talk():
        for text in a.say:
            try:
                asyncio.run(_say(text, a.port, a.watch, out / "eagle.log"))
            except Exception as e:
                print(f"  say failed: {e}")

    outcome, peak, started, talker, cgroup, mic_at = "time is up", 0, time.monotonic(), None, None, None
    try:
        while proc.poll() is None:
            elapsed = time.monotonic() - started
            if cgroup is None:
                r = subprocess.run(["systemctl", "--user", "show", "-p", "ControlGroup", "--value",
                                    f"{unit}.scope"], capture_output=True, text=True)
                cgroup = Path("/sys/fs/cgroup" + r.stdout.strip()) if r.stdout.strip() else None
            try:
                mem = int((cgroup / "memory.current").read_text()) >> 20 if cgroup else 0
            except OSError:
                mem = 0
            peak = max(peak, mem)
            if mem > kill_mb:
                outcome = f"monitor killed it at {mem} MB"
                break
            if mic_at is None and (out / "mic_open").exists():
                mic_at = elapsed
            if talker is None and a.say and mic_at is not None and elapsed > mic_at + a.settle:
                talker = threading.Thread(target=talk, daemon=True)
                talker.start()
            if talker is not None and not talker.is_alive() and not a.stay:
                outcome = "prompts done"
                break
            if elapsed > a.seconds:
                break
            time.sleep(0.2)
        else:
            outcome = {99: "the watchdog stopped it (see report.txt)"}.get(proc.returncode,
                                                                          f"it exited ({proc.returncode})")
    except KeyboardInterrupt:
        outcome = "stopped by you"
    finally:
        subprocess.run(["systemctl", "--user", "kill", "-s", "KILL", f"{unit}.scope"],
                       capture_output=True)
        proc.wait(timeout=10)
        xvfb.terminate()
        key_file.write_bytes(b"\0" * key_file.stat().st_size)
        key_file.unlink()
        STATE.unlink(missing_ok=True)
        log.close()
    print(f"\nend: {outcome}; peak memory {peak} MB")
    for name in ("report.txt", "speaker.wav", "memory.txt", "eagle.log"):
        if (out / name).exists():
            print(f"  {out / name}")


def main() -> None:
    if sys.argv[1:2] == ["_child"]:
        return cmd_child()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="start a sandboxed eagle")
    r.add_argument("--say", action="append", default=[], help="typed prompt, sent once the mic is open")
    r.add_argument("--seconds", type=int, default=120, help="stop after this long (default 120)")
    r.add_argument("--limit-mb", type=int, default=1500, help="watchdog stops the eagle here (default 1500)")
    r.add_argument("--mic-wav", help="16-bit mono WAV the mic plays once")
    r.add_argument("--mic-wav-at", type=float, default=3.0, help="seconds after the mic opens (default 3)")
    r.add_argument("--mic-unpaced", action="store_true",
                   help="mic runs as fast as it can, like the device that froze the machine on 2026-11-03")
    r.add_argument("--settle", type=float, default=3.0, help="wait after the mic opens before prompts")
    r.add_argument("--watch", type=float, default=30.0, help="seconds to watch each prompt")
    r.add_argument("--stay", action="store_true", help="keep running after the prompts, for 'say'")
    r.add_argument("--port", type=int, default=9229)
    r.add_argument("--dir", help="where logs go (default: a new temp dir)")
    s = sub.add_parser("say", help="type into a running sandbox")
    s.add_argument("text")
    s.add_argument("--watch", type=float, default=30.0)
    a = p.parse_args()
    {"run": cmd_run, "say": cmd_say}[a.cmd](a)


if __name__ == "__main__":
    main()
