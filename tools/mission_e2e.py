#!/usr/bin/env python3
"""Run a whole workflow against our own site, and judge it from the far side.

    python tools/mission_e2e.py

Why this exists. Every live test so far ran against someone else's website,
so every failure arrived with a question attached: the eagle, or Cloudflare,
or a layout change, or a slow CDN? Here we own the pages, so a failure is
ours. And no voice is involved — the goal goes in as text, which makes the
run repeatable and means nobody has to sit at a microphone.

The verdict comes from the SERVER's record of what it received, never from
the eagle's own report. That distinction is the whole history of this
codebase: tools that said "done" having done nothing. A harness that asks the
subject whether it succeeded measures nothing.

Exit code is 0 only when the server accepted a document containing every value
that was sitting on the Desktop and nowhere in the goal.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.testsite.server import EXPECTED, prepare, serve  # noqa: E402

PORT = 8971
BASE = f"http://127.0.0.1:{PORT}"

GOAL = ("download the registration form from the dashboard, fill it in with "
        "my details from the file on my Desktop, then upload and submit it")

#: The steps a person would take. Supplied here rather than planned, so this
#: measures EXECUTION. A second harness should let the model plan them, and
#: the gap between the two numbers is exactly how good the planner is.
STEPS = [
    f"Open {BASE}/",
    "Click the Download the registration form link",
    "Read the file my-details.txt on the Desktop",
    "Fill the downloaded form with those details and save it",
    "Click the Go to Submit page link",
    "Upload the filled form",
    "Click the Submit form button",
]


def _get(path: str) -> str:
    return urllib.request.urlopen(BASE + path, timeout=10).read().decode()


def _submissions() -> list[dict]:
    try:
        return json.loads(_get("/submissions"))
    except Exception:
        return []


def main() -> int:
    from actions.mission import mission
    from core import mission_store as store

    data = prepare()
    serve(PORT)
    _get("/reset")
    store.clear()

    print(f"\n  site      {BASE}")
    print(f"  data      {data}")
    print(f"  goal      {GOAL}\n" + "=" * 74)

    started = mission({"action": "start", "goal": GOAL, "steps": STEPS})
    if not started.ok and started.data.get("needs_confirmation"):
        # The mission includes a real commit ("Click the Submit form
        # button"), so it asked before running a single step — this rig
        # plays the human's "yes, go ahead" that a live session would relay,
        # since it is testing the eagle's OWN rig, submitting to a server it
        # owns. A real caller only sends confirm=True after actually asking.
        print(f"  confirm   {started.message}")
        started = mission({"action": "start", "goal": GOAL, "steps": STEPS,
                           "confirm": True})
    if not started.ok:
        print("  could not start:", started.message)
        return 1

    t0 = time.monotonic()
    for i in range(1, len(STEPS) + 3):
        t = time.monotonic()
        r = mission({"action": "next"})
        print(f"  {'✓' if r.ok else '✗'} {i}  ({(time.monotonic()-t)*1000:5.0f}ms)  "
              f"{r.message[:110]}")
        if not r.ok:
            print(f"       next: {r.guidance[:140]}")
            break
        if "mission done" in r.message.lower():
            break
    took = time.monotonic() - t0

    print("=" * 74)
    subs = _submissions()
    if not subs:
        print("  VERDICT: FAILED — the server received nothing at all.")
        print(f"  ({took:.1f}s)")
        return 1

    last = subs[-1]
    if last["accepted"]:
        print(f"  VERDICT: PASSED — the server accepted a document with all "
              f"{len(EXPECTED)} values.")
        print(f"  ({took:.1f}s, {last['bytes']} bytes)")
        return 0

    print(f"  VERDICT: FAILED — the server got a document but it was missing: "
          f"{', '.join(last['missing'])}")
    print(f"  what arrived:\n    " + last["text"][:300].replace("\n", "\n    "))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
