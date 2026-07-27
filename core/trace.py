"""Terminal trace for the mission pipeline.

The eagle's own terminal is where the operator watches a mission run, and until
now it showed connection lines and a queue-depth tick — nothing about what the
swarm was actually doing. When something stalled there was no way to tell which
stage it stalled in.

One line per stage transition, fixed-width tags so the whole run reads as a
column and greps cleanly:

    12:35:02  MISSION   ▸ goal heard: "a nail salon landing page with booking"
    12:35:02  ROUTE     ▸ conductor (swarm_mode plan)
    12:35:02  PROJECT   ▸ ~/Projects/nail-salon-booking  (derived)
    12:35:03  CHIEF     ▸ spawning claude_code as architect
    12:36:11  PLAN      ▸ 2 agents, coupled  [68.4s]
    12:36:11  GATE      ▸ awaiting human approval

Deliberately plain stdout: it must survive the UI being closed, and it is the
one surface that still works when the dashboard is the thing that broke.
"""
from __future__ import annotations

import time
from datetime import datetime

# Stage tags. Fixed width so the arrows line up into a readable column.
_W = 9

_STARTS: dict[str, float] = {}


def trace(stage: str, msg: str, *, timer: str | None = None,
          start: bool = False, ok: bool | None = None) -> None:
    """Emit one stage line.

    timer/start turn a pair of calls into a measured span — how long the chief
    took, how long a build ran — because "it felt slow" is not a bug report.
    `ok` marks a terminal outcome: ✔ or ✘.
    """
    if start and timer:
        _STARTS[timer] = time.monotonic()
    took = ""
    if timer and not start:
        t0 = _STARTS.pop(timer, None)
        if t0 is not None:
            took = f"  [{time.monotonic() - t0:.1f}s]"
    mark = "▸" if ok is None else ("✔" if ok else "✘")
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"{ts}  {stage.upper():<{_W}} {mark} {msg}{took}", flush=True)


def banner(title: str, lines: list[str] | None = None) -> None:
    """A block for the few moments that deserve to stop the eye — the plan
    awaiting approval, a mission finishing, a merge landing."""
    print("\n" + "─" * 72, flush=True)
    print(f"  {title}", flush=True)
    for ln in lines or []:
        print(f"    {ln}", flush=True)
    print("─" * 72 + "\n", flush=True)
