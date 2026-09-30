"""The numbers this rewrite claims, produced by running them.

Everything here is measured on the machine it runs on, in one process, with
the same method before and after. Run it once on HEAD before any file
changes and once at the end; the two blocks go side by side in the
receipts.

The AFTER run has no second chance: by the time it runs, the BEFORE files
are gone, so there is no re-measuring a metric that broke. That means every
measurement below has to be independently guarded — an import that fails
because something got renamed, a subprocess that exits non-zero without
raising, a target file that moved rather than shrank — none of those may be
allowed to take out the measurements that still work. A metric that cannot
be taken prints N/A (<reason>) in its own place; everything else still runs
and the receipts block still gets written either way.
"""
from __future__ import annotations

import argparse
import importlib
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TARGETS = [
    "dashboard/server.py", "dashboard/static/app.html", "actions/web_search.py",
    "dashboard/static/login.html", "actions/system_monitor.py",
    "actions/proactive.py", "config/__init__.py", "setup.py",
    "core/llm_client.py",
]


def _bench(fn, n: int = 200) -> float:
    fn()
    t = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t) / n


def _line_counts(add) -> None:
    """Every TARGETS file plus the dynamic extras, summed into TOTAL.

    A file that used to exist and now does not is not "0 lines" — it might
    have moved. Printing 0 would fold silently into TOTAL and make a moved
    file look like it shrank to nothing; printing MISSING and leaving it out
    of TOTAL, loudly, is the only version of this number that cannot be
    misread as good news.
    """
    total = 0
    missing: list[str] = []
    try:
        for rel in TARGETS:
            p = ROOT / rel
            if p.exists():
                n = len(p.read_text(encoding="utf-8").splitlines())
                total += n
                add(f"  {rel:36} {n:5} lines")
            else:
                missing.append(rel)
                add(f"  {rel:36} {'MISSING':>5}")
        extras = (sorted(ROOT.glob("dashboard/netaccess/*.py"))
                  + sorted(ROOT.glob("dashboard/static/*.js"))
                  + sorted(ROOT.glob("dashboard/static/*.css"))
                  + [ROOT / "dashboard/crypto.py"])
        for extra in extras:
            if extra.exists() and extra.name != "crypto-js.min.js":
                n = len(extra.read_text(encoding="utf-8").splitlines())
                total += n
                add(f"  {extra.relative_to(ROOT).as_posix():36} {n:5} lines")
        add(f"  {'TOTAL':36} {total:5} lines")
        if missing:
            add(f"  WARNING: TOTAL excludes {len(missing)} missing file(s) below — a rename "
                "would look identical to this, so it is not proof those lines are gone: "
                + ", ".join(missing))
    except Exception as e:
        add(f"  {'TOTAL':36} N/A (line counts aborted: {type(e).__name__}: {e})")


def _config_get_os(add) -> None:
    try:
        import config
        importlib.reload(config)
        us = _bench(config.get_os, 2000) * 1e6
        add(f"  config.get_os()                      {us:8.2f} us/call")
    except Exception as e:
        add(f"  config.get_os()                      N/A ({type(e).__name__}: {e})")


def _system_monitor(add) -> None:
    try:
        import actions.system_monitor as sm
        importlib.reload(sm)
        before_threads = threading.active_count()
        sm.get_system_status()
        time.sleep(2.0)
        t0 = time.process_time()
        time.sleep(5.0)
        cpu = (time.process_time() - t0) / 5.0
        add(f"  system_monitor idle CPU              {cpu * 100:8.3f} % of one core")
        add(f"  threads it started                   {threading.active_count() - before_threads:8}")
    except Exception as e:
        reason = f"N/A ({type(e).__name__}: {e})"
        add(f"  system_monitor idle CPU              {reason}")
        add(f"  threads it started                   {reason}")


def _cold_import(add) -> None:
    try:
        t = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0,'.'); import dashboard.server"],
            cwd=ROOT, capture_output=True, text=True)
        elapsed_ms = (time.perf_counter() - t) * 1e3
        if proc.returncode == 0:
            add(f"  import dashboard.server              {elapsed_ms:8.1f} ms (cold subprocess)")
        else:
            stderr_lines = proc.stderr.strip().splitlines()
            tail = stderr_lines[-1] if stderr_lines else "no stderr"
            add(f"  import dashboard.server              N/A (subprocess exited "
                f"{proc.returncode}: {tail})")
    except Exception as e:
        add(f"  import dashboard.server              N/A ({type(e).__name__}: {e})")


def _dashboard_server_ctor(add) -> None:
    try:
        import asyncio
        import dashboard.server as ds

        async def _ctor():
            t0 = time.perf_counter()
            ds.DashboardServer()
            return (time.perf_counter() - t0) * 1e3

        add(f"  DashboardServer()                    {asyncio.run(_ctor()):8.1f} ms")
    except Exception as e:
        add(f"  DashboardServer()                    N/A ({type(e).__name__}: {e})")


def _peak_rss(add) -> None:
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        add(f"  peak RSS this process                {rss:8.1f} MB")
    except Exception as e:
        add(f"  peak RSS this process                N/A ({type(e).__name__}: {e})")


def measure() -> list[str]:
    lines: list[str] = []
    add = lines.append
    for step in (_line_counts, _config_get_os, _system_monitor,
                 _cold_import, _dashboard_server_ctor, _peak_rss):
        step(add)
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    args = ap.parse_args()
    out = [f"\n### {args.label}  ({time.strftime('%Y-%m-%d %H:%M:%S')})", "```"]
    try:
        out += measure()
    except Exception as e:
        # Every individual measurement above is already guarded; reaching
        # here means something outside all of them broke. Even then, the
        # receipts write below must still happen — a partial block is still
        # more evidence than a script that exited before writing anything.
        out.append(f"  MEASUREMENT ABORTED: {type(e).__name__}: {e}")
        print(f"[bench_rewrite] measure() raised despite per-metric guards: {e}",
              file=sys.stderr)
    out.append("```")
    text = "\n".join(out)
    print(text)
    receipts = ROOT / "docs/superpowers/specs/2026-08-21-receipts.md"
    receipts.parent.mkdir(parents=True, exist_ok=True)
    with receipts.open("a", encoding="utf-8") as fh:
        fh.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
