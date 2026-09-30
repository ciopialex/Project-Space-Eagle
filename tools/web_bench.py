"""Measure the web action path, so the next change to it is not a guess.

The brief that started this work said: do not assume the bottleneck is the
language model, instrument first, and report before/after numbers. The eagle's
own session logs already put the web path far ahead of anything in the voice
path — `open youtube.com` at 3040ms and a single click at 5177ms, against a
whole-turn target near 1000ms. This measures where those milliseconds go.

Runs entirely against local `file://` fixtures. That is not a convenience: it
removes network variance from a latency measurement, and it keeps the benchmark
away from the user's accounts and cookies entirely. A throwaway profile
directory is used for the same reason — the eagle's real profile carries
imported sessions and is never opened here.

Usage:
    python tools/web_bench.py            # all scenarios, 5 runs each
    python tools/web_bench.py --runs 10
"""
from __future__ import annotations

import argparse
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.grounding.web.browser import EagleBrowser  # noqa: E402

#: A page that is finished the moment it parses. Any wait beyond this is pure
#: overhead — this is the scenario a fixed settle delay taxes hardest.
STATIC = """<!doctype html><title>static</title>
<body><h1>Ready</h1><button>Go</button><input aria-label="Search"></body>"""

#: A single-page app: nothing useful in the DOM at `domcontentloaded`, real
#: content mounted a beat later. This is the scenario the settle delay exists
#: for, and the one an over-eager optimisation breaks. youtube.com read as
#: 6 controls when the collector ran too early.
SPA = """<!doctype html><title>spa</title><body><div id="root"></div>
<script>
setTimeout(function () {
  var r = document.getElementById('root');
  for (var i = 0; i < 40; i++) {
    var b = document.createElement('button');
    b.textContent = 'Item ' + i;
    r.appendChild(b);
  }
}, %d);
</script></body>"""


def _fixtures(tmp: Path) -> dict[str, str]:
    pages = {
        "static": STATIC,
        "spa_fast": SPA % 150,
        "spa_slow": SPA % 900,
    }
    urls = {}
    for name, html in pages.items():
        f = tmp / f"{name}.html"
        f.write_text(html)
        urls[name] = f.as_uri()
    return urls


def _stats(values: list[float]) -> str:
    values = sorted(values)
    return (f"median={statistics.median(values):7.0f}ms  "
            f"min={values[0]:7.0f}ms  max={values[-1]:7.0f}ms")


def run(runs: int = 5) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="aethelark-bench-"))
    profile = tmp / "profile"
    browser = EagleBrowser(headless=True, profile_dir=profile)
    urls = _fixtures(tmp)

    try:
        t0 = time.monotonic()
        browser.start()
        cold_ms = (time.monotonic() - t0) * 1000
        if not browser.running:
            print(f"browser failed to start: {browser.last_error}")
            return 1

        print(f"\ncold start            {cold_ms:7.0f}ms   "
              "(paid once per process, before any page loads)")

        results: dict[str, list[float]] = {}
        controls: dict[str, int] = {}
        for name, url in urls.items():
            timings = []
            for _ in range(runs):
                t = time.monotonic()
                browser.goto(url)
                timings.append((time.monotonic() - t) * 1000)
            results[name] = timings
            page = browser.page()
            controls[name] = len(page.collect()) if page else -1

        print(f"\nnavigation ({runs} runs each)")
        for name, timings in results.items():
            print(f"  {name:10} {_stats(timings)}   controls_seen={controls[name]}")

        page = browser.page()
        if page:
            snap = []
            for _ in range(runs):
                t = time.monotonic()
                page.collect()
                snap.append((time.monotonic() - t) * 1000)
            print(f"\nsnapshot (collect)   {_stats(snap)}")

        print("\nA control count of 40+ on the spa_* rows means the settle "
              "logic waited long enough.\nA count near 0 means it read the "
              "page before the app mounted — faster and useless.")
        return 0
    finally:
        browser.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    raise SystemExit(run(ap.parse_args().runs))
