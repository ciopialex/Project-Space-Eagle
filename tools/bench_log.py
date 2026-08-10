#!/usr/bin/env python3
"""Read a session log, say what actually happened, and flag what went wrong.

    eagle 2>&1 | tee /tmp/run.log          # or AETHELARK_TRACE=1 eagle | tee ...
    python tools/bench_log.py /tmp/run.log

Written because eyeballing these logs is how things get missed. Every finding
below was a real defect that scrolled past unnoticed in a log the user had
already read: a bot wall reported as a working page, a tool refusing its own
input, three tools reporting no status at all.

It has no opinion about WHAT you said — it reports the shape of each turn and
flags patterns that are defects regardless of intent. Pair it with
`docs/Aethelark_Voice_Benchmark.md`, which fixes what you say so the expected
shape is known in advance.
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass, field

RE_START = re.compile(r"\[Tool\] ▶ (\S+) \(epoch=(\d+)\)\s*(\{.*\})?")
#: `✓`/`✗` read "[Tool] ✓ name (854ms)"; `?` reads "[Tool] ? name no status
#: reported (976ms)". The first draft matched only the former, so every
#: unmigrated tool — the exact thing this is meant to surface — was parsed as
#: a call that never finished and the NO STATUS finding never fired.
RE_DONE = re.compile(r"\[Tool\] ([✓✗?]) (\S+)(?: no status reported)? \((\d+)ms\)")
RE_PLAY = re.compile(r"play_q=(\d+)/\d+\(~(\d+)ms\)")
RE_MICQ = re.compile(r"mic_q=(\d+)/\d+.*?mic_drops=(\d+)")
RE_TRACE = re.compile(r"\[Trace\]\s*(.+)")
RE_INTENT = re.compile(r"\[Intent\] (\S+) \(conf=([\d.]+)")
RE_SESSION = re.compile(r"Connected\. \(session=(\w+)\)")
RE_INTERRUPT = re.compile(r"Interrupted \(epoch (\d+)→(\d+)\)")

CRASHES = ("core dumped", "Traceback (most recent call last)",
           "Failed to restore OpenGL context", "Aborted")
DROPS = ("connection closed by server", "connection dropped", "Reconnecting in")


@dataclass
class Call:
    tool: str
    epoch: str
    args: str = ""
    status: str = ""      # ✓ ok | ✗ failed | ? no status reported
    ms: int = 0
    detail: str = ""


@dataclass
class Report:
    calls: list[Call] = field(default_factory=list)
    sessions: list[str] = field(default_factory=list)
    play_ms: list[int] = field(default_factory=list)
    traces: list[str] = field(default_factory=list)
    intents: list[tuple] = field(default_factory=list)
    crashes: list[str] = field(default_factory=list)
    drops: int = 0
    interrupts: int = 0
    mic_drops: int = 0


def parse(text: str) -> Report:
    r = Report()
    pending: Call | None = None
    for line in text.splitlines():
        if m := RE_SESSION.search(line):
            r.sessions.append(m.group(1))
        if m := RE_START.search(line):
            pending = Call(tool=m.group(1), epoch=m.group(2),
                           args=(m.group(3) or "").strip())
            r.calls.append(pending)
        elif m := RE_DONE.search(line):
            status, tool, ms = m.group(1), m.group(2), int(m.group(3))
            target = pending if (pending and pending.tool == tool) else None
            if target is None:
                target = Call(tool=tool, epoch="?")
                r.calls.append(target)
            target.status, target.ms = status, ms
            pending = None
        elif r.calls and re.match(r"\s+(why|said|result|next):", line):
            r.calls[-1].detail += line.strip()[:180] + " "
        if m := RE_PLAY.search(line):
            if int(m.group(1)) > 0:
                r.play_ms.append(int(m.group(2)))
        if m := RE_MICQ.search(line):
            r.mic_drops = max(r.mic_drops, int(m.group(2)))
        if m := RE_TRACE.search(line):
            r.traces.append(m.group(1).strip())
        if m := RE_INTENT.search(line):
            r.intents.append((m.group(1), m.group(2)))
        if RE_INTERRUPT.search(line):
            r.interrupts += 1
        if any(c in line for c in CRASHES):
            r.crashes.append(line.strip()[:150])
        if any(d in line for d in DROPS):
            r.drops += 1
    return r


# ── the defects worth flagging, each one seen in a real log ────────────────

def findings(r: Report) -> list[str]:
    out = []

    no_status = [c for c in r.calls if c.status == "?"]
    if no_status:
        names = ", ".join(sorted({c.tool for c in no_status}))
        out.append(
            f"NO STATUS ({len(no_status)} calls): {names}. The model was given "
            f"no ok flag and had to guess from prose. These tools are not on "
            f"the ToolResult contract yet.")

    failed = [c for c in r.calls if c.status == "✗"]
    for c in failed:
        out.append(f"FAILED: {c.tool} after {c.ms}ms — {c.detail[:110]}")

    # A tool that fails in under ~50ms rejected its own input; it never tried.
    for c in r.calls:
        if c.status in ("✗", "?") and c.ms < 50 and "not" in c.detail.lower():
            out.append(
                f"REFUSED ITS OWN INPUT: {c.tool} gave up in {c.ms}ms — "
                f"the argument shape is wrong, not the request. {c.detail[:90]}")

    # web_agency open already returns the control list; an immediate look is a
    # whole extra model round-trip for identical bytes.
    for a, b in zip(r.calls, r.calls[1:]):
        if a.tool == b.tool == "web_agency" and "action=open" in a.args \
                and "action=look" in b.args:
            out.append("REDUNDANT ROUND-TRIP: web_agency look immediately after "
                       "open, which already returned the controls. One wasted "
                       "trip to the model and back.")

    # Same tool, same args, twice — the model did not believe the first answer.
    seen = Counter((c.tool, c.args) for c in r.calls if c.args)
    for (tool, args), n in seen.items():
        if n > 1:
            out.append(f"REPEATED CALL x{n}: {tool} {args[:70]} — identical "
                       f"args twice; the first answer was not trusted or not used.")

    if r.crashes:
        out.append(f"CRASH: {r.crashes[0]}")
    if r.drops:
        out.append(f"CONNECTION DROPPED {r.drops}x — session churn; each one "
                   f"costs a reconnect and can lose the turn.")
    if r.mic_drops:
        out.append(f"MIC DROPS: {r.mic_drops} frames lost — audio in was starved.")
    if not r.traces:
        out.append("NO [Trace] LINES — run with AETHELARK_TRACE=1, or the "
                   "voice latency cannot be split into model vs playback time.")
    return out


def main(path: str) -> int:
    text = open(path, encoding="utf-8", errors="replace").read()
    r = parse(text)

    print(f"\n{'='*66}\n  SESSION REPORT — {path}\n{'='*66}")
    print(f"  sessions      : {len(r.sessions)}  {r.sessions[:6]}")
    print(f"  tool calls    : {len(r.calls)}")
    print(f"  interrupts    : {r.interrupts}")
    if r.play_ms:
        print(f"  spoken audio  : {len(r.play_ms)} bursts, "
              f"longest {max(r.play_ms)}ms, median {sorted(r.play_ms)[len(r.play_ms)//2]}ms")
    if r.intents:
        print(f"  intent guesses: {r.intents[:4]}")

    print(f"\n  {'tool':22} {'epoch':>5} {'st':>3} {'ms':>7}")
    print(f"  {'-'*22} {'-'*5} {'-'*3} {'-'*7}")
    for c in r.calls:
        print(f"  {c.tool:22} {c.epoch:>5} {c.status or '-':>3} {c.ms:>7}")

    f = findings(r)
    print(f"\n{'-'*66}\n  FINDINGS ({len(f)})\n{'-'*66}")
    for i, line in enumerate(f, 1):
        print(f"  {i}. {line}")
    if not f:
        print("  nothing flagged.")
    print()
    return 1 if any(x.startswith(("CRASH", "FAILED")) for x in f) else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
