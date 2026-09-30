#!/usr/bin/env python3
"""Run a real mission end to end and print what every step actually did.

Not a unit test. The unit tests prove the RULES — that a failed rung is never
retried, that a step is not marked done unless it worked. This proves the
eagle finishes, which is the only claim that matters.

    python tools/mission_smoke.py
    python tools/mission_smoke.py "go to wikipedia and find what a motherboard is"
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.mission import mission            # noqa: E402
from core import mission_store as store        # noqa: E402

GOAL = " ".join(sys.argv[1:]) or \
    "go to makerworld.com and download a laptop stand"
MAX_STEPS = 25


def main() -> int:
    store.clear()                              # a clean run, not a resumed one
    print(f"\nGOAL: {GOAL}\n" + "=" * 70)

    t0 = time.monotonic()
    started = mission({"action": "start", "goal": GOAL})
    print(f"\nPLAN ({(time.monotonic()-t0)*1000:.0f}ms)  ok={started.ok}")
    print(f"  {started.message}\n")
    if not started.ok:
        print(f"  guidance: {started.guidance}")
        return 1

    print("=" * 70)
    for i in range(1, MAX_STEPS + 1):
        t = time.monotonic()
        r = mission({"action": "next"})
        ms = (time.monotonic() - t) * 1000
        mark = "✓" if r.ok else "✗"
        print(f"\n{mark} step {i}  ({ms:.0f}ms)")
        print(f"    {r.message[:300]}")
        if not r.ok:
            print(f"    next: {r.guidance[:220]}")
            break
        if "mission done" in r.message.lower():
            break

    print("\n" + "=" * 70)
    print(mission({"action": "status"}).message)
    print(f"total: {time.monotonic()-t0:.1f}s\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
