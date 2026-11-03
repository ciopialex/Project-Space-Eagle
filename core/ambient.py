"""What a module says when nobody asked it anything: a first layer that adhered, a
spool about to run out.

Each module writes its latest event to one file (one slot, not a queue: the island
shows one thing and decays). This reads, orders and hands over; the window layer
decides when to look. No Qt and no timer, so it is testable without a display.
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


# The fallback when a module ranks nothing.
NEUTRAL_PRIORITY = 50


class AmbientWatcher:
    """Reads the slot each module writes and reports what changed. Sameness is judged
    on the rendered fields, so a module rewriting the same news every few seconds
    is announced once. Unparseable files are ignored. poll() is cheap.
    """

    # The fields the island draws: not timestamp or telemetry.
    _RENDERED = ("event", "icon", "title", "detail", "badge", "color",
                 "activity", "alert")

    def __init__(self, files: dict[str, Path],
                 priorities: dict[str, dict[str, int]] | None = None) -> None:
        # Module key -> the file it writes. The file decides who said it, not the
        # payload's own name.
        self._files = {key: Path(path) for key, path in files.items()}
        # Module key -> its own event ranking from the manifest; absent means
        # NEUTRAL_PRIORITY.
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
                # Half-written or corrupt: silence beats a blank card.
                continue
            if not isinstance(payload, dict):
                continue
            if self._mtimes.get(key) != mtime:
                self._mtimes[key] = mtime
                self.heard[key] = payload

            name = str(payload.get("event") or "").strip()
            if not name:
                # No event name is no event.
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

        # Highest priority first; the caller shows the head.
        found.sort(key=lambda e: e.priority, reverse=True)
        return found

    @classmethod
    def _identity(cls, key: str, payload: dict) -> tuple[str, ...]:
        """What this payload would say to a person, as a comparable value."""
        return (key, *(str(payload.get(f, "")) for f in cls._RENDERED))

    def forget(self, module: str) -> None:
        """Forget one module's last event, so a restarted listener's leftover file is
        shown again.
        """
        self._seen.pop(module, None)


class ListenerSupervisor:
    """Keeps each module's listener alive: started at launch, restarted if it falls
    over (capped), killed when the eagle exits. No thread or timer of its own:
    ensure_running() is called by whatever is already ticking.
    """

    # Restarts allowed per module per session.
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
        # stdout is discarded: an unread pipe fills and blocks the child.
        return subprocess.Popen(
            argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)

    def sweep_orphans(self) -> list[int]:
        """Kill listeners left behind by an eagle that is no longer running. They are
        started in their own session and survive a crash; enough of them exhaust a
        printer's websocket. Matched on the exact argv this host spawns, only this
        user's processes, never our own children.
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
                # Match whole argv elements from the end: a console script runs as
                # (python, script, 'listen'), and a shell's argv never ends in 'listen'.
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
                # A missing binary is a broken install, not a reason to stop the eagle.
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
    """Turn `listener = "atrade listen"` into an argv. The first word resolves the
    way invoke resolves it, not via PATH.
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
