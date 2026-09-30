"""What a module says when nobody asked it anything.

A module answers a question because someone spoke. It also *notices* things —
a first layer that adhered, a spool about to run out, a print that has cooled
enough to lift off the plate. Those have no question behind them, and until
now the eagle had no way to hear them.

Both shipped modules produce them and neither has ever reached a screen:

    a3d     writes a3d_dynamic_island.json via os.replace(), carrying
            eight prioritised events. Built exactly to Pillar 4 of the module
            standard.
    atrade  declares [events] with a listener command in its manifest.
    host    parsed no declaration, started no listener, read no file.

Three breaks, not one.

**The file is the interface.** It is what Pillar 4 specifies, a3d already
implements it, and one slot rather than a queue is the *correct* shape for a
surface that shows one thing and decays — a backlog of stale island cards is
not something anyone wants. Modules prioritise before writing, so the host's
job is to read, order, and hand over.

Deliberately free of Qt and of any timer. This decides *what* was said; the
window layer decides *when* to look. That keeps it testable without a display.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AmbientEvent:
    """One thing a module said without being asked."""

    module: str
    event: str
    payload: dict[str, Any]
    priority: int


#: The fallback when a module ranks nothing at all.
NEUTRAL_PRIORITY = 50


class AmbientWatcher:
    """Reads the slot each module writes, and reports what *changed*.

    The slot is a state file, not a queue: a module rewrites it on every tick
    of its own loop, so most writes carry the same news as the last one. a3d
    rewrites `printer_error` every eight seconds for as long as a printer is
    switched off, and the only field that moves is `timestamp`.

    So sameness is judged on what a person would see, not on bytes. Two
    payloads agreeing on every rendered field say the same thing however much
    machinery differs underneath, and the second one is silence. A printer
    that is off is announced once — the island is not a klaxon. When the
    rendered text does move (`print_progress` counting up), that is genuinely
    new and is passed on.

    `poll()` is cheap and safe to call often. Nothing that fails to parse is
    reported at all: a module writing a truncated file must not put an empty
    card on screen, and must certainly not take the island down.
    """

    #: The fields the island actually draws. Deliberately excludes `timestamp`
    #: and `telemetry` — a clock ticking is not an event, and a temperature
    #: wobbling by 0.1° is not something to interrupt anyone about.
    _RENDERED = ("event", "icon", "title", "detail", "badge", "color",
                 "activity", "alert")

    def __init__(self, files: dict[str, Path],
                 priorities: dict[str, dict[str, int]] | None = None) -> None:
        #: module key -> the slot it writes. Keyed by manifest key, not by
        #: whatever the payload calls itself: a3d's payloads say
        #: `"module": "3d"` while the island renders by manifest key, so the
        #: file an event came out of is what decides who said it.
        self._files = {key: Path(path) for key, path in files.items()}
        #: module key -> its own ranking of its own events, straight off the
        #: manifest. Absent means that module ranked nothing, and everything it
        #: says lands at NEUTRAL_PRIORITY.
        self._priorities = {k: dict(v) for k, v in (priorities or {}).items()}
        self._seen: dict[str, tuple[str, ...]] = {}
        self.heard: dict[str, dict] = {}
        self._mtimes: dict[str, int] = {}

    def poll(self) -> list[AmbientEvent]:
        """Everything new said since the last call, most important first."""
        found: list[AmbientEvent] = []
        self.heard = {}

        for key, path in self._files.items():
            try:
                raw = path.read_bytes()
                mtime = path.stat().st_mtime_ns
            except OSError:
                continue        # not written yet, gone, or replaced mid-read

            try:
                payload = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                # Half-written or corrupt. The module writes atomically, so this
                # should not happen — and if it does, silence beats a blank card.
                continue
            if not isinstance(payload, dict):
                continue
            if self._mtimes.get(key) != mtime:
                self._mtimes[key] = mtime
                self.heard[key] = payload

            name = str(payload.get("event") or "").strip()
            if not name:
                # No event name is no event. A payload that cannot say what
                # happened has nothing the island can render or decay.
                continue

            identity = self._identity(key, payload)
            if self._seen.get(key) == identity:
                continue        # the same news, told again
            self._seen[key] = identity

            found.append(AmbientEvent(
                module=key,
                event=name,
                payload=payload,
                priority=self._priorities.get(key, {}).get(
                    name, NEUTRAL_PRIORITY),
            ))

        # Highest priority first. A printer error outranks a progress tick, and
        # the caller shows the head of this list.
        found.sort(key=lambda e: e.priority, reverse=True)
        return found

    @classmethod
    def _identity(cls, key: str, payload: dict) -> tuple[str, ...]:
        """What this payload would say to a person, as a comparable value."""
        return (key, *(str(payload.get(f, "")) for f in cls._RENDERED))

    def forget(self, module: str) -> None:
        """Drop what was last seen for one module, so its slot reads fresh.

        Used when a listener is restarted: the file it left behind is the last
        thing the old process said, and that is worth showing again rather than
        swallowing as a repeat of itself.
        """
        self._seen.pop(module, None)


class ListenerSupervisor:
    """Keeps each module's listener alive, and does not let one become a swarm.

    A declaration is not a producer. `atrade listen` and `a3d listen` both
    exist and neither runs on its own, so the host starts them — once at
    startup, restarted if they fall over, killed when the eagle exits.

    Restarts are capped. A module crashing in a loop is a bug to report, not a
    thing to keep respawning: unsupervised children on this machine have
    already produced orphaned processes and an out-of-memory kill, and a
    listener that cannot stay up is better off silent and complained about.

    No thread and no timer of its own. `ensure_running()` is called by whatever
    is already ticking, which keeps the lifetime visible to the caller instead
    of hidden in here.
    """

    #: Restarts allowed per module before it is left down for this session.
    MAX_RESTARTS = 3

    def __init__(self, specs: dict[str, list[str]], spawn=None) -> None:
        self._specs = {k: list(v) for k, v in specs.items() if v}
        self._spawn = spawn or self._default_spawn
        self._procs: dict[str, Any] = {}
        self._restarts: dict[str, int] = {}
        self._stopped = False

    @staticmethod
    def _default_spawn(argv: list[str]):
        import subprocess
        # Its stdout is not the interface — the file is — so it is discarded
        # rather than piped. An unread pipe fills and blocks the child, which
        # is a hang that looks like a module with nothing to say.
        return subprocess.Popen(
            argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)

    def sweep_orphans(self) -> list[int]:
        """Kill listeners left behind by an eagle that is no longer running.

        Children are spawned with `start_new_session=True`, so they survive
        their parent by design -- which is right while the eagle is alive and
        wrong once it is not. `stop_all` only runs on a clean exit; a crash, an
        OOM kill, or `kill -9` leaves the listener running forever, and the
        next launch starts another one beside it.

        Measured on this machine, 2026-09-05: 17 `a3d listen` and 17 `atrade
        listen` processes were live at once, one pair per launch. That is not
        only untidy. A printer's SDCP websocket accepts a small number of
        concurrent clients, so seventeen listeners exhausted it and every
        telemetry read failed -- which the module reported as DISCONNECTED and
        the island showed as "CC1 UNREACHABLE", first thing, every launch. With
        the orphans killed the same printer answered IDLE five times out of
        five.

        Matched on the exact argv this host would spawn, never on a substring:
        `pkill -f "a3d listen"` also matches the shell that typed it, which was
        demonstrated the hard way. Only this user's own processes, and never
        one of ours.
        """
        import os

        wanted = {tuple(argv) for argv in self._specs.values()}
        if not wanted:
            return []

        mine = {proc.pid for proc in self._procs.values() if proc is not None}
        killed: list[int] = []
        try:
            import psutil
        except ImportError:
            return killed

        uid = os.getuid()
        for proc in psutil.process_iter(["pid", "cmdline", "uids"]):
            try:
                pid = proc.info["pid"]
                if pid in mine or pid == os.getpid():
                    continue
                uids = proc.info.get("uids")
                if uids is not None and uids.real != uid:
                    continue
                cmdline = tuple(proc.info.get("cmdline") or ())
                # SUFFIX, not equality. Measured 2026-09-07: the sweeper looked
                # for ('/home/.../a3d', 'listen') while the live process reads
                #     ('/usr/bin/python3', '/home/.../a3d', 'listen')
                # because a console script is exec'd through its interpreter.
                # The equality test therefore never matched once, and ten
                # orphans were alive on this machine — one pair per launch —
                # with the code that exists to prevent exactly that reporting
                # nothing to sweep.
                #
                # Still not a substring test. The comment below is right that
                # `pkill -f "a3d listen"` also kills the shell that typed it;
                # a shell's argv is ('/bin/bash', '-c', '<one long string>'),
                # whose tail is that whole string and never equals 'listen'.
                # Matching whole argv elements from the end survives the
                # interpreter prefix and still cannot catch a bystander.
                if not any(cmdline[-len(w):] == w for w in wanted
                           if len(cmdline) >= len(w)):
                    continue
                proc.terminate()
                killed.append(pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if killed:
            gone, alive = psutil.wait_procs(
                [p for p in psutil.process_iter() if p.pid in set(killed)],
                timeout=3)
            for proc in alive:
                try:
                    proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            print(f"[ambient] cleared {len(killed)} listener(s) left by an "
                  f"eagle that is no longer running")
        return killed

    def ensure_running(self) -> list[str]:
        """Start whatever should be running and is not. Returns what it started."""
        if self._stopped:
            return []
        started: list[str] = []
        for key, argv in self._specs.items():
            proc = self._procs.get(key)
            if proc is not None and proc.poll() is None:
                continue                                  # alive
            if proc is not None:
                self._restarts[key] = self._restarts.get(key, 0) + 1
                if self._restarts[key] > self.MAX_RESTARTS:
                    self._procs.pop(key, None)
                    self._specs.pop(key, None)
                    print(f"[ambient] {key} listener died "
                          f"{self.MAX_RESTARTS} times; leaving it down")
                    return started
            try:
                self._procs[key] = self._spawn(argv)
            except Exception as e:
                # A missing binary is a broken install, not a reason to stop
                # the eagle starting.
                print(f"[ambient] could not start {key} listener: {e}")
                continue
            started.append(key)
        return started

    def stop_all(self) -> None:
        """Kill every child. Called on the way out; safe to call twice."""
        self._stopped = True
        for key, proc in list(self._procs.items()):
            try:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=3)
            except Exception as e:
                print(f"[ambient] {key} listener would not stop: {e}")
        self._procs.clear()


def listener_specs(manifests, resolved: dict[str, str]) -> dict[str, list[str]]:
    """Turn `listener = "atrade listen"` into an argv the host can run.

    The first word names the module's own binary, and it is resolved the same
    way `invoke` resolves it rather than trusted to PATH — a module installed
    inside its own directory is not on anyone's PATH, and guessing is how a
    listener silently never starts.
    """
    specs: dict[str, list[str]] = {}
    for manifest in manifests:
        events = getattr(manifest, "events", None)
        if not events or not events.listener:
            continue
        words = events.listener.split()
        binary = resolved.get(manifest.key)
        if not binary:
            print(f"[ambient] {manifest.key} declares a listener but its "
                  f"binary is not installed")
            continue
        specs[manifest.key] = [str(binary), *words[1:]]
    return specs
