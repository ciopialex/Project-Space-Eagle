"""Who is running, on whose behalf, and how to stop all of it.

Agent sessions were tracked only inside the PTY pool, keyed by
(agent_key, directory). That is enough to talk to a session and useless for the
two questions an operator actually asks:

    "what is running right now?"      — during a mission
    "stop everything."                — when it goes wrong

Tonight a re-delegation loop left a dozen agent processes alive with no way to
enumerate or stop them short of `pkill`, which is exactly the blunt instrument
that kills Aethelark itself: `pkill claude` would have taken out the very
process trying to do the killing.

IDENTITY IS A TUPLE, NEVER A PID
--------------------------------
The OS recycles PIDs. A number that meant "the site agent" a minute ago can
mean your editor now. Every entry therefore carries (pid, start_time), read
from /proc — a recycled PID has a different start time, so the tuple stops
matching and the kill is refused rather than aimed at a stranger.

Roles, not numbers: work is addressed as mission/workstream. The body behind a
role is swappable — that is what makes agent replacement safe.
"""
from __future__ import annotations

import os
import signal
import threading
import time
from dataclasses import dataclass, field

_lock = threading.Lock()
_procs: dict[str, "AgentProc"] = {}


def _start_time(pid: int) -> str:
    """Field 22 of /proc/<pid>/stat — the disambiguator for a recycled PID.

    Returns "" when unavailable (non-Linux, or the process is already gone),
    in which case identity checks fall back to the PID alone.
    """
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            data = f.read()
        return data[data.rindex(b")") + 2:].split()[19].decode()
    except Exception:
        return ""


@dataclass
class AgentProc:
    key: str                    # mission/workstream — the ROLE, not the body
    pid: int
    agent: str                  # which CLI is filling the role
    mission: str
    workstream: str
    start_time: str = ""
    started_at: float = field(default_factory=time.time)

    @property
    def age_s(self) -> float:
        return time.time() - self.started_at

    def is_same_process(self) -> bool:
        """Is the PID still the LIVE process we registered?

        A zombie keeps its /proc entry until the parent reaps it, so an
        existence check alone reports a killed agent as still running — and
        kill() would then wait out its grace period and escalate to SIGKILL
        against something already dead. State 'Z' is dead.
        """
        try:
            with open(f"/proc/{self.pid}/stat", "rb") as f:
                data = f.read()
            tail = data[data.rindex(b")") + 2:].split()
            if tail[0:1] == [b"Z"]:
                return False
            return not self.start_time or tail[19].decode() == self.start_time
        except (OSError, IndexError, ValueError):
            return False

    def alive(self) -> bool:
        return self.is_same_process()


def register(mission: str, workstream: str, agent: str, pid: int) -> AgentProc:
    p = AgentProc(key=f"{mission}/{workstream}", pid=pid, agent=agent,
                  mission=mission, workstream=workstream,
                  start_time=_start_time(pid))
    with _lock:
        _procs[p.key] = p       # a role has exactly one body at a time
    return p


def unregister(mission: str, workstream: str) -> None:
    with _lock:
        _procs.pop(f"{mission}/{workstream}", None)


def running() -> list[AgentProc]:
    """Live entries only; dead ones are reaped on the way past."""
    with _lock:
        items = list(_procs.items())
    live = []
    for key, p in items:
        if p.alive():
            live.append(p)
        else:
            with _lock:
                _procs.pop(key, None)
    return live


def kill(p: AgentProc, grace_s: float = 3.0) -> bool:
    """Stop one agent by verified identity, politely then firmly.

    Refuses when the tuple no longer matches — the PID has been recycled and
    signalling it would hit an unrelated process. Signals the process GROUP,
    since agents spawn children of their own; every session is a group leader
    (start_new_session=True), so this can never reach Aethelark's own group.
    """
    if not p.is_same_process():
        return False
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(p.pid, signal.SIGTERM)
        except OSError:
            return False
    deadline = time.time() + grace_s
    while time.time() < deadline:
        if not p.is_same_process():
            unregister(p.mission, p.workstream)
            return True
        time.sleep(0.1)
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(p.pid, signal.SIGKILL)
        except OSError:
            pass
    time.sleep(0.2)
    gone = not p.is_same_process()
    if gone:
        unregister(p.mission, p.workstream)
    return gone


def kill_all() -> dict:
    """The panic button. Returns what actually died, not what was attempted."""
    killed, failed = [], []
    for p in running():
        (killed if kill(p) else failed).append(p.key)
    return {"killed": killed, "failed": failed}


def snapshot() -> dict:
    return {"running": [
        {"role": p.key, "agent": p.agent, "pid": p.pid,
         "age_s": round(p.age_s, 1)} for p in running()]}


def describe() -> str:
    """Speakable answer to "what have you got running?"."""
    live = running()
    if not live:
        return "Nothing is running — no agents are alive."
    bits = [f"{p.workstream} on {p.agent} for {int(p.age_s // 60)} minutes"
            for p in live]
    return f"{len(live)} agent{'s' if len(live) != 1 else ''} running: " + "; ".join(bits)


def clear() -> None:
    """Test hook — module state would otherwise leak between cases."""
    with _lock:
        _procs.clear()
